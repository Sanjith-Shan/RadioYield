// STDF V4 reader: byte stream -> Arrow-layout tables.
//
// Robustness contract: no input can make this code read out of bounds. Every
// record is bounded by its REC_LEN *and* by the end of the buffer; every field
// read goes through detail::Cursor. A record that runs past EOF ends parsing
// with stats.truncated = true; unknown record types are skipped by length.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <unordered_map>

#include "bytes.hpp"
#include "ry_stdf/stdf.hpp"

#if defined(__unix__) || defined(__APPLE__)
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#define RY_HAVE_MMAP 1
#endif

namespace ry::stdf {

const char* const kMeasurementColumns[18] = {
    "lot_id",  "station",   "build",    "data_origin", "site_num", "part_id",
    "part_ts", "retest",    "test_num", "test_name",   "units",    "lo_limit",
    "hi_limit", "result",   "test_pass", "part_pass",  "hard_bin", "soft_bin"};

namespace {
using detail::Cursor;

Column make_col(FT t) {
    switch (t) {
        case FT::U1:
        case FT::B1:
        case FT::N1: return Column::fixed("uint8", 1);
        case FT::U2: return Column::fixed("uint16", 2);
        case FT::U4: return Column::fixed("uint32", 4);
        case FT::I1: return Column::fixed("int8", 1);
        case FT::I2: return Column::fixed("int16", 2);
        case FT::I4: return Column::fixed("int32", 4);
        case FT::R4: return Column::fixed("float32", 4);
        case FT::R8: return Column::fixed("float64", 8);
        case FT::C1:
        case FT::Cn: return Column::string();
        case FT::Bn:
        case FT::Dn: return Column::binary();
        case FT::KU1:
        case FT::KN1: return Column::list(Column::fixed("uint8", 1));
        case FT::KU2: return Column::list(Column::fixed("uint16", 2));
        case FT::KR4: return Column::list(Column::fixed("float32", 4));
        case FT::KCn:
        case FT::Vn: return Column::list(Column::string());
    }
    return Column::fixed("uint8", 1);
}

void reserve_col(Column& c, size_t rows) {
    switch (c.kind) {
        case Column::FIXED: c.data.reserve(rows * size_t(c.width)); break;
        case Column::BOOL: c.data.reserve(rows / 8 + 1); break;
        case Column::STRING:
        case Column::BINARY:
            c.offsets.reserve(rows + 1);
            c.data.reserve(rows * 4);
            break;
        case Column::LIST: c.offsets.reserve(rows + 1); break;
    }
}

Table make_table(const RecordSpec& s, size_t expected_rows) {
    Table t;
    for (const auto& f : s.fields) {
        t.names.emplace_back(f.name);
        t.cols.push_back(make_col(f.type));
        reserve_col(t.cols.back(), expected_rows);
    }
    return t;
}

template <class T>
bool scalar(Cursor& c, Column& col, int64_t& count) {
    T v;
    if (c.get(v)) {
        col.push(v);
        count = int64_t(v);
        return true;
    }
    col.push_null();
    return false;
}

// Decode a GDR Vn field into printable "TYPE:value" strings. Returns false
// (and leaves `out` partial) if the data does not fit in the record.
bool decode_vn(Cursor& c, int64_t k, std::vector<std::string>& out) {
    char buf[64];
    for (int64_t j = 0; j < k; ++j) {
        uint8_t code;
        if (!c.get(code)) return false;
        switch (code) {
            case 0: continue;  // B*0 pad byte
            case 1: { uint8_t v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "U1:%u", unsigned(v)); break; }
            case 2: { uint16_t v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "U2:%u", unsigned(v)); break; }
            case 3: { uint32_t v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "U4:%lu", (unsigned long)v); break; }
            case 4: { int8_t v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "I1:%d", int(v)); break; }
            case 5: { int16_t v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "I2:%d", int(v)); break; }
            case 6: { int32_t v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "I4:%ld", (long)v); break; }
            case 7: { float v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "R4:%.9g", double(v)); break; }
            case 8: { double v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "R8:%.17g", v); break; }
            case 10: {
                const uint8_t* s; size_t n;
                if (!c.cn(s, n)) { c.kill(); return false; }
                out.emplace_back("Cn:" + std::string(reinterpret_cast<const char*>(s), n));
                continue;
            }
            case 11:
            case 12: {
                const uint8_t* s; size_t n;
                bool ok = code == 11 ? c.bn(s, n) : c.dn(s, n);
                if (!ok) { c.kill(); return false; }
                std::string h = code == 11 ? "Bn:" : "Dn:";
                static const char* hex = "0123456789abcdef";
                for (size_t i = 0; i < n; ++i) { h += hex[s[i] >> 4]; h += hex[s[i] & 15]; }
                out.push_back(std::move(h));
                continue;
            }
            case 13: { uint8_t v; if (!c.get(v)) return false; std::snprintf(buf, sizeof buf, "N1:%u", unsigned(v & 15)); break; }
            default: c.kill(); return false;  // unknown type code: rest is undecodable
        }
        out.emplace_back(buf);
    }
    return true;
}

