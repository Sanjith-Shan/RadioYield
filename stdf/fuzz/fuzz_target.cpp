// Fuzz entry point (libFuzzer signature). Used by the libFuzzer build, the
// in-house mutation driver (mutate_driver.cpp) and the regression replayer.
//
// Besides "no crash / no sanitizer report", it checks structural invariants of
// every produced table and round-trips the measurement table through the
// writer and back, so logic bugs that leave the output inconsistent are
// findings too (they abort).
#include <cstdio>
#include <cstdlib>

#include "ry_stdf/stdf.hpp"

using namespace ry::stdf;

namespace {

[[noreturn]] void fail(const char* what) {
    std::fprintf(stderr, "INVARIANT VIOLATED: %s\n", what);
    std::abort();
}

void check_col(const Column& c, int64_t rows) {
    if (c.length != rows) fail("column length != table rows");
    if (c.null_count < 0 || c.null_count > c.length) fail("null_count out of range");
    if (c.null_count > 0 && c.valid.size() != size_t((c.length + 7) / 8)) fail("validity bitmap size");
    switch (c.kind) {
        case Column::FIXED:
            if (c.data.size() != size_t(c.length) * size_t(c.width)) fail("fixed data size");
            break;
        case Column::BOOL:
            if (c.data.size() != size_t((c.length + 7) / 8)) fail("bool data size");
            break;
        case Column::STRING:
        case Column::BINARY:
        case Column::LIST: {
            if (c.offsets.size() != size_t(c.length) + 1 || c.offsets[0] != 0) fail("offsets size");
            for (size_t i = 0; i + 1 < c.offsets.size(); ++i)
                if (c.offsets[i + 1] < c.offsets[i]) fail("offsets not monotone");
            if (c.kind == Column::LIST) {
                if (!c.child) fail("list without child");
                if (c.offsets.back() != c.child->length) fail("list offsets vs child length");
                check_col(*c.child, c.child->length);
            } else if (size_t(c.offsets.back()) != c.data.size()) {
                fail("string offsets vs data size");
            }
            if (c.kind == Column::STRING) {  // must be valid UTF-8 (we only emit ASCII + 2-byte seqs)
                for (size_t i = 0; i < c.data.size(); ++i) {
                    uint8_t b = c.data[i];
                    if (b < 0x80) continue;
                    if ((b & 0xE0) != 0xC0 || i + 1 >= c.data.size() || (c.data[i + 1] & 0xC0) != 0x80)
                        fail("invalid UTF-8 in string column");
                    ++i;
                }
            }
            break;
        }
    }
}

void check_utf8(const std::string& s) {
    for (size_t i = 0; i < s.size(); ++i) {
        uint8_t b = uint8_t(s[i]);
        if (b < 0x80) continue;
        if ((b & 0xE0) != 0xC0 || i + 1 >= s.size() || (uint8_t(s[i + 1]) & 0xC0) != 0x80)
            fail("invalid UTF-8 in meta / warning text");
        ++i;
    }
}

void check_table(const Table& t) {
    if (t.names.size() != t.cols.size()) fail("names vs cols");
    for (const auto& c : t.cols) check_col(c, t.rows);
}

bool bit(const uint8_t* bits, int64_t i) { return (bits[size_t(i >> 3)] >> (i & 7)) & 1; }

// Re-encode the measurement table with the writer and read it back.
void round_trip(const ParseResult& r) {
    const Table& m = r.measurements;
    const int64_t n = m.rows;
    if (n == 0) return;
    auto col = [&](const char* name) -> const Column& {
        for (size_t i = 0; i < m.names.size(); ++i)
            if (m.names[i] == name) return m.cols[i];
        fail("missing measurement column");
    };
    std::vector<std::vector<uint8_t>> keep;
    auto validity = [&](const Column& c) -> const uint8_t* {
        if (c.null_count == 0) return nullptr;
        keep.emplace_back(size_t(n));
        for (int64_t i = 0; i < n; ++i) keep.back()[size_t(i)] = bit(c.valid.data(), i);
        return keep.back().data();
    };
    auto bools = [&](const Column& c) -> const uint8_t* {
        keep.emplace_back(size_t(n));
        for (int64_t i = 0; i < n; ++i) keep.back()[size_t(i)] = bit(c.data.data(), i);
        return keep.back().data();
    };
    auto str = [&](const Column& c) {
        StrCol s;
        s.offsets = c.offsets.data();
        s.data = c.data.data();
        s.valid = validity(c);
        return s;
    };
    WriteInput in;
    in.n = size_t(n);
    const Column& site = col("site_num");
    in.site_num = {site.data.data(), validity(site)};
    in.part_id = str(col("part_id"));
    const Column& ts = col("part_ts");
    in.part_ts = {reinterpret_cast<const double*>(ts.data.data()), validity(ts)};
    const Column& rt = col("retest");
    in.retest = {bools(rt), validity(rt)};
    const Column& tn = col("test_num");
    in.test_num = {reinterpret_cast<const uint32_t*>(tn.data.data()), validity(tn)};
    in.test_name = str(col("test_name"));
    in.units = str(col("units"));
    const Column &lo = col("lo_limit"), &hi = col("hi_limit"), &res = col("result");
    in.lo_limit = {reinterpret_cast<const double*>(lo.data.data()), validity(lo)};
    in.hi_limit = {reinterpret_cast<const double*>(hi.data.data()), validity(hi)};
    in.result = {reinterpret_cast<const double*>(res.data.data()), validity(res)};
    const Column &tp = col("test_pass"), &pp = col("part_pass");
    in.test_pass = {bools(tp), validity(tp)};
    in.part_pass = {bools(pp), validity(pp)};
    const Column &hb = col("hard_bin"), &sb = col("soft_bin");
    in.hard_bin = {reinterpret_cast<const uint16_t*>(hb.data.data()), validity(hb)};
    in.soft_bin = {reinterpret_cast<const uint16_t*>(sb.data.data()), validity(sb)};
    WriteMeta meta;
    for (const auto& kv : r.meta) {
        bool is_mir = !kv.first.empty() && kv.first[0] >= 'A' && kv.first[0] <= 'Z';
        if (is_mir) meta.mir.push_back(kv);
        else if (kv.first != "lot_id" && kv.first != "station" && kv.first.find('=') == std::string::npos)
            meta.dtrs.emplace_back(kv.first, kv.second.s);
    }
    std::vector<uint8_t> buf;
    try {
        buf = write_buffer(in, meta, (n & 1) != 0);
    } catch (const Error&) {
        return;  // e.g. a string grew past 255 bytes after Latin-1 -> UTF-8 mapping
    }
    ParseResult r2 = parse_buffer(buf.data(), buf.size());
    check_table(r2.measurements);
    if (r2.measurements.rows != n) fail("writer round trip changed the row count");
    if (r2.stats.warnings != 0) fail("writer output produced reader warnings");
}

}  // namespace

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    try {
        ParseResult r = parse_buffer(data, size);
        check_table(r.measurements);
        for (const auto& kv : r.records) check_table(kv.second);
        if (r.stats.bytes_parsed > r.stats.bytes) fail("bytes_parsed > bytes");
        for (const auto& kv : r.meta) {  // everything that becomes a Python str
            check_utf8(kv.first);
            if (!kv.second.is_int) check_utf8(kv.second.s);
        }
        for (const auto& m : r.stats.warning_messages) check_utf8(m);
        for (const auto& kv : r.stats.events) check_utf8(kv.first);
        round_trip(r);
    } catch (const Error&) {
        // clean rejection (not STDF at all)
    }
    return 0;
}
