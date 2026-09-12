"""Python face of the C++ STDF V4 reader/writer (``radioyield.stdf._stdf``).

All parsing, record decoding and column building happens in C++. This module
only wraps the C++-owned buffers into pyarrow arrays (zero copy, via
``pa.Array.from_buffers``) and, for writing, hands Arrow buffers to C++ as
numpy views. See ``stdf/README.md`` for the record-level mapping.
"""
from __future__ import annotations

import math
import numbers
import os
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa

from radioyield.schema import COLUMNS, DATA_ORIGINS, MEASUREMENT_SCHEMA
from radioyield.stdf import _stdf

__all__ = ["read", "read_bytes", "write", "write_bytes", "write_many", "StdfFile", "StdfError", "StdfWarning"]

StdfError = _stdf.StdfError  # subclass of ValueError


class StdfWarning(UserWarning):
    """The file was readable but malformed in places (truncated, overrunning fields, ...)."""


_FIXED = {
    "uint8": pa.uint8(),
    "uint16": pa.uint16(),
    "uint32": pa.uint32(),
    "int8": pa.int8(),
    "int16": pa.int16(),
    "int32": pa.int32(),
    "float32": pa.float32(),
    "float64": pa.float64(),
    "bool": pa.bool_(),
}

_SPECS = {name: (typ, sub, fields) for name, typ, sub, fields in _stdf.record_specs()}
_MIR_TYPES = dict(_SPECS["MIR"][2])
REQUIRED_META = ("lot_id", "station", "build", "data_origin")


def _array(d: dict) -> pa.Array:
    t, n, nulls = d["type"], d["length"], d["null_count"]
    validity = None if d["validity"] is None else pa.py_buffer(d["validity"])
    if t == "list":
        child = _array(d["child"])
        return pa.Array.from_buffers(
            pa.list_(child.type), n, [validity, pa.py_buffer(d["offsets"])], null_count=nulls, children=[child]
        )
    if t in ("string", "binary"):
        typ = pa.string() if t == "string" else pa.binary()
        return pa.Array.from_buffers(
            typ, n, [validity, pa.py_buffer(d["offsets"]), pa.py_buffer(d["data"])], null_count=nulls
        )
    return pa.Array.from_buffers(_FIXED[t], n, [validity, pa.py_buffer(d["data"])], null_count=nulls)


def _table(d: dict, schema: pa.Schema | None = None) -> pa.Table:
    arrays = [_array(c) for c in d["columns"]]
    if schema is not None:
        return pa.Table.from_arrays(arrays, schema=schema)
    return pa.Table.from_arrays(arrays, names=list(d["names"]))


@dataclass
class StdfFile:
    """Result of :func:`read`.

    measurements: pa.Table in MEASUREMENT_SCHEMA (one row per PTR inside a PIR/PRR pair)
    records:      record name -> pa.Table of raw decoded fields (missing trailing fields are null)
    meta:         MIR fields (upper case STDF names) + every ``ry.<key>=<value>`` DTR before the
                  first PIR (lower-case key, string value) + lot_id/station aliases
    stats:        record counts, unknown/skipped records, warnings, byte order, truncation
    """

    path: str | None
    measurements: pa.Table
    records: dict[str, pa.Table] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def warnings(self) -> int:
        return int(self.stats.get("warnings", 0))


def _wrap(raw: dict, path: str | None, warn: bool) -> StdfFile:
    f = StdfFile(
        path=path,
        measurements=_table(raw["measurements"], MEASUREMENT_SCHEMA),
        records={name: _table(t) for name, t in raw["records"].items()},
        meta=dict(raw["meta"]),
        stats=dict(raw["stats"]),
    )
    if warn and (f.stats["warnings"] or f.stats["truncated"]):
        first = f.stats["warning_messages"][:1]
        warnings.warn(
            f"{path or '<bytes>'}: {f.stats['warnings']} STDF warnings"
            f"{' (truncated)' if f.stats['truncated'] else ''}; first: {first}",
            StdfWarning,
            stacklevel=3,
        )
    return f