// Generic spec-driven decode of one record body into one table row.
void decode_generic(const RecordSpec& spec, Table& t, Cursor c) {
    int64_t counts[64] = {0};
    const size_t nf = spec.fields.size();
    for (size_t i = 0; i < nf; ++i) {
        const FieldSpec& f = spec.fields[i];
        Column& col = t.cols[i];
        int64_t& cnt = counts[i];
        switch (f.type) {
            case FT::U1:
            case FT::B1:
            case FT::N1: scalar<uint8_t>(c, col, cnt); break;
            case FT::U2: scalar<uint16_t>(c, col, cnt); break;
            case FT::U4: scalar<uint32_t>(c, col, cnt); break;
            case FT::I1: scalar<int8_t>(c, col, cnt); break;
            case FT::I2: scalar<int16_t>(c, col, cnt); break;
            case FT::I4: scalar<int32_t>(c, col, cnt); break;
            case FT::R4: { float v; if (c.get(v)) col.push(v); else col.push_null(); break; }
            case FT::R8: { double v; if (c.get(v)) col.push(v); else col.push_null(); break; }
            case FT::C1:
                if (c.left() >= 1) { col.push_str(c.p, 1); ++c.p; }
                else col.push_null();
                break;
            case FT::Cn: { const uint8_t* s; size_t n; if (c.cn(s, n)) col.push_str(s, n); else col.push_null(); break; }
            case FT::Bn: { const uint8_t* s; size_t n; if (c.bn(s, n)) col.push_bytes(s, n); else col.push_null(); break; }
            case FT::Dn: { const uint8_t* s; size_t n; if (c.dn(s, n)) col.push_bytes(s, n); else col.push_null(); break; }
            case FT::KU1:
            case FT::KU2:
            case FT::KR4:
            case FT::KN1:
            case FT::KCn:
            case FT::Vn: {
                int64_t k = (f.count_field >= 0 && size_t(f.count_field) < i) ? counts[f.count_field] : 0;
                if (k < 0) k = 0;
                if (c.at_end()) { col.push_null(); break; }
                Column& ch = *col.child;
                if (f.type == FT::KCn) {
                    // pre-scan so a partial array never leaves orphan children
                    Cursor scan = c;
                    bool ok = true;
                    for (int64_t j = 0; j < k && ok; ++j) { const uint8_t* s; size_t n; ok = scan.cn(s, n); }
                    if (!ok) { c.kill(); col.push_null(); break; }
                    for (int64_t j = 0; j < k; ++j) { const uint8_t* s = nullptr; size_t n = 0; c.cn(s, n); ch.push_str(s, n); }
                    col.end_list();
                    break;
                }
                if (f.type == FT::Vn) {
                    std::vector<std::string> vals;
                    if (!decode_vn(c, k, vals)) { col.push_null(); break; }
                    for (auto& v : vals) ch.push_str(reinterpret_cast<const uint8_t*>(v.data()), v.size());
                    col.end_list();
                    break;
                }
                size_t need = f.type == FT::KU1 ? size_t(k)
                            : f.type == FT::KU2 ? size_t(k) * 2
                            : f.type == FT::KR4 ? size_t(k) * 4
                                                : (size_t(k) + 1) / 2;
                if (need > c.left()) { c.kill(); col.push_null(); break; }
                if (f.type == FT::KU1) {
                    for (int64_t j = 0; j < k; ++j) ch.push(c.p[j]);
                } else if (f.type == FT::KU2) {
                    for (int64_t j = 0; j < k; ++j) ch.push(detail::load<uint16_t>(c.p + 2 * j, c.swap));
                } else if (f.type == FT::KR4) {
                    for (int64_t j = 0; j < k; ++j) ch.push(detail::load<float>(c.p + 4 * j, c.swap));
                } else {  // N1 nibbles, low nibble first
                    for (int64_t j = 0; j < k; ++j) {
                        uint8_t b = c.p[j / 2];
                        ch.push(uint8_t((j & 1) ? (b >> 4) : (b & 15)));
                    }
                }
                c.p += need;
                col.end_list();
                break;
            }
        }
    }
    ++t.rows;
}

