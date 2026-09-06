// RadioYield STDF V4 reader / writer (C++17).
//
// The reader turns an STDF V4 byte stream into Arrow-layout columns (validity
// bitmap + data/offset buffers) that the Python layer wraps zero-copy into
// pyarrow arrays. The writer goes the other way for the measurement table
// defined in radioyield/schema.py.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace ry::stdf {

// Allocator whose resize() leaves new elements uninitialized (they are always
// overwritten right away); avoids a memset per appended field.
template <class T>
struct DefaultInitAllocator : std::allocator<T> {
    template <class U>
    struct rebind {
        using other = DefaultInitAllocator<U>;
    };
    using std::allocator<T>::allocator;
    DefaultInitAllocator() = default;
    template <class U>
    DefaultInitAllocator(const DefaultInitAllocator<U>&) noexcept {}
    template <class U>
    void construct(U* p) noexcept {
        ::new (static_cast<void*>(p)) U;
    }
    template <class U, class... Args>
    void construct(U* p, Args&&... args) {
        ::new (static_cast<void*>(p)) U(std::forward<Args>(args)...);
    }
};
using Bytes = std::vector<uint8_t, DefaultInitAllocator<uint8_t>>;

// Raised for inputs that cannot be STDF at all (empty, first record not FAR)
// and for writer misuse. Malformed content *inside* an STDF file never throws:
// it produces a partial result plus warnings in Stats.
struct Error : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// ---------------------------------------------------------------- record specs
enum class FT : uint8_t {
    U1, U2, U4, I1, I2, I4, R4, R8, C1, Cn, Bn, Dn, B1, N1,
    KU1, KU2, KR4, KCn, KN1,  // arrays, count taken from an earlier field
    Vn,                        // GDR generic data
};

struct FieldSpec {
    const char* name;
    FT type;
    int8_t count_field;  // index of the field holding k for K* types, else -1
};

struct RecordSpec {
    uint8_t typ, sub;
    const char* name;
    std::vector<FieldSpec> fields;
};

const std::vector<RecordSpec>& record_specs();
const RecordSpec* find_spec(uint8_t typ, uint8_t sub);  // nullptr if unknown

// ---------------------------------------------------------------- columns
// One Arrow-layout column. During building, validity is one byte per row; it
// is packed into an LSB bitmap by finish().
struct Column {
    enum Kind : uint8_t { FIXED, BOOL, STRING, BINARY, LIST };
    Kind kind = FIXED;
    std::string type;  // arrow type name: uint8 ... float64, bool, string, binary, list
    int width = 0;     // bytes per element for FIXED
    int64_t length = 0;
    int64_t null_count = 0;
    std::vector<uint8_t> valid;    // byte-per-row once the first null arrives; bitmap after finish()
    Bytes data;                    // fixed data / bool bits / chars
    std::vector<int32_t> offsets;  // STRING, BINARY, LIST
    std::unique_ptr<Column> child; // LIST
    bool finished = false;

    static Column fixed(const char* type, int width);
    static Column boolean();
    static Column string();
    static Column binary();
    static Column list(Column child);

    template <class T>
    void push(T v) {
        size_t o = data.size();
        data.resize(o + sizeof(T));
        std::memcpy(data.data() + o, &v, sizeof(T));
        if (null_count) valid.push_back(1);
        ++length;
    }
    void push_bool(bool v);
    void push_str(const uint8_t* s, size_t n);  // latin-1 -> valid UTF-8
    void push_bytes(const uint8_t* s, size_t n);
    void push_null();
    // LIST: call begin_list(), append to *child, then end_list().
    void end_list();  // closes the list element started at the last offset
    void finish();    // pack validity; drop it if there are no nulls
};

struct Table {
    std::vector<std::string> names;
    std::vector<Column> cols;
    int64_t rows = 0;
};

// ---------------------------------------------------------------- reader
struct MetaValue {
    bool is_int = false;
    int64_t i = 0;
    std::string s;
};

struct Stats {
    std::map<std::string, int64_t> counts;          // per record type name
    std::map<std::string, int64_t> unknown_types;   // "typ.sub" -> count
    std::map<std::string, int64_t> events;          // named warning/event counters
    std::vector<std::string> warning_messages;      // first N warnings, human readable
    int64_t warnings = 0;
    int64_t records = 0;       // all records seen (known + unknown)
    int64_t unknown = 0;       // records skipped by length
    int64_t bytes = 0;         // file size
    int64_t bytes_parsed = 0;  // bytes consumed by complete records
    bool truncated = false;    // stopped before EOF on a record running past it
    char byte_order = '<';
    int cpu_type = -1;
    int stdf_ver = -1;
};

struct ParseOptions {
    bool records = true;          // build per-record-type tables (else only small ones)
    size_t max_warning_messages = 50;
};

struct ParseResult {
    Table measurements;                      // MEASUREMENT_SCHEMA order
    std::map<std::string, Table> records;    // record name -> raw field table
    std::vector<std::pair<std::string, MetaValue>> meta;
    Stats stats;
};

extern const char* const kMeasurementColumns[18];

ParseResult parse_buffer(const uint8_t* data, size_t len, const ParseOptions& opt = {});
ParseResult parse_file(const std::string& path, const ParseOptions& opt = {});

// ---------------------------------------------------------------- writer
template <class T>
struct NumCol {
    const T* v = nullptr;
    const uint8_t* valid = nullptr;  // nullptr: all valid
    bool ok(size_t i) const { return !valid || valid[i]; }
};

struct StrCol {
    const int32_t* offsets = nullptr;  // n+1 entries
    const uint8_t* data = nullptr;
    const uint8_t* valid = nullptr;
    bool ok(size_t i) const { return !valid || valid[i]; }
    size_t len(size_t i) const { return size_t(offsets[i + 1] - offsets[i]); }
    const uint8_t* ptr(size_t i) const { return data + offsets[i]; }
};

struct WriteInput {
    size_t n = 0;
    NumCol<uint8_t> site_num;
    StrCol part_id;
    NumCol<double> part_ts;
    NumCol<uint8_t> retest;  // bool as 0/1
    NumCol<uint32_t> test_num;
    StrCol test_name, units;
    NumCol<double> lo_limit, hi_limit, result;
    NumCol<uint8_t> test_pass, part_pass;
    NumCol<uint16_t> hard_bin, soft_bin;
};

struct WriteMeta {
    // MIR fields by STDF name (LOT_ID, NODE_NAM, PART_TYP, START_T, ...).
    std::vector<std::pair<std::string, MetaValue>> mir;
    // File-level DTRs, written as "ry.<key>=<value>" before the first PIR.
    std::vector<std::pair<std::string, std::string>> dtrs;
    // true: a PTR whose name/units/limits equal its test's defaults (set by the
    // first PTR of that TEST_NUM) is written short, ending after ALARM_ID.
    // false: every PTR carries TEST_TXT, OPT_FLAG, limits and UNITS.
    bool compact_ptr = true;
};

struct WriteStats {
    int64_t records = 0;
    int64_t parts = 0;
    int64_t ptrs = 0;
    int64_t bytes = 0;
};

// big_endian=false writes CPU_TYPE=2 (little endian), true writes CPU_TYPE=1.
std::vector<uint8_t> write_buffer(const WriteInput& in, const WriteMeta& meta, bool big_endian,
                                  WriteStats* stats = nullptr);
WriteStats write_file(const std::string& path, const WriteInput& in, const WriteMeta& meta,
                      bool big_endian);

}  // namespace ry::stdf