def read(path: str | os.PathLike, records: bool = True, warn: bool = True) -> StdfFile:
    """Parse an STDF V4 file.

    records=False skips the raw per-record tables for the bulky record types
    (PTR, MPR, FTR, PIR, PRR, DTR, GDR); measurements/meta/stats are unchanged.
    Raises StdfError (a ValueError) only when the input is not STDF at all.
    """
    p = os.fspath(path)
    return _wrap(_stdf.parse_file(p, records), p, warn)


def read_bytes(data: bytes, records: bool = True, warn: bool = True) -> StdfFile:
    return _wrap(_stdf.parse_bytes(bytes(data), records), None, warn)


# ----------------------------------------------------------------------- writer
def _as_table(measurements) -> pa.Table:
    if isinstance(measurements, pa.Table):
        t = measurements
    elif isinstance(measurements, pa.RecordBatch):
        t = pa.Table.from_batches([measurements])
    else:  # pandas DataFrame
        from radioyield.schema import to_table

        t = to_table(measurements)
    missing = [c for c in COLUMNS if c not in t.column_names]
    if missing:
        raise ValueError(f"measurement table missing columns: {missing}")
    return t.select(COLUMNS).cast(MEASUREMENT_SCHEMA)


def _flat(col: pa.ChunkedArray) -> pa.Array:
    return col.combine_chunks() if col.num_chunks != 1 else col.chunk(0)


def _valid(arr: pa.Array):
    if arr.null_count == 0:
        return None
    return np.ascontiguousarray(arr.is_valid().to_numpy(zero_copy_only=False), dtype=np.uint8)


def _num(arr: pa.Array, dtype) -> np.ndarray:
    if arr.null_count:
        arr = arr.fill_null(False if pa.types.is_boolean(arr.type) else 0)
    return np.ascontiguousarray(arr.to_numpy(zero_copy_only=False), dtype=dtype)


def _strbufs(arr: pa.Array):
    n = len(arr)
    bufs = arr.buffers()
    if n == 0 or bufs[1] is None:
        off = np.zeros(n + 1, dtype=np.int32)
    else:
        off = np.frombuffer(bufs[1], dtype=np.int32, count=n + 1, offset=arr.offset * 4)
    data = np.frombuffer(bufs[2], dtype=np.uint8) if len(bufs) > 2 and bufs[2] is not None else np.zeros(0, np.uint8)
    return off, data


_NUM_COLS = {
    "site_num": np.uint8,
    "part_ts": np.float64,
    "retest": np.uint8,
    "test_num": np.uint32,
    "lo_limit": np.float64,
    "hi_limit": np.float64,
    "result": np.float64,
    "test_pass": np.uint8,
    "part_pass": np.uint8,
    "hard_bin": np.uint16,
    "soft_bin": np.uint16,
}
_STR_COLS = ("part_id", "test_name", "units")


def _columns(t: pa.Table) -> dict:
    cols: dict[str, Any] = {}
    for name, dt in _NUM_COLS.items():
        a = _flat(t.column(name))
        cols[name] = _num(a, dt)
        cols[name + "_valid"] = _valid(a)
    for name in _STR_COLS:
        a = _flat(t.column(name))
        cols[name + "_offsets"], cols[name + "_data"] = _strbufs(a)
        cols[name + "_valid"] = _valid(a)
    return cols


def _constant(t: pa.Table, name: str):
    vals = [v for v in t.column(name).unique().to_pylist() if v is not None]
    if len(vals) > 1:
        raise ValueError(
            f"table has {len(vals)} distinct {name} values {vals[:5]}; one STDF file is one "
            f"(lot, station, build, data_origin): use write_many()"
        )
    return vals[0] if vals else None