// Strings live in one pool; rows refer to them by index (-1 = null).
struct Pool {
    std::vector<std::string> s;
    int32_t add(const uint8_t* p, size_t n) {
        s.emplace_back(reinterpret_cast<const char*>(p), n);
        return int32_t(s.size() - 1);
    }
};

struct TestState {
    bool has_defaults = false;
    int32_t def_name = -1, def_units = -1;
    double def_lo = 0, def_hi = 0;
    bool def_lo_ok = false, def_hi_ok = false;
    int32_t last_name = -1, last_units = -1;  // cache of the last explicit string
};

struct Part {
    int32_t id = -1;
    double ts = 0;
    uint8_t ts_state = 0;  // 0 none (use MIR.START_T), 1 value, 2 explicit null
    bool closed = false;
    bool retest = false, pass = false;
    uint16_t hbin = 0, sbin = 0;
};

struct Row {
    double lo, hi;
    float result;
    int32_t part;
    uint32_t test_num;
    int32_t name, units;
    uint8_t site;
    uint8_t flags;  // 1 lo ok, 2 hi ok, 4 result ok, 8 pass
};

// Latin-1 -> UTF-8 for anything that becomes a Python str (meta keys/values,
// warning messages). Column strings use Column::push_str, which does the same.
std::string utf8(const uint8_t* p, size_t n) {
    std::string out;
    out.reserve(n);
    for (size_t i = 0; i < n; ++i) {
        uint8_t b = p[i];
        if (b < 0x80) {
            out.push_back(char(b));
        } else {
            out.push_back(char(0xC0 | (b >> 6)));
            out.push_back(char(0x80 | (b & 0x3F)));
        }
    }
    return out;
}
std::string utf8(const std::string& s) { return utf8(reinterpret_cast<const uint8_t*>(s.data()), s.size()); }

bool same_str(const Pool& pool, int32_t idx, const uint8_t* s, size_t n) {
    if (idx < 0) return false;
    const std::string& x = pool.s[size_t(idx)];
    return x.size() == n && (n == 0 || std::memcmp(x.data(), s, n) == 0);
}

void push_repeated(Column& col, const std::string* s, int64_t n) {
    if (!s) {
        for (int64_t i = 0; i < n; ++i) col.push_null();
        return;
    }
    for (int64_t i = 0; i < n; ++i) col.push_str(reinterpret_cast<const uint8_t*>(s->data()), s->size());
}

class Parser {
   public:
    Parser(const uint8_t* d, size_t n, const ParseOptions& o) : data_(d), len_(n), opt_(o) {}

    ParseResult run();

   private:
    const uint8_t* data_;
    size_t len_;
    const ParseOptions& opt_;
    ParseResult r_;
    bool swap_ = false;

    std::vector<int64_t> spec_counts_;
    std::vector<std::unique_ptr<Table>> tables_;
    std::vector<bool> bulk_;

    Pool pool_;
    std::vector<Part> parts_;
    std::vector<int32_t> open_part_ = std::vector<int32_t>(65536, -1);  // (head<<8|site)
    int32_t last_opened_ = -1;
    bool seen_pir_ = false;
    bool have_mir_ = false;
    bool have_start_t_ = false;
    uint32_t start_t_ = 0;
    std::vector<Row> rows_;
    std::unordered_map<uint32_t, uint32_t> test_idx_;
    std::vector<TestState> tests_;
    bool have_last_test_ = false;
    uint32_t last_test_num_ = 0;
    uint32_t last_test_slot_ = 0;

