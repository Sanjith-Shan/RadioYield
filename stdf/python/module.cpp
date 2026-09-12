// pybind11 bindings: radioyield.stdf._stdf
//
// Columns cross into Python as numpy arrays that own the C++ buffers (no
// copy); radioyield/stdf/_api.py wraps them into pyarrow arrays with
// pa.Array.from_buffers (again no copy).
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "ry_stdf/stdf.hpp"

namespace py = pybind11;
using namespace ry::stdf;

namespace {

template <class Vec>
py::array to_np(Vec&& v) {
    using V = std::decay_t<Vec>;
    using T = typename V::value_type;
    auto* p = new V(std::move(v));
    py::capsule owner(p, [](void* x) { delete reinterpret_cast<V*>(x); });
    return py::array_t<T>({py::ssize_t(p->size())}, {py::ssize_t(sizeof(T))}, p->data(), owner);
}

py::dict col_to_py(Column& c) {
    c.finish();
    py::dict d;
    d["type"] = c.type;
    d["length"] = c.length;
    d["null_count"] = c.null_count;
    d["validity"] = c.valid.empty() ? py::object(py::none()) : py::object(to_np(std::move(c.valid)));
    d["data"] = to_np(std::move(c.data));
    if (c.kind == Column::STRING || c.kind == Column::BINARY || c.kind == Column::LIST)
        d["offsets"] = to_np(std::move(c.offsets));
    else
        d["offsets"] = py::none();
    d["child"] = c.child ? py::object(col_to_py(*c.child)) : py::object(py::none());
    return d;
}

py::dict table_to_py(Table& t) {
    py::dict d;
    py::list names, cols;
    for (size_t i = 0; i < t.cols.size(); ++i) {
        names.append(t.names[i]);
        cols.append(col_to_py(t.cols[i]));
    }
    d["names"] = names;
    d["columns"] = cols;
    d["rows"] = t.rows;
    return d;
}

py::dict result_to_py(ParseResult& r) {
    py::dict out;
    out["measurements"] = table_to_py(r.measurements);
    py::dict recs;
    for (auto& kv : r.records) recs[py::str(kv.first)] = table_to_py(kv.second);
    out["records"] = recs;
    py::dict meta;
    for (auto& kv : r.meta) {
        if (kv.second.is_int) meta[py::str(kv.first)] = kv.second.i;
        else meta[py::str(kv.first)] = kv.second.s;
    }
    out["meta"] = meta;
    const Stats& s = r.stats;
    py::dict st;
    st["counts"] = s.counts;
    st["unknown_types"] = s.unknown_types;
    st["events"] = s.events;
    st["warning_messages"] = s.warning_messages;
    st["warnings"] = s.warnings;
    st["records"] = s.records;
    st["unknown"] = s.unknown;
    st["bytes"] = s.bytes;
    st["bytes_parsed"] = s.bytes_parsed;
    st["truncated"] = s.truncated;
    st["byte_order"] = std::string(1, s.byte_order);
    st["cpu_type"] = s.cpu_type;
    st["stdf_ver"] = s.stdf_ver;
    out["stats"] = st;
    return out;
}

// ---- writer input
struct Holder {
    std::vector<py::object> keep;
    template <class T>
    NumCol<T> num(const py::dict& cols, const char* name, size_t n) {
        NumCol<T> c;
        auto a = py::array_t<T, py::array::c_style | py::array::forcecast>::ensure(cols[name]);
        if (!a || size_t(a.size()) != n) throw Error(std::string("column '") + name + "' has wrong type or length");
        keep.push_back(a);
        c.v = a.data();
        c.valid = valid(cols, name, n);
        return c;
    }
    const uint8_t* valid(const py::dict& cols, const char* name, size_t n) {
        std::string k = std::string(name) + "_valid";
        if (!cols.contains(k.c_str()) || cols[k.c_str()].is_none()) return nullptr;
        auto a = py::array_t<uint8_t, py::array::c_style | py::array::forcecast>::ensure(cols[k.c_str()]);
        if (!a || size_t(a.size()) != n) throw Error("validity for '" + std::string(name) + "' has wrong length");
        keep.push_back(a);
        return a.data();
    }
    StrCol str(const py::dict& cols, const char* name, size_t n) {
        StrCol c;
        std::string ko = std::string(name) + "_offsets", kd = std::string(name) + "_data";
        auto o = py::array_t<int32_t, py::array::c_style | py::array::forcecast>::ensure(cols[ko.c_str()]);
        auto d = py::array_t<uint8_t, py::array::c_style | py::array::forcecast>::ensure(cols[kd.c_str()]);
        if (!o || size_t(o.size()) != n + 1 || !d) throw Error(std::string("string column '") + name + "' malformed");
        const int32_t* off = o.data();
        for (size_t i = 0; i < n; ++i)
            if (off[i] < 0 || off[i + 1] < off[i] || size_t(off[i + 1]) > size_t(d.size()))
                throw Error(std::string("string column '") + name + "' has invalid offsets");
        keep.push_back(o);
        keep.push_back(d);
        c.offsets = off;
        c.data = d.data();
        c.valid = valid(cols, name, n);
        return c;
    }
};

WriteInput make_input(const py::dict& cols, size_t n, Holder& h) {
    WriteInput in;
    in.n = n;
    in.site_num = h.num<uint8_t>(cols, "site_num", n);
    in.part_id = h.str(cols, "part_id", n);
    in.part_ts = h.num<double>(cols, "part_ts", n);
    in.retest = h.num<uint8_t>(cols, "retest", n);
    in.test_num = h.num<uint32_t>(cols, "test_num", n);
    in.test_name = h.str(cols, "test_name", n);
    in.units = h.str(cols, "units", n);
    in.lo_limit = h.num<double>(cols, "lo_limit", n);
    in.hi_limit = h.num<double>(cols, "hi_limit", n);
    in.result = h.num<double>(cols, "result", n);
    in.test_pass = h.num<uint8_t>(cols, "test_pass", n);
    in.part_pass = h.num<uint8_t>(cols, "part_pass", n);
    in.hard_bin = h.num<uint16_t>(cols, "hard_bin", n);
    in.soft_bin = h.num<uint16_t>(cols, "soft_bin", n);
    return in;
}

WriteMeta make_meta(const std::vector<std::pair<std::string, py::object>>& mir,
                    const std::vector<std::pair<std::string, std::string>>& dtrs, bool compact) {
    WriteMeta m;
    m.compact_ptr = compact;
    for (auto& kv : mir) {
        MetaValue v;
        if (py::isinstance<py::int_>(kv.second) && !py::isinstance<py::bool_>(kv.second)) {
            v.is_int = true;
            v.i = kv.second.cast<int64_t>();
        } else if (py::isinstance<py::str>(kv.second)) {
            v.s = kv.second.cast<std::string>();
        } else {
            throw Error("MIR field '" + kv.first + "' must be int or str");
        }
        m.mir.emplace_back(kv.first, std::move(v));
    }
    m.dtrs = dtrs;
    return m;
}

py::dict wstats(const WriteStats& s) {
    py::dict d;
    d["records"] = s.records;
    d["parts"] = s.parts;
    d["ptrs"] = s.ptrs;
    d["bytes"] = s.bytes;
    return d;
}

}  // namespace