def _fmt(v) -> str:
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _prepare(measurements, meta: dict | None):
    t = _as_table(measurements)
    meta = dict(meta or {})
    for k in REQUIRED_META:
        from_table = _constant(t, k)
        if k not in meta or meta[k] is None:
            if from_table is None:
                raise ValueError(f"meta must contain '{k}' (and the table has no value for it)")
            meta[k] = from_table
        elif from_table is not None and str(from_table) != str(meta[k]):
            raise ValueError(f"meta[{k!r}]={meta[k]!r} disagrees with the table's {k}={from_table!r}")
    if meta["data_origin"] not in DATA_ORIGINS:
        raise ValueError(f"data_origin must be one of {DATA_ORIGINS}, got {meta['data_origin']!r}")
    mir: list[tuple[str, Any]] = [("LOT_ID", str(meta["lot_id"])), ("NODE_NAM", str(meta["station"]))]
    dtrs: list[tuple[str, str]] = [("build", _fmt(meta["build"])), ("data_origin", _fmt(meta["data_origin"]))]
    for k, v in meta.items():
        if k in REQUIRED_META:
            continue
        if k in _MIR_TYPES:
            if k in ("LOT_ID", "NODE_NAM"):
                want = meta["lot_id"] if k == "LOT_ID" else meta["station"]
                if str(v) != str(want):
                    raise ValueError(f"meta[{k!r}]={v!r} disagrees with {want!r}")
                continue
            if _MIR_TYPES[k].startswith("U"):
                if not isinstance(v, numbers.Real) or isinstance(v, bool) or not math.isfinite(v):
                    raise ValueError(f"MIR.{k} must be an integer, got {v!r}")
                mir.append((k, int(v)))
            else:
                mir.append((k, str(v)))
            continue
        if not isinstance(k, str) or not k or "=" in k or k == "part_ts":
            raise ValueError(f"invalid meta key {k!r} for a ry.* DTR")
        dtrs.append((k, _fmt(v)))
    return t, mir, dtrs


def _big(byte_order: str) -> bool:
    if byte_order in ("<", "little"):
        return False
    if byte_order in (">", "big"):
        return True
    raise ValueError(f"byte_order must be '<' or '>', got {byte_order!r}")


def write(
    path: str | os.PathLike, measurements, meta: dict | None = None, byte_order: str = "<", compact_ptr: bool = True
) -> dict:
    """Write one (lot, station) measurement table as an STDF V4 file.

    meta must give lot_id, station, build, data_origin (missing ones are taken
    from the table when it holds a single value). Upper-case keys that are MIR
    field names (PART_TYP, JOB_NAM, START_T, ...) go into the MIR; every other
    key becomes a ``ry.<key>=<value>`` DTR. compact_ptr=True writes later PTRs of
    a test without name/limits/units when they equal the test's first PTR
    (STDF default semantics); False writes every PTR in full. Returns writer stats.
    """
    t, mir, dtrs = _prepare(measurements, meta)
    return dict(
        _stdf.write_file(os.fspath(path), _columns(t), t.num_rows, mir, dtrs, _big(byte_order), compact_ptr)
    )


def write_bytes(measurements, meta: dict | None = None, byte_order: str = "<", compact_ptr: bool = True) -> bytes:
    t, mir, dtrs = _prepare(measurements, meta)
    return _stdf.write_bytes(_columns(t), t.num_rows, mir, dtrs, _big(byte_order), compact_ptr)


def write_many(
    directory: str | os.PathLike,
    measurements,
    meta: dict | None = None,
    byte_order: str = "<",
    name: str = "{lot_id}__{station}__{build}.stdf",
) -> list[str]:
    """Split a table by (lot_id, station, build, data_origin) and write one file each.

    ``meta`` holds extra keys shared by every file (lot_id/station/build/data_origin
    come from the table). Returns the written paths.
    """
    t = _as_table(measurements)
    os.makedirs(directory, exist_ok=True)
    keys = t.select(list(REQUIRED_META)).group_by(list(REQUIRED_META), use_threads=False).aggregate([])
    base = {k: v for k, v in (meta or {}).items() if k not in REQUIRED_META}
    out = []
    import pyarrow.compute as pc

    for row in keys.to_pylist():
        mask = None
        for k in REQUIRED_META:
            v = row[k]
            m = pc.is_null(t.column(k)) if v is None else pc.equal(t.column(k), v)
            mask = m if mask is None else pc.and_(mask, m)
        sub = t.filter(mask)
        safe = {k: "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(row[k])) for k in row}
        p = os.path.join(os.fspath(directory), name.format(**safe))
        if p in out:
            raise ValueError(f"file name collision for {row}; pass a name pattern including data_origin")
        write(p, sub, {**base, **row}, byte_order)
        out.append(p)
    return out