    void warn(const char* event, const std::string& msg, bool is_warning = true) {
        ++r_.stats.events[event];
        if (!is_warning) return;
        ++r_.stats.warnings;
        if (r_.stats.warning_messages.size() < opt_.max_warning_messages)
            r_.stats.warning_messages.push_back(msg);  // callers pass UTF-8 only
    }
    void on_mir(Cursor c);
    void on_pir(Cursor c);
    void on_prr(Cursor c);
    void on_ptr(Cursor c, size_t off);
    void on_dtr(Cursor c);
    void set_meta(const std::string& k, MetaValue v);
    const std::string* meta_str(const char* k) const;
    void build_measurements();
};

void Parser::set_meta(const std::string& k, MetaValue v) {
    for (auto& kv : r_.meta)
        if (kv.first == k) { kv.second = std::move(v); return; }
    r_.meta.emplace_back(k, std::move(v));
}

const std::string* Parser::meta_str(const char* k) const {
    for (auto& kv : r_.meta)
        if (kv.first == k && !kv.second.is_int) return &kv.second.s;
    return nullptr;
}

void Parser::on_mir(Cursor c) {
    if (have_mir_) { warn("extra_mir", "additional MIR ignored for meta"); return; }
    have_mir_ = true;
    const RecordSpec* spec = find_spec(1, 10);
    for (const auto& f : spec->fields) {
        MetaValue v;
        bool ok = false;
        switch (f.type) {
            case FT::U1: { uint8_t x = 0; ok = c.get(x); v.is_int = true; v.i = x; break; }
            case FT::U2: { uint16_t x = 0; ok = c.get(x); v.is_int = true; v.i = x; break; }
            case FT::U4: { uint32_t x = 0; ok = c.get(x); v.is_int = true; v.i = x; break; }
            case FT::C1:
                if (c.left() >= 1) { v.s.assign(1, char(*c.p)); ++c.p; ok = true; }
                break;
            case FT::Cn: { const uint8_t* s; size_t n; ok = c.cn(s, n); if (ok) v.s.assign(reinterpret_cast<const char*>(s), n); break; }
            default: break;
        }
        if (!ok) break;
        if (!v.is_int) v.s = utf8(v.s);  // keep meta strings valid UTF-8 like the columns
        if (std::strcmp(f.name, "START_T") == 0) { have_start_t_ = true; start_t_ = uint32_t(v.i); }
        set_meta(f.name, v);
    }
    if (c.overrun) warn("field_overrun", "MIR field runs past end of record");
}

void Parser::on_pir(Cursor c) {
    uint8_t head = 1, site = 1;
    c.get(head);
    c.get(site);
    seen_pir_ = true;
    size_t key = (size_t(head) << 8) | site;
    if (open_part_[key] >= 0) {
        warn("pir_without_prr", "PIR for head " + std::to_string(head) + " site " + std::to_string(site) +
                                    " while a part was still open; previous part left unclosed");
    }
    parts_.emplace_back();
    open_part_[key] = int32_t(parts_.size() - 1);
    last_opened_ = open_part_[key];
}

void Parser::on_prr(Cursor c) {
    uint8_t head = 1, site = 1, flg = 0;
    uint16_t num_test = 0, hbin = 65535, sbin = 65535;
    int16_t x = 0, y = 0;
    uint32_t tt = 0;
    c.get(head);
    c.get(site);
    bool has_flg = c.get(flg);
    c.get(num_test);
    c.get(hbin);
    c.get(sbin);
    c.get(x);
    c.get(y);
    c.get(tt);
    const uint8_t* id = nullptr;
    size_t idn = 0;
    bool has_id = c.cn(id, idn);
    if (c.overrun) warn("field_overrun", "PRR field runs past end of record");
    size_t key = (size_t(head) << 8) | site;
    int32_t pi = open_part_[key];
    if (pi < 0) {
        warn("prr_without_pir", "PRR without matching PIR (head " + std::to_string(head) + " site " +
                                    std::to_string(site) + ")");
        return;
    }
    open_part_[key] = -1;
    Part& p = parts_[size_t(pi)];
    p.closed = true;
    p.retest = has_flg && (flg & 0x03);
    p.pass = !(has_flg && (flg & 0x08));
    p.hbin = hbin;
    p.sbin = sbin;
    p.id = (has_id && idn > 0) ? pool_.add(id, idn) : -1;
}