PYBIND11_MODULE(_stdf, m) {
    m.doc() = "RadioYield STDF V4 reader/writer (C++17 core)";
    py::register_exception<Error>(m, "StdfError", PyExc_ValueError);

    m.def(
        "parse_file",
        [](const std::string& path, bool records) {
            ParseOptions o;
            o.records = records;
            ParseResult r;
            {
                py::gil_scoped_release nogil;
                r = parse_file(path, o);
            }
            return result_to_py(r);
        },
        py::arg("path"), py::arg("records") = true);

    m.def(
        "parse_bytes",
        [](py::bytes data, bool records) {
            ParseOptions o;
            o.records = records;
            std::string_view sv = data;
            ParseResult r;
            {
                py::gil_scoped_release nogil;
                r = parse_buffer(reinterpret_cast<const uint8_t*>(sv.data()), sv.size(), o);
            }
            return result_to_py(r);
        },
        py::arg("data"), py::arg("records") = true);

    m.def(
        "write_file",
        [](const std::string& path, const py::dict& cols, size_t n,
           const std::vector<std::pair<std::string, py::object>>& mir,
           const std::vector<std::pair<std::string, std::string>>& dtrs, bool big_endian, bool compact) {
            Holder h;
            WriteInput in = make_input(cols, n, h);
            WriteMeta meta = make_meta(mir, dtrs, compact);
            WriteStats s;
            {
                py::gil_scoped_release nogil;
                s = write_file(path, in, meta, big_endian);
            }
            return wstats(s);
        },
        py::arg("path"), py::arg("cols"), py::arg("n"), py::arg("mir"), py::arg("dtrs"), py::arg("big_endian"),
        py::arg("compact_ptr") = true);

    m.def(
        "write_bytes",
        [](const py::dict& cols, size_t n, const std::vector<std::pair<std::string, py::object>>& mir,
           const std::vector<std::pair<std::string, std::string>>& dtrs, bool big_endian, bool compact) {
            Holder h;
            WriteInput in = make_input(cols, n, h);
            WriteMeta meta = make_meta(mir, dtrs, compact);
            std::vector<uint8_t> buf;
            {
                py::gil_scoped_release nogil;
                buf = write_buffer(in, meta, big_endian);
            }
            return py::bytes(reinterpret_cast<const char*>(buf.data()), buf.size());
        },
        py::arg("cols"), py::arg("n"), py::arg("mir"), py::arg("dtrs"), py::arg("big_endian"),
        py::arg("compact_ptr") = true);

    m.def("record_specs", [] {
        static const char* tn[] = {"U1", "U2", "U4", "I1", "I2", "I4", "R4", "R8", "C1", "Cn",
                                   "Bn", "Dn", "B1", "N1", "kxU1", "kxU2", "kxR4", "kxCn", "kxN1", "Vn"};
        py::list out;
        for (const auto& r : record_specs()) {
            py::list fields;
            for (const auto& f : r.fields) fields.append(py::make_tuple(f.name, tn[int(f.type)]));
            out.append(py::make_tuple(r.name, r.typ, r.sub, fields));
        }
        return out;
    });
    m.attr("MEASUREMENT_COLUMNS") = [] {
        py::list l;
        for (const char* c : kMeasurementColumns) l.append(c);
        return l;
    }();
}
