// Dependency-free C++ unit tests for the STDF core (run by ctest).
#include <cmath>
#include <cstdio>
#include <functional>
#include <random>
#include <string>
#include <vector>

#include "bytes.hpp"
#include "ry_stdf/stdf.hpp"

using namespace ry::stdf;

static int g_fail = 0, g_checks = 0;
#define CHECK(cond)                                                                \
    do {                                                                           \
        ++g_checks;                                                                \
        if (!(cond)) {                                                             \
            ++g_fail;                                                              \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                          \
    } while (0)

// ------------------------------------------------------------ helpers
struct Rec {
    bool big;
    std::vector<uint8_t> b;
    template <class T>
    Rec& n(T v) {
        size_t o = b.size();
        b.resize(o + sizeof(T));
        detail::store<T>(b.data() + o, v, big == detail::host_is_little());
        return *this;
    }
    Rec& s(const std::string& x) {
        b.push_back(uint8_t(x.size()));
        b.insert(b.end(), x.begin(), x.end());
        return *this;
    }
};

struct File {
    bool big;
    std::vector<uint8_t> d;
    void add(uint8_t typ, uint8_t sub, const Rec& r) {
        Rec h{big, {}};
        h.n<uint16_t>(uint16_t(r.b.size()));
        d.insert(d.end(), h.b.begin(), h.b.end());
        d.push_back(typ);
        d.push_back(sub);
        d.insert(d.end(), r.b.begin(), r.b.end());
    }
    Rec r() const { return Rec{big, {}}; }
};

static const Column& col(const Table& t, const char* name) {
    for (size_t i = 0; i < t.names.size(); ++i)
        if (t.names[i] == name) return t.cols[i];
    std::fprintf(stderr, "no column %s\n", name);
    std::abort();
}
static bool is_valid(const Column& c, int64_t i) {
    return c.null_count == 0 || ((c.valid[size_t(i >> 3)] >> (i & 7)) & 1);
}
template <class T>
static T val(const Column& c, int64_t i) {
    T v;
    std::memcpy(&v, c.data.data() + size_t(i) * sizeof(T), sizeof(T));
    return v;
}
static std::string str(const Column& c, int64_t i) {
    return std::string(reinterpret_cast<const char*>(c.data.data()) + c.offsets[size_t(i)],
                       size_t(c.offsets[size_t(i) + 1] - c.offsets[size_t(i)]));
}
static bool bit(const Column& c, int64_t i) { return (c.data[size_t(i >> 3)] >> (i & 7)) & 1; }

// FAR + MIR(START_T=1000, LOT_ID, NODE_NAM) prefix
static File header(bool big) {
    File f{big, {}};
    f.add(0, 10, f.r().n<uint8_t>(big ? 1 : 2).n<uint8_t>(4));
    Rec mir = f.r();
    mir.n<uint32_t>(999).n<uint32_t>(1000).n<uint8_t>(1);
    mir.b.push_back('P'); mir.b.push_back(' '); mir.b.push_back(' ');
    mir.n<uint16_t>(65535);
    mir.b.push_back(' ');
    mir.s("LOT9").s("PT").s("stationA");
    f.add(1, 10, mir);
    return f;
}

// ------------------------------------------------------------ writer round trip
struct Owned {
    std::vector<uint8_t> site, retest, tpass, ppass;
    std::vector<double> ts, lo, hi, res;
    std::vector<uint32_t> tn;
    std::vector<uint16_t> hb, sb;
    std::vector<int32_t> pid_o{0}, name_o{0}, units_o{0};
    std::vector<uint8_t> pid_d, name_d, units_d;
    std::vector<uint8_t> lo_v, hi_v, res_v, sb_v;
};

static void push_s(std::vector<int32_t>& o, std::vector<uint8_t>& d, const std::string& s) {
    d.insert(d.end(), s.begin(), s.end());
    o.push_back(int32_t(d.size()));
}

static WriteInput make_input(Owned& w, size_t parts, size_t tests) {
    for (size_t p = 0; p < parts; ++p)
        for (size_t t = 0; t < tests; ++t) {
            w.site.push_back(uint8_t(1 + p % 4));
            w.retest.push_back(p == parts - 1);
            w.tpass.push_back(!(p == 2 && t == 1));
            w.ppass.push_back(p != 2);
            w.ts.push_back(1.7e9 + double(p) * 1.25);
            w.tn.push_back(uint32_t(100 + t));
            bool nolo = t == 2;
            w.lo.push_back(nolo ? 0 : -1.0 - double(t));
            w.lo_v.push_back(!nolo);
            w.hi.push_back(10.5 + double(t));
            w.hi_v.push_back(1);
            bool nores = (p == 1 && t == 0);
            w.res.push_back(nores ? 0 : 0.1 * double(p) + double(t));
            w.res_v.push_back(!nores);
            w.hb.push_back(p == 2 ? 5 : 1);
            w.sb.push_back(p == 2 ? 50 : 1);
            w.sb_v.push_back(p != 0);
            push_s(w.pid_o, w.pid_d, "SN" + std::to_string(p));
            push_s(w.name_o, w.name_d, "test_" + std::to_string(t));
            push_s(w.units_o, w.units_d, t == 1 ? "dBm" : "V");
        }
    WriteInput in;
    in.n = w.tn.size();
    in.site_num = {w.site.data(), nullptr};
    in.part_id = {w.pid_o.data(), w.pid_d.data(), nullptr};
    in.part_ts = {w.ts.data(), nullptr};
    in.retest = {w.retest.data(), nullptr};
    in.test_num = {w.tn.data(), nullptr};
    in.test_name = {w.name_o.data(), w.name_d.data(), nullptr};
    in.units = {w.units_o.data(), w.units_d.data(), nullptr};
    in.lo_limit = {w.lo.data(), w.lo_v.data()};
    in.hi_limit = {w.hi.data(), w.hi_v.data()};
    in.result = {w.res.data(), w.res_v.data()};
    in.test_pass = {w.tpass.data(), nullptr};
    in.part_pass = {w.ppass.data(), nullptr};
    in.hard_bin = {w.hb.data(), nullptr};
    in.soft_bin = {w.sb.data(), w.sb_v.data()};
    return in;
}

static void test_round_trip(bool big) {
    Owned w;
    WriteInput in = make_input(w, 6, 4);
    WriteMeta meta;
    MetaValue lot; lot.s = "LOT1";
    MetaValue stn; stn.s = "ST-2";
    meta.mir = {{"LOT_ID", lot}, {"NODE_NAM", stn}};
    meta.dtrs = {{"build", "EVT1"}, {"data_origin", "SIMULATED"}, {"distance_m", "3"}};
    auto buf = write_buffer(in, meta, big);
    CHECK(buf[4] == (big ? 1 : 2));
    auto r = parse_buffer(buf.data(), buf.size());
    CHECK(r.stats.byte_order == (big ? '>' : '<'));
    CHECK(r.stats.warnings == 0);
    CHECK(!r.stats.truncated);
    const Table& m = r.measurements;
    CHECK(m.rows == int64_t(in.n));
    CHECK(r.stats.counts["PIR"] == 6 && r.stats.counts["PRR"] == 6 && r.stats.counts["PTR"] == 24);
    CHECK(r.stats.counts["MRR"] == 1 && r.stats.counts["PCR"] == 1 && r.stats.counts["TSR"] == 4);
    for (int64_t i = 0; i < m.rows; ++i) {
        size_t k = size_t(i);
        CHECK(str(col(m, "lot_id"), i) == "LOT1");
        CHECK(str(col(m, "station"), i) == "ST-2");
        CHECK(str(col(m, "build"), i) == "EVT1");
        CHECK(str(col(m, "data_origin"), i) == "SIMULATED");
        CHECK(val<uint8_t>(col(m, "site_num"), i) == w.site[k]);
        CHECK(val<double>(col(m, "part_ts"), i) == w.ts[k]);
        CHECK(bit(col(m, "retest"), i) == bool(w.retest[k]));
        CHECK(val<uint32_t>(col(m, "test_num"), i) == w.tn[k]);
        CHECK(str(col(m, "test_name"), i) == "test_" + std::to_string(w.tn[k] - 100));
        CHECK(str(col(m, "units"), i) == (w.tn[k] == 101 ? "dBm" : "V"));
        CHECK(is_valid(col(m, "lo_limit"), i) == bool(w.lo_v[k]));
        if (w.lo_v[k]) CHECK(val<double>(col(m, "lo_limit"), i) == double(float(w.lo[k])));
        CHECK(val<double>(col(m, "hi_limit"), i) == double(float(w.hi[k])));
        CHECK(is_valid(col(m, "result"), i) == bool(w.res_v[k]));
        if (w.res_v[k]) CHECK(val<double>(col(m, "result"), i) == double(float(w.res[k])));
        CHECK(bit(col(m, "test_pass"), i) == bool(w.tpass[k]));
        CHECK(bit(col(m, "part_pass"), i) == bool(w.ppass[k]));
        CHECK(val<uint16_t>(col(m, "hard_bin"), i) == w.hb[k]);
        CHECK(is_valid(col(m, "soft_bin"), i) == bool(w.sb_v[k]));
        if (w.sb_v[k]) CHECK(val<uint16_t>(col(m, "soft_bin"), i) == w.sb[k]);
    }
    bool saw_distance = false;
    for (auto& kv : r.meta) saw_distance |= kv.first == "distance_m" && kv.second.s == "3";
    CHECK(saw_distance);
    // compact PTRs: later PTRs of a test omit OPT_FLAG and limits
    const Table& ptr = r.records.at("PTR");
    CHECK(ptr.rows == 24);
    CHECK(is_valid(col(ptr, "OPT_FLAG"), 0));
    CHECK(!is_valid(col(ptr, "OPT_FLAG"), 23));
}

// ------------------------------------------------------------ PTR default semantics
static void test_ptr_defaults(bool big) {
    File f = header(big);
    f.add(5, 10, f.r().n<uint8_t>(1).n<uint8_t>(1));
    auto ptr_head = [&](float result) {
        Rec r = f.r();
        r.n<uint32_t>(7).n<uint8_t>(1).n<uint8_t>(1).n<uint8_t>(0).n<uint8_t>(0).n<float>(result);
        return r;
    };
    // 1: first PTR: name, limits [1,2], units
    Rec a = ptr_head(1.5f);
    a.s("vdd").s("").n<uint8_t>(0x0E).n<int8_t>(0).n<int8_t>(0).n<int8_t>(0).n<float>(1).n<float>(2).s("V");
    f.add(15, 10, a);
    // 2: ends after RESULT (no TEST_TXT, no OPT_FLAG) -> all defaults
    f.add(15, 10, ptr_head(1.6f));
    // 3: OPT_FLAG bit 6 -> no low limit for this PTR; explicit HI 3
    Rec c = ptr_head(1.7f);
    c.s("").s("").n<uint8_t>(0x4E).n<int8_t>(0).n<int8_t>(0).n<int8_t>(0).n<float>(9).n<float>(3);
    f.add(15, 10, c);
    // 4: OPT_FLAG bits 4,5 -> use defaults even though values present
    Rec d = ptr_head(1.8f);
    d.s("").s("").n<uint8_t>(0x3E).n<int8_t>(0).n<int8_t>(0).n<int8_t>(0).n<float>(9).n<float>(9).s("mV");
    f.add(15, 10, d);
    // 5: TEST_FLG bit 7 fail, bit 1 result invalid
    Rec e = f.r();
    e.n<uint32_t>(7).n<uint8_t>(1).n<uint8_t>(1).n<uint8_t>(0x82).n<uint8_t>(0).n<float>(0);
    f.add(15, 10, e);
    // DTR part_ts, PRR (PART_FLG retest+fail), ends before PART_TXT
    f.add(50, 30, f.r().s("ry.part_ts=1234.5"));
    Rec prr = f.r();
    prr.n<uint8_t>(1).n<uint8_t>(1).n<uint8_t>(0x09).n<uint16_t>(5).n<uint16_t>(3).n<uint16_t>(65535)
        .n<int16_t>(0).n<int16_t>(0).n<uint32_t>(0).s("U1");
    f.add(5, 20, prr);
    auto r = parse_buffer(f.d.data(), f.d.size());
    const Table& m = r.measurements;
    CHECK(m.rows == 5);
    CHECK(r.stats.warnings == 0);
    const Column &lo = col(m, "lo_limit"), &hi = col(m, "hi_limit"), &u = col(m, "units"), &nm = col(m, "test_name");
    for (int64_t i = 0; i < 5; ++i) CHECK(str(nm, i) == "vdd");
    CHECK(val<double>(lo, 0) == 1 && val<double>(hi, 0) == 2 && str(u, 0) == "V");
    CHECK(val<double>(lo, 1) == 1 && val<double>(hi, 1) == 2 && str(u, 1) == "V");
    CHECK(!is_valid(lo, 2) && val<double>(hi, 2) == 3);
    CHECK(val<double>(lo, 3) == 1 && val<double>(hi, 3) == 2 && str(u, 3) == "mV");
    CHECK(val<double>(lo, 4) == 1 && str(u, 4) == "V");
    const Column& res = col(m, "result");
    CHECK(val<double>(res, 1) == double(1.6f));
    CHECK(!is_valid(res, 4));
    CHECK(bit(col(m, "test_pass"), 0) && !bit(col(m, "test_pass"), 4));
    CHECK(!bit(col(m, "part_pass"), 0));
    CHECK(bit(col(m, "retest"), 0));
    CHECK(val<uint16_t>(col(m, "hard_bin"), 0) == 3);
    CHECK(!is_valid(col(m, "soft_bin"), 0));
    CHECK(str(col(m, "part_id"), 0) == "U1");
    CHECK(val<double>(col(m, "part_ts"), 0) == 1234.5);
    CHECK(str(col(m, "lot_id"), 0) == "LOT9" && str(col(m, "station"), 0) == "stationA");
    CHECK(!is_valid(col(m, "build"), 0));
    CHECK(str(col(m, "data_origin"), 0) == "UNKNOWN");
    // raw PRR table: PART_TXT / PART_FIX missing -> null
    const Table& prr_t = r.records.at("PRR");
    CHECK(!is_valid(col(prr_t, "PART_TXT"), 0) && !is_valid(col(prr_t, "PART_FIX"), 0));
}

// ------------------------------------------------------------ robustness
static void test_truncation_and_garbage() {
    Owned w;
    WriteInput in = make_input(w, 3, 3);
    WriteMeta meta;
    auto buf = write_buffer(in, meta, false);
    int errors = 0, truncated = 0;
    for (size_t cut = 0; cut <= buf.size(); ++cut) {
        try {
            auto r = parse_buffer(buf.data(), cut);
            truncated += r.stats.truncated;
            CHECK(r.stats.bytes_parsed <= int64_t(cut));
        } catch (const Error&) {
            ++errors;
        }
    }
    CHECK(errors == 6);  // cuts 0..5 cannot hold a FAR
    CHECK(truncated > 0);
    // unknown record types are skipped by length
    File f = header(false);
    Rec junk = f.r();
    for (int i = 0; i < 37; ++i) junk.n<uint8_t>(uint8_t(i * 7));
    f.add(180, 7, junk);
    f.add(5, 10, f.r().n<uint8_t>(1).n<uint8_t>(1));
    f.add(5, 20, f.r().n<uint8_t>(1).n<uint8_t>(1));
    auto r = parse_buffer(f.d.data(), f.d.size());
    CHECK(r.stats.unknown == 1 && r.stats.unknown_types["180.7"] == 1);
    CHECK(r.stats.counts["PIR"] == 1 && r.stats.counts["PRR"] == 1);
    // not STDF
    const uint8_t not_stdf[] = {'h', 'e', 'l', 'l', 'o', '!', '!', '!'};
    bool threw = false;
    try { parse_buffer(not_stdf, sizeof not_stdf); } catch (const Error&) { threw = true; }
    CHECK(threw);
    // random garbage after a valid FAR/MIR never throws
    std::mt19937 g(42);
    for (int trial = 0; trial < 300; ++trial) {
        File h = header(trial & 1);
        size_t n = g() % 2048;
        for (size_t i = 0; i < n; ++i) h.d.push_back(uint8_t(g()));
        auto rr = parse_buffer(h.d.data(), h.d.size());
        CHECK(rr.stats.bytes_parsed <= rr.stats.bytes);
    }
    // field running past REC_LEN: Cn length larger than the record
    File o = header(false);
    o.add(5, 10, o.r().n<uint8_t>(1).n<uint8_t>(1));
    Rec bad = o.r();
    bad.n<uint32_t>(1).n<uint8_t>(1).n<uint8_t>(1).n<uint8_t>(0).n<uint8_t>(0).n<float>(2.f).n<uint8_t>(200);
    o.add(15, 10, bad);
    o.add(5, 20, o.r().n<uint8_t>(1).n<uint8_t>(1).n<uint8_t>(0).n<uint16_t>(1).n<uint16_t>(1));
    auto ro = parse_buffer(o.d.data(), o.d.size());
    CHECK(ro.measurements.rows == 1);
    CHECK(ro.stats.events["field_overrun"] >= 1);
    CHECK(!is_valid(col(ro.measurements, "part_id"), 0));
}

static void test_arrays_and_gdr() {
    File f = header(true);
    Rec pgr = f.r();
    pgr.n<uint16_t>(3).s("grp").n<uint16_t>(3).n<uint16_t>(10).n<uint16_t>(11).n<uint16_t>(12);
    f.add(1, 62, pgr);
    Rec gdr = f.r();
    gdr.n<uint16_t>(3).n<uint8_t>(1).n<uint8_t>(42).n<uint8_t>(10).s("hi").n<uint8_t>(7).n<float>(0.5f);
    f.add(50, 10, gdr);
    Rec mpr = f.r();  // 3 return states (N1), 2 results
    mpr.n<uint32_t>(9).n<uint8_t>(1).n<uint8_t>(1).n<uint8_t>(0).n<uint8_t>(0).n<uint16_t>(3).n<uint16_t>(2)
        .n<uint8_t>(0x21).n<uint8_t>(0x03).n<float>(1.f).n<float>(2.f);
    f.add(15, 15, mpr);
    auto r = parse_buffer(f.d.data(), f.d.size());
    const Column& pi = col(r.records.at("PGR"), "PMR_INDX");
    CHECK(pi.offsets[1] == 3 && val<uint16_t>(*pi.child, 2) == 12);
    const Column& gd = col(r.records.at("GDR"), "GEN_DATA");
    CHECK(gd.offsets[1] == 3);
    CHECK(str(*gd.child, 0) == "U1:42" && str(*gd.child, 1) == "Cn:hi" && str(*gd.child, 2) == "R4:0.5");
    const Column& st = col(r.records.at("MPR"), "RTN_STAT");
    CHECK(st.offsets[1] == 3 && val<uint8_t>(*st.child, 0) == 1 && val<uint8_t>(*st.child, 1) == 2 &&
          val<uint8_t>(*st.child, 2) == 3);
    const Column& rr = col(r.records.at("MPR"), "RTN_RSLT");
    CHECK(val<float>(*rr.child, 1) == 2.f);
    CHECK(!is_valid(col(r.records.at("MPR"), "TEST_TXT"), 0));
}

// fuzz finding 1: the first PTR of a file had TEST_NUM 0xFFFFFFFF, which
// collided with the "no previous test" sentinel of the per-test cache and
// indexed an empty vector.
static void test_regression_test_num_max() {
    File f = header(false);
    f.add(5, 10, f.r().n<uint8_t>(1).n<uint8_t>(1));
    Rec p = f.r();
    p.n<uint32_t>(0xFFFFFFFFu).n<uint8_t>(1).n<uint8_t>(1).n<uint8_t>(0).n<uint8_t>(0).n<float>(1.f).s("x");
    f.add(15, 10, p);
    f.add(15, 10, p);
    f.add(5, 20, f.r().n<uint8_t>(1).n<uint8_t>(1).n<uint8_t>(0).n<uint16_t>(1).n<uint16_t>(1));
    auto r = parse_buffer(f.d.data(), f.d.size());
    CHECK(r.measurements.rows == 2);
    CHECK(val<uint32_t>(col(r.measurements, "test_num"), 1) == 0xFFFFFFFFu);
}

int main() {
    test_regression_test_num_max();
    test_round_trip(false);
    test_round_trip(true);
    test_ptr_defaults(false);
    test_ptr_defaults(true);
    test_truncation_and_garbage();
    test_arrays_and_gdr();
    std::printf("%d checks, %d failures\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