void Parser::on_dtr(Cursor c) {
    const uint8_t* s;
    size_t n;
    if (!c.cn(s, n)) return;
    if (n < 4 || std::memcmp(s, "ry.", 3) != 0) return;
    const uint8_t* eq = static_cast<const uint8_t*>(std::memchr(s, '=', n));
    if (!eq) return;
    std::string key = utf8(s + 3, size_t(eq - s - 3));
    std::string val = utf8(eq + 1, size_t(s + n - eq - 1));
    if (key == "part_ts") {
        if (last_opened_ < 0 || parts_[size_t(last_opened_)].closed) {
            warn("orphan_part_ts", "ry.part_ts DTR outside an open part ignored");
            return;
        }
        Part& p = parts_[size_t(last_opened_)];
        if (val.empty()) { p.ts_state = 2; return; }
        char* end = nullptr;
        double v = std::strtod(val.c_str(), &end);
        if (end == val.c_str() || *end != '\0') {
            warn("bad_part_ts", "unparseable ry.part_ts value (" + std::to_string(val.size()) + " bytes)");
            p.ts_state = 2;
            return;
        }
        p.ts = v;
        p.ts_state = 1;
        return;
    }
    if (seen_pir_) {
        warn("ry_dtr_after_first_pir", "ry." + key + " DTR after first PIR not added to meta", false);
        return;
    }
    if (key == "lot_id" || key == "station" || key.empty()) {
        warn("reserved_ry_key", "ry." + key + " DTR ignored (reserved or empty key)");
        return;
    }
    MetaValue mv;
    mv.s = std::move(val);  // already UTF-8
    set_meta(key, mv);
}

void Parser::on_ptr(Cursor c, size_t off) {
    uint32_t test_num;
    uint8_t head = 1, site = 1, test_flg = 0, parm_flg = 0, opt = 0;
    float result = 0, lo = 0, hi = 0;
    int8_t scal;
    const uint8_t *txt = nullptr, *units = nullptr, *alarm;
    size_t txt_n = 0, units_n = 0, alarm_n;
    if (!c.get(test_num)) {
        warn("ptr_missing_test_num", "PTR at offset " + std::to_string(off) + " has no TEST_NUM; skipped");
        return;
    }
    c.get(head);
    c.get(site);
    bool has_flg = c.get(test_flg);
    c.get(parm_flg);
    bool has_result = c.get(result);
    bool has_txt = c.cn(txt, txt_n);
    c.cn(alarm, alarm_n);
    bool has_opt = c.get(opt);
    c.get(scal);
    c.get(scal);
    c.get(scal);
    bool has_lo = c.get(lo);
    bool has_hi = c.get(hi);
    bool has_units = c.cn(units, units_n);
    if (c.overrun) warn("field_overrun", "PTR at offset " + std::to_string(off) + " has a field running past REC_LEN");

    // per-test default state (first PTR for a TEST_NUM sets limits/units/name)
    uint32_t slot;
    if (have_last_test_ && test_num == last_test_num_) {
        slot = last_test_slot_;
    } else {
        auto it = test_idx_.find(test_num);
        if (it == test_idx_.end()) {
            slot = uint32_t(tests_.size());
            tests_.emplace_back();
            test_idx_.emplace(test_num, slot);
        } else {
            slot = it->second;
        }
        have_last_test_ = true;
        last_test_num_ = test_num;
        last_test_slot_ = slot;
    }
    TestState& ts = tests_[slot];
    const bool first = !ts.has_defaults;

    int32_t name = -1;
    if (has_txt && txt_n > 0) {
        if (same_str(pool_, ts.last_name, txt, txt_n)) name = ts.last_name;
        else name = ts.last_name = pool_.add(txt, txt_n);
    } else {
        name = ts.def_name;
    }
    int32_t uidx = -1;
    if (has_units && units_n > 0) {
        if (same_str(pool_, ts.last_units, units, units_n)) uidx = ts.last_units;
        else uidx = ts.last_units = pool_.add(units, units_n);
    } else {
        uidx = ts.def_units;
    }
    double vlo = ts.def_lo, vhi = ts.def_hi;
    bool lo_ok = ts.def_lo_ok, hi_ok = ts.def_hi_ok;
    if (has_opt) {
        if (opt & 0x40) lo_ok = false;
        else if (!(opt & 0x10) && has_lo) { vlo = lo; lo_ok = true; }
        if (opt & 0x80) hi_ok = false;
        else if (!(opt & 0x20) && has_hi) { vhi = hi; hi_ok = true; }
    }
    if (first) {
        ts.has_defaults = true;
        ts.def_lo = vlo; ts.def_lo_ok = lo_ok;
        ts.def_hi = vhi; ts.def_hi_ok = hi_ok;
    }
    if (ts.def_name < 0 && name >= 0) ts.def_name = name;
    if (ts.def_units < 0 && uidx >= 0) ts.def_units = uidx;

    size_t key = (size_t(head) << 8) | site;
    int32_t pi = open_part_[key];
    if (pi < 0) {
        warn("ptr_outside_part", "PTR outside PIR/PRR (defaults only)", false);
        return;
    }
    Row row;
    row.lo = vlo;
    row.hi = vhi;
    row.result = result;
    row.part = pi;
    row.test_num = test_num;
    row.name = name;
    row.units = uidx;
    row.site = site;
    row.flags = uint8_t((lo_ok ? 1 : 0) | (hi_ok ? 2 : 0) |
                        ((has_result && !(has_flg && (test_flg & 0x02))) ? 4 : 0) |
                        (((test_flg & 0xC0) == 0) ? 8 : 0));
    rows_.push_back(row);
}

