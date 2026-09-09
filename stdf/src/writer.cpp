// STDF V4 writer for the RadioYield measurement table.
//
// Stream layout: FAR, MIR, ry.* DTRs, then per part PIR / DTR ry.part_ts /
// PTR... / PRR, then HBR, SBR, PCR, TSR summaries and MRR.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>
#include <map>
#include <string>
#include <unordered_map>

#include "bytes.hpp"
#include "ry_stdf/stdf.hpp"

namespace ry::stdf {
namespace {

class Out {
   public:
    Out(bool big, FILE* f) : swap_(big == detail::host_is_little()), f_(f) {}
    std::vector<uint8_t> buf;
    int64_t records = 0, bytes = 0;

    void begin(uint8_t typ, uint8_t sub) {
        rec_.clear();
        typ_ = typ;
        sub_ = sub;
    }
    template <class T>
    void put(T v) {
        size_t o = rec_.size();
        rec_.resize(o + sizeof(T));
        detail::store<T>(rec_.data() + o, v, swap_);
    }
    void u1(uint8_t v) { put(v); }
    void u2(uint16_t v) { put(v); }
    void u4(uint32_t v) { put(v); }
    void i1(int8_t v) { put(v); }
    void i2(int16_t v) { put(v); }
    void r4(float v) { put(v); }
    void c1(char c) { rec_.push_back(uint8_t(c)); }
    void cn(const uint8_t* s, size_t n, const char* what) {
        if (n > 255) throw Error(std::string(what) + " longer than 255 bytes cannot be stored in an STDF Cn field");
        rec_.push_back(uint8_t(n));
        rec_.insert(rec_.end(), s, s + n);
    }
    void cn(const std::string& s, const char* what) { cn(reinterpret_cast<const uint8_t*>(s.data()), s.size(), what); }
    void end() {
        if (rec_.size() > 65535) throw Error("STDF record body exceeds 65535 bytes");
        uint8_t h[4];
        detail::store<uint16_t>(h, uint16_t(rec_.size()), swap_);
        h[2] = typ_;
        h[3] = sub_;
        buf.insert(buf.end(), h, h + 4);
        buf.insert(buf.end(), rec_.begin(), rec_.end());
        ++records;
        if (f_ && buf.size() >= (1u << 22)) flush();
    }
    void flush() {
        if (!f_) return;
        if (!buf.empty() && std::fwrite(buf.data(), 1, buf.size(), f_) != buf.size())
            throw Error("short write to STDF output file");
        bytes += int64_t(buf.size());
        buf.clear();
    }