void Parser::build_measurements() {
    Table& t = r_.measurements;
    const int64_t n = int64_t(rows_.size());
    for (const char* c : kMeasurementColumns) t.names.emplace_back(c);
    t.rows = n;
    std::string unknown = "UNKNOWN";
    const std::string* lot = meta_str("LOT_ID");
    const std::string* station = meta_str("NODE_NAM");
    const std::string* build = meta_str("build");
    const std::string* origin = meta_str("data_origin");
    if (!origin) origin = &unknown;

    auto s_col = [&](const std::string* s) {
        Column c = Column::string();
        if (s) {
            c.data.reserve(s->size() * size_t(n));
            c.offsets.reserve(size_t(n) + 1);
        }
        push_repeated(c, s, n);
        return c;
    };
    t.cols.push_back(s_col(lot));
    t.cols.push_back(s_col(station));
    t.cols.push_back(s_col(build));
    t.cols.push_back(s_col(origin));

    Column site = Column::fixed("uint8", 1), part_id = Column::string(), part_ts = Column::fixed("float64", 8),
           retest = Column::boolean(), test_num = Column::fixed("uint32", 4), test_name = Column::string(),
           units = Column::string(), lo = Column::fixed("float64", 8), hi = Column::fixed("float64", 8),
           result = Column::fixed("float64", 8), test_pass = Column::boolean(), part_pass = Column::boolean(),
           hbin = Column::fixed("uint16", 2), sbin = Column::fixed("uint16", 2);
    for (Column* c : {&site, &part_ts, &test_num, &lo, &hi, &result, &hbin, &sbin}) {
        c->data.reserve(size_t(n) * size_t(c->width));
    }
    for (Column* c : {&part_id, &test_name, &units}) c->offsets.reserve(size_t(n) + 1);

    auto push_pool = [&](Column& col, int32_t idx) {
        if (idx < 0) { col.push_null(); return; }
        const std::string& s = pool_.s[size_t(idx)];
        col.push_str(reinterpret_cast<const uint8_t*>(s.data()), s.size());
    };
    for (const Row& row : rows_) {
        const Part& p = parts_[size_t(row.part)];
        site.push(row.site);
        if (p.closed) push_pool(part_id, p.id); else part_id.push_null();
        if (p.ts_state == 1) part_ts.push(p.ts);
        else if (p.ts_state == 0 && have_start_t_) part_ts.push(double(start_t_));
        else part_ts.push_null();
        if (p.closed) retest.push_bool(p.retest); else retest.push_null();
        test_num.push(row.test_num);
        push_pool(test_name, row.name);
        push_pool(units, row.units);
        if (row.flags & 1) lo.push(row.lo); else lo.push_null();
        if (row.flags & 2) hi.push(row.hi); else hi.push_null();
        if (row.flags & 4) result.push(double(row.result)); else result.push_null();
        test_pass.push_bool(row.flags & 8);
        if (p.closed) part_pass.push_bool(p.pass); else part_pass.push_null();
        if (p.closed && p.hbin != 65535) hbin.push(p.hbin); else hbin.push_null();
        if (p.closed && p.sbin != 65535) sbin.push(p.sbin); else sbin.push_null();
    }
    for (Column* c : {&site, &part_id, &part_ts, &retest, &test_num, &test_name, &units, &lo, &hi, &result,
                      &test_pass, &part_pass, &hbin, &sbin})
        t.cols.push_back(std::move(*c));
    for (auto& c : t.cols) c.finish();
}

ParseResult Parser::run() {
    Stats& st = r_.stats;
    st.bytes = int64_t(len_);
    if (len_ == 0) throw Error("empty input: not an STDF file");
    if (len_ < 4 || data_[2] != 0 || data_[3] != 10)
        throw Error("not an STDF V4 file: first record is not a FAR (0,10)");
    if (len_ < 6) throw Error("truncated FAR: file too short to be STDF");
    const uint8_t cpu = data_[4];
    st.cpu_type = cpu;
    st.stdf_ver = data_[5];
    bool big;
    if (cpu == 1) big = true;
    else if (cpu == 2) big = false;
    else {
        big = (data_[0] == 0 && data_[1] != 0);  // infer from FAR REC_LEN layout
        warn("unsupported_cpu_type", "FAR.CPU_TYPE=" + std::to_string(cpu) +
                                         " (not 1/2); byte order inferred from FAR.REC_LEN, IEEE floats assumed");
    }
    if (data_[5] != 4) warn("stdf_ver_not_4", "FAR.STDF_VER=" + std::to_string(data_[5]) + "; decoding as V4 anyway");
    st.byte_order = big ? '>' : '<';
    swap_ = big == detail::host_is_little();

    const auto& specs = record_specs();
    spec_counts_.assign(specs.size(), 0);
    tables_.resize(specs.size());
    bulk_.assign(specs.size(), false);
    for (size_t i = 0; i < specs.size(); ++i) {
        std::string nm = specs[i].name;
        bulk_[i] = nm == "PTR" || nm == "MPR" || nm == "FTR" || nm == "PIR" || nm == "PRR" || nm == "DTR" ||
                   nm == "GDR";
    }

    // Pre-scan record headers (cheap) so column buffers are sized once instead
    // of growing by repeated reallocation. Capped by what the bytes can hold.
    std::vector<size_t> expected(specs.size(), 0);
    size_t ptr_expected = 0;
    for (size_t q = 0; q + 4 <= len_;) {
        const uint16_t rl = detail::load<uint16_t>(data_ + q, swap_);
        if (const RecordSpec* sp = find_spec(data_[q + 2], data_[q + 3])) {
            ++expected[size_t(sp - specs.data())];
            if (sp->typ == 15 && sp->sub == 10) ++ptr_expected;
        }
        q += 4 + size_t(rl);
    }
    rows_.reserve(ptr_expected);

    size_t pos = 0;
    while (pos < len_) {
        if (len_ - pos < 4) {
            warn("truncated_header", "file ends inside a record header at offset " + std::to_string(pos));
            st.truncated = true;
            break;
        }
        const uint16_t rl = detail::load<uint16_t>(data_ + pos, swap_);
        const uint8_t typ = data_[pos + 2], sub = data_[pos + 3];
        const size_t body = pos + 4;
        if (size_t(rl) > len_ - body) {
            warn("truncated_record", "record (" + std::to_string(typ) + "," + std::to_string(sub) + ") at offset " +
                                         std::to_string(pos) + " declares " + std::to_string(rl) +
                                         " bytes but only " + std::to_string(len_ - body) + " remain");
            st.truncated = true;
            break;
        }
        ++st.records;
        Cursor c{data_ + body, data_ + body + rl, swap_};
        const RecordSpec* spec = find_spec(typ, sub);
        if (!spec) {
            ++st.unknown;
            ++st.unknown_types[std::to_string(typ) + "." + std::to_string(sub)];
        } else {
            const size_t si = size_t(spec - specs.data());
            ++spec_counts_[si];
            if (opt_.records || !bulk_[si]) {
                if (!tables_[si]) tables_[si] = std::make_unique<Table>(make_table(*spec, expected[si]));
                decode_generic(*spec, *tables_[si], c);
            }
            switch ((typ << 8) | sub) {
                case (0 << 8) | 10:
                    if (pos != 0) warn("extra_far", "FAR at offset " + std::to_string(pos) + " ignored");
                    break;
                case (1 << 8) | 10: on_mir(c); break;
                case (5 << 8) | 10: on_pir(c); break;
                case (5 << 8) | 20: on_prr(c); break;
                case (15 << 8) | 10: on_ptr(c, pos); break;
                case (50 << 8) | 30: on_dtr(c); break;
                default: break;
            }
        }
        pos = body + rl;
        st.bytes_parsed = int64_t(pos);
    }

    for (size_t i = 0; i < parts_.size(); ++i)
        if (!parts_[i].closed) {
            warn("unclosed_part", "part opened by PIR never closed by PRR; its rows have null bins/part fields");
        }

    if (!have_mir_) warn("missing_mir", "no MIR record; lot_id/station are null");
    // convenience aliases for the data contract
    if (const std::string* lot = meta_str("LOT_ID")) { MetaValue v; v.s = *lot; set_meta("lot_id", v); }
    if (const std::string* stn = meta_str("NODE_NAM")) { MetaValue v; v.s = *stn; set_meta("station", v); }
    if (!meta_str("data_origin")) { MetaValue v; v.s = "UNKNOWN"; set_meta("data_origin", v); }

    build_measurements();
    for (size_t i = 0; i < specs.size(); ++i) {
        if (spec_counts_[i]) st.counts[specs[i].name] = spec_counts_[i];
        if (tables_[i]) {
            for (auto& c : tables_[i]->cols) c.finish();
            r_.records.emplace(specs[i].name, std::move(*tables_[i]));
        }
    }
    return std::move(r_);
}

}  // namespace