   private:
    bool swap_;
    FILE* f_;
    std::vector<uint8_t> rec_;
    uint8_t typ_ = 0, sub_ = 0;
};

struct PartKey {
    const WriteInput* in;
    bool same(size_t a, size_t b) const {
        auto s_eq = [&](const StrCol& c) {
            bool va = c.ok(a), vb = c.ok(b);
            if (va != vb) return false;
            if (!va) return true;
            size_t la = c.len(a);
            return la == c.len(b) && (la == 0 || std::memcmp(c.ptr(a), c.ptr(b), la) == 0);
        };
        auto n_eq = [&](const auto& c) {
            bool va = c.ok(a), vb = c.ok(b);
            if (va != vb) return false;
            return !va || std::memcmp(&c.v[a], &c.v[b], sizeof(c.v[a])) == 0;
        };
        return s_eq(in->part_id) && n_eq(in->site_num) && n_eq(in->part_ts) && n_eq(in->retest) &&
               n_eq(in->hard_bin) && n_eq(in->soft_bin) && n_eq(in->part_pass);
    }
    std::string bytes(size_t i) const {
        std::string k;
        auto add_s = [&](const StrCol& c) {
            k.push_back(c.ok(i) ? 1 : 0);
            if (c.ok(i)) {
                uint32_t n = uint32_t(c.len(i));
                k.append(reinterpret_cast<const char*>(&n), 4);
                k.append(reinterpret_cast<const char*>(c.ptr(i)), n);
            }
        };
        auto add_n = [&](const auto& c) {
            k.push_back(c.ok(i) ? 1 : 0);
            if (c.ok(i)) k.append(reinterpret_cast<const char*>(&c.v[i]), sizeof(c.v[i]));
        };
        add_s(in->part_id);
        add_n(in->site_num);
        add_n(in->part_ts);
        add_n(in->retest);
        add_n(in->hard_bin);
        add_n(in->soft_bin);
        add_n(in->part_pass);
        return k;
    }
};

struct TestDef {
    bool seen = false;
    int64_t name_row = -1, units_row = -1;  // row holding the default string
    bool lo_ok = false, hi_ok = false;
    float lo = 0, hi = 0;
    // TSR accumulators
    uint32_t exec = 0, fail = 0;
    bool any = false;
    float mn = 0, mx = 0;
    double sum = 0, sq = 0;
};

bool str_eq(const StrCol& c, size_t a, int64_t b) {
    if (b < 0) return false;
    size_t la = c.len(a);
    return la == c.len(size_t(b)) && (la == 0 || std::memcmp(c.ptr(a), c.ptr(size_t(b)), la) == 0);
}

void write_mir(Out& o, const WriteMeta& meta, uint32_t start_t) {
    std::map<std::string, const MetaValue*> m;
    for (auto& kv : meta.mir) m[kv.first] = &kv.second;
    const RecordSpec* spec = find_spec(1, 10);
    for (auto& kv : m) {
        bool known = false;
        for (auto& f : spec->fields) known |= kv.first == f.name;
        if (!known) throw Error("unknown MIR field '" + kv.first + "'");
    }
    o.begin(1, 10);
    for (const auto& f : spec->fields) {
        auto it = m.find(f.name);
        const MetaValue* v = it == m.end() ? nullptr : it->second;
        auto need_int = [&](int64_t lo, int64_t hi, int64_t dflt) -> int64_t {
            if (!v) return dflt;
            if (!v->is_int) throw Error(std::string("MIR.") + f.name + " must be an integer");
            if (v->i < lo || v->i > hi) throw Error(std::string("MIR.") + f.name + " out of range");
            return v->i;
        };
        auto need_str = [&](const char* dflt) -> std::string {
            if (!v) return dflt;
            if (v->is_int) throw Error(std::string("MIR.") + f.name + " must be a string");
            return v->s;
        };
        std::string nm = f.name;
        switch (f.type) {
            case FT::U1: o.u1(uint8_t(need_int(0, 255, 1))); break;
            case FT::U2: o.u2(uint16_t(need_int(0, 65535, 65535))); break;
            case FT::U4: o.u4(uint32_t(need_int(0, 4294967295LL, start_t))); break;
            case FT::C1: {
                std::string s = need_str(nm == "MODE_COD" ? "P" : " ");
                if (s.size() != 1) throw Error("MIR." + nm + " must be exactly one character");
                o.c1(s[0]);
                break;
            }
            case FT::Cn: o.cn(need_str(""), ("MIR." + nm).c_str()); break;
            default: break;
        }
    }
    o.end();
}

void dtr(Out& o, const std::string& text) {
    o.begin(50, 30);
    o.cn(text, "DTR text");
    o.end();
}

size_t write_stream(Out& o, const WriteInput& in, const WriteMeta& meta, bool big) {
    const size_t n = in.n;
    // ---- group rows into parts (first-appearance order, stable within a part)
    std::vector<int32_t> part_of(n);
    std::vector<size_t> part_first;
    {
        PartKey pk{&in};
        std::unordered_map<std::string, int32_t> idx;
        for (size_t i = 0; i < n; ++i) {
            if (i > 0 && pk.same(i, i - 1)) {
                part_of[i] = part_of[i - 1];
                continue;
            }
            auto ins = idx.emplace(pk.bytes(i), int32_t(part_first.size()));
            if (ins.second) part_first.push_back(i);
            part_of[i] = ins.first->second;
        }
    }
    const size_t np = part_first.size();
    std::vector<size_t> start(np + 1, 0), order(n);
    for (size_t i = 0; i < n; ++i) ++start[size_t(part_of[i]) + 1];
    for (size_t p = 0; p < np; ++p) start[p + 1] += start[p];
    {
        std::vector<size_t> fill(start.begin(), start.end() - 1);
        for (size_t i = 0; i < n; ++i) order[fill[size_t(part_of[i])]++] = i;
    }

    // ---- times
    double tmin = std::numeric_limits<double>::infinity(), tmax = -tmin;
    for (size_t i = 0; i < n; ++i)
        if (in.part_ts.ok(i) && std::isfinite(in.part_ts.v[i])) {
            tmin = std::min(tmin, in.part_ts.v[i]);
            tmax = std::max(tmax, in.part_ts.v[i]);
        }
    auto clamp_t = [](double t) -> uint32_t {
        if (!std::isfinite(t) || t < 0) return 0;
        if (t > 4294967295.0) return 4294967295u;
        return uint32_t(std::floor(t));
    };
    uint32_t start_t = std::isfinite(tmin) ? clamp_t(tmin) : 0;
    uint32_t finish_t = std::isfinite(tmax) ? clamp_t(tmax) : start_t;
    for (auto& kv : meta.mir)
        if (kv.first == "START_T" && kv.second.is_int) start_t = uint32_t(kv.second.i);

    // ---- header
    o.begin(0, 10);
    o.u1(big ? 1 : 2);
    o.u1(4);
    o.end();
    write_mir(o, meta, start_t);
    for (auto& kv : meta.dtrs) {
        if (kv.first.empty() || kv.first.find('=') != std::string::npos)
            throw Error("invalid ry.* meta key '" + kv.first + "'");
        dtr(o, "ry." + kv.first + "=" + kv.second);
    }

    // ---- parts
    std::unordered_map<uint32_t, size_t> test_slot;
    std::vector<TestDef> tests;
    std::vector<uint32_t> test_order;
    std::map<uint16_t, std::pair<uint32_t, int>> hbins, sbins;  // bin -> (count, pf mask 1=pass 2=fail)
    uint32_t part_cnt = 0, rtst_cnt = 0, good_cnt = 0;
    char tsbuf[64];
    for (size_t p = 0; p < np; ++p) {
        const size_t r0 = part_first[p];
        const uint8_t site = in.site_num.ok(r0) ? in.site_num.v[r0] : 1;
        o.begin(5, 10);
        o.u1(1);
        o.u1(site);
        o.end();
        if (in.part_ts.ok(r0)) std::snprintf(tsbuf, sizeof tsbuf, "ry.part_ts=%.17g", in.part_ts.v[r0]);
        else std::snprintf(tsbuf, sizeof tsbuf, "ry.part_ts=");
        dtr(o, tsbuf);
        for (size_t k = start[p]; k < start[p + 1]; ++k) {
            const size_t i = order[k];
            const uint32_t tn = in.test_num.ok(i) ? in.test_num.v[i] : 0;
            auto it = test_slot.find(tn);
            if (it == test_slot.end()) {
                it = test_slot.emplace(tn, tests.size()).first;
                tests.emplace_back();
                test_order.push_back(tn);
            }
            TestDef& d = tests[it->second];
            const bool first = !d.seen;
            const bool name_ok = in.test_name.ok(i), units_ok = in.units.ok(i);
            const bool lo_ok = in.lo_limit.ok(i), hi_ok = in.hi_limit.ok(i);
            const float lo = lo_ok ? float(in.lo_limit.v[i]) : 0.f, hi = hi_ok ? float(in.hi_limit.v[i]) : 0.f;
            const bool res_ok = in.result.ok(i);
            const float res = res_ok ? float(in.result.v[i]) : 0.f;
            uint8_t flg = 0;
            if (!in.test_pass.ok(i)) flg |= 0x40;
            else if (!in.test_pass.v[i]) flg |= 0x80;
            if (!res_ok) flg |= 0x02;

            const bool name_same = name_ok ? str_eq(in.test_name, i, d.name_row) : d.name_row < 0;
            const bool units_same = units_ok ? str_eq(in.units, i, d.units_row) : d.units_row < 0;
            auto f_same = [](bool a_ok, float a, bool b_ok, float b) {
                return a_ok == b_ok && (!a_ok || std::memcmp(&a, &b, 4) == 0);
            };
            const bool compact = meta.compact_ptr && !first && name_same && units_same && f_same(lo_ok, lo, d.lo_ok, d.lo) &&
                                 f_same(hi_ok, hi, d.hi_ok, d.hi);
            o.begin(15, 10);
            o.u4(tn);
            o.u1(1);
            o.u1(in.site_num.ok(i) ? in.site_num.v[i] : 1);
            o.u1(flg);
            o.u1(0);
            o.r4(res);
            if (compact) {
                // TEST_TXT, ALARM_ID empty; OPT_FLAG and later fields omitted:
                // the reader takes name/units/limits from the first PTR.
                o.u1(0);
                o.u1(0);
            } else {
                if (name_ok) o.cn(in.test_name.ptr(i), in.test_name.len(i), "test_name");
                else o.u1(0);
                o.u1(0);  // ALARM_ID
                o.u1(uint8_t(0x02 | 0x0C | (lo_ok ? 0 : 0x40) | (hi_ok ? 0 : 0x80)));
                o.i1(0);
                o.i1(0);
                o.i1(0);
                o.r4(lo);
                o.r4(hi);
                if (units_ok) o.cn(in.units.ptr(i), in.units.len(i), "units");
                else o.u1(0);
            }
            o.end();
            if (first) {
                d.seen = true;
                d.lo_ok = lo_ok; d.lo = lo;
                d.hi_ok = hi_ok; d.hi = hi;
            }
            if (d.name_row < 0 && name_ok && in.test_name.len(i) > 0) d.name_row = int64_t(i);
            if (d.units_row < 0 && units_ok && in.units.len(i) > 0) d.units_row = int64_t(i);
            ++d.exec;
            if (flg & 0x80) ++d.fail;
            if (res_ok && std::isfinite(res)) {
                if (!d.any) { d.mn = d.mx = res; d.any = true; }
                d.mn = std::min(d.mn, res);
                d.mx = std::max(d.mx, res);
                d.sum += res;
                d.sq += double(res) * res;
            }
        }
        const bool retest = in.retest.ok(r0) && in.retest.v[r0];
        const bool pp_ok = in.part_pass.ok(r0), pass = pp_ok && in.part_pass.v[r0];
        uint8_t pflg = uint8_t((retest ? 0x01 : 0) | (pp_ok ? (pass ? 0 : 0x08) : 0x10));
        const uint16_t hb = in.hard_bin.ok(r0) ? in.hard_bin.v[r0] : 65535;
        const uint16_t sb = in.soft_bin.ok(r0) ? in.soft_bin.v[r0] : 65535;
        const size_t ntest = start[p + 1] - start[p];
        o.begin(5, 20);
        o.u1(1);
        o.u1(site);
        o.u1(pflg);
        o.u2(uint16_t(std::min<size_t>(ntest, 65535)));
        o.u2(hb);
        o.u2(sb);
        o.i2(-32768);
        o.i2(-32768);
        o.u4(0);
        if (in.part_id.ok(r0)) o.cn(in.part_id.ptr(r0), in.part_id.len(r0), "part_id");
        else o.u1(0);
        o.end();
        ++part_cnt;
        if (retest) ++rtst_cnt;
        if (pp_ok && pass) ++good_cnt;
        const int pf = pp_ok ? (pass ? 1 : 2) : 0;
        if (hb != 65535) { auto& e = hbins[hb]; ++e.first; e.second |= pf; }
        if (sb != 65535) { auto& e = sbins[sb]; ++e.first; e.second |= pf; }
    }

    // ---- summaries
    auto bin_rec = [&](uint8_t sub, const std::map<uint16_t, std::pair<uint32_t, int>>& bins) {
        for (auto& kv : bins) {
            o.begin(1, sub);
            o.u1(255);
            o.u1(0);
            o.u2(kv.first);
            o.u4(kv.second.first);
            o.c1(kv.second.second == 1 ? 'P' : kv.second.second == 2 ? 'F' : ' ');
            o.u1(0);
            o.end();
        }
    };
    bin_rec(40, hbins);
    bin_rec(50, sbins);
    o.begin(1, 30);
    o.u1(255);
    o.u1(0);
    o.u4(part_cnt);
    o.u4(rtst_cnt);
    o.u4(0);
    o.u4(good_cnt);
    o.u4(4294967295u);
    o.end();
    for (uint32_t tn : test_order) {
        const TestDef& d = tests[test_slot[tn]];
        o.begin(10, 30);
        o.u1(255);
        o.u1(0);
        o.c1('P');
        o.u4(tn);
        o.u4(d.exec);
        o.u4(d.fail);
        o.u4(4294967295u);
        if (d.name_row >= 0) o.cn(in.test_name.ptr(size_t(d.name_row)), in.test_name.len(size_t(d.name_row)), "test_name");
        else o.u1(0);
        o.u1(0);
        o.u1(0);
        o.u1(uint8_t(0x04 | (d.any ? 0 : 0x33)));
        o.r4(0);
        o.r4(d.mn);
        o.r4(d.mx);
        o.r4(float(d.sum));
        o.r4(float(d.sq));
        o.end();
    }
    o.begin(1, 20);
    o.u4(finish_t);
    o.c1(' ');
    o.u1(0);
    o.u1(0);
    o.end();
    return np;
}

}  // namespace

std::vector<uint8_t> write_buffer(const WriteInput& in, const WriteMeta& meta, bool big_endian, WriteStats* stats) {
    Out o(big_endian, nullptr);
    size_t np = write_stream(o, in, meta, big_endian);
    if (stats) {
        stats->parts = int64_t(np);
        stats->ptrs = int64_t(in.n);
        stats->records = o.records;
        stats->bytes = int64_t(o.buf.size());
    }
    return std::move(o.buf);
}

WriteStats write_file(const std::string& path, const WriteInput& in, const WriteMeta& meta, bool big_endian) {
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) throw Error("cannot open '" + path + "' for writing");
    WriteStats st;
    try {
        Out o(big_endian, f);
        st.parts = int64_t(write_stream(o, in, meta, big_endian));
        st.ptrs = int64_t(in.n);
        o.flush();
        st.records = o.records;
        st.bytes = o.bytes;
    } catch (...) {
        std::fclose(f);
        std::remove(path.c_str());
        throw;
    }
    if (std::fclose(f) != 0) throw Error("error closing '" + path + "'");
    return st;
}

}  // namespace ry::stdf