ParseResult parse_buffer(const uint8_t* data, size_t len, const ParseOptions& opt) {
    Parser p(data, len, opt);
    return p.run();
}

ParseResult parse_file(const std::string& path, const ParseOptions& opt) {
#ifdef RY_HAVE_MMAP
    int fd = ::open(path.c_str(), O_RDONLY);
    if (fd < 0) throw Error("cannot open '" + path + "'");
    struct stat sb;
    if (::fstat(fd, &sb) != 0) {
        ::close(fd);
        throw Error("cannot stat '" + path + "'");
    }
    size_t n = size_t(sb.st_size);
    if (n == 0) {
        ::close(fd);
        throw Error("empty input: not an STDF file");
    }
    void* m = ::mmap(nullptr, n, PROT_READ, MAP_PRIVATE, fd, 0);
    ::close(fd);
    if (m == MAP_FAILED) throw Error("cannot mmap '" + path + "'");
    struct Unmap {
        void* m; size_t n;
        ~Unmap() { ::munmap(m, n); }
    } guard{m, n};
    return parse_buffer(static_cast<const uint8_t*>(m), n, opt);
#else
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) throw Error("cannot open '" + path + "'");
    std::vector<uint8_t> buf;
    uint8_t chunk[1 << 16];
    size_t k;
    while ((k = std::fread(chunk, 1, sizeof chunk, f)) > 0) buf.insert(buf.end(), chunk, chunk + k);
    std::fclose(f);
    return parse_buffer(buf.data(), buf.size(), opt);
#endif
}

}  // namespace ry::stdf
