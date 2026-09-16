"""Writer -> reader round trips, checked by our reader AND by pystdf (both byte orders)."""
from __future__ import annotations

import pyarrow as pa
import pytest

from _stdf_reference import (
    expected_after_write,
    measurements_from_records,
    pystdf_records,
    semiate_records,
    synth_table,
)
from radioyield.schema import MEASUREMENT_SCHEMA
from radioyield.stdf import read, write
from radioyield.stdf._api import StdfError, read_bytes, write_bytes, write_many

META = {"lot_id": "LOT-A", "station": "ST1", "build": "EVT", "data_origin": "SIMULATED"}


@pytest.mark.parametrize("compact", [True, False], ids=["compact", "full_ptr"])
@pytest.mark.parametrize("byte_order", ["<", ">"])
def test_round_trip_ours_and_pystdf(tmp_path, byte_order, compact):
    pytest.importorskip("pystdf")
    t = synth_table(parts=40, tests=9, seed=1)
    path = tmp_path / f"rt_{'le' if byte_order == '<' else 'be'}.stdf"
    meta = {**META, "distance_m": 3.0, "setup": "OTA", "PART_TYP": "RADIO-X", "JOB_NAM": "rf_sweep"}
    stats = write(path, t, meta, byte_order=byte_order, compact_ptr=compact)
    assert stats["parts"] == 40 and stats["ptrs"] == 360
    if not compact:
        assert read(path).records["PTR"].column("OPT_FLAG").null_count == 0

    f = read(path)
    assert f.stats["byte_order"] == byte_order
    assert f.stats["warnings"] == 0 and not f.stats["truncated"]
    assert f.measurements.schema.equals(MEASUREMENT_SCHEMA)
    want = expected_after_write(t)
    assert f.measurements.equals(want), _diff(f.measurements, want)

    # pystdf decodes the same bytes independently; same mapping must give the same table
    recs = pystdf_records(path)
    ref = measurements_from_records(recs)
    assert ref.equals(f.measurements), _diff(ref, f.measurements)

    # record counts agree with pystdf
    counts = {}
    for name, _ in recs:
        counts[name] = counts.get(name, 0) + 1
    assert counts == f.stats["counts"]

    # meta round trip: MIR keys stay MIR, others become ry.* DTRs (string values)
    assert f.meta["lot_id"] == "LOT-A" and f.meta["station"] == "ST1"
    assert f.meta["LOT_ID"] == "LOT-A" and f.meta["NODE_NAM"] == "ST1"
    assert f.meta["build"] == "EVT" and f.meta["data_origin"] == "SIMULATED"
    assert f.meta["distance_m"] == "3.0" and f.meta["setup"] == "OTA"
    assert f.meta["PART_TYP"] == "RADIO-X" and f.meta["JOB_NAM"] == "rf_sweep"


@pytest.mark.parametrize("byte_order", ["<", ">"])
def test_semiate_reads_our_files(tmp_path, byte_order):
    pytest.importorskip("Semi_ATE.STDF")
    t = synth_table(parts=6, tests=4, seed=2, nulls=False)
    path = tmp_path / "x.stdf"
    write(path, t, META, byte_order=byte_order)
    recs = semiate_records(path)
    ptr = [r for name, r in recs if name == "PTR"]
    assert len(ptr) == t.num_rows
    ours = read(path).records["PTR"].column("RESULT").to_pylist()
    assert [r["RESULT"] for r in ptr] == pytest.approx(ours, rel=0, abs=0)


def test_summary_records(tmp_path):
    t = synth_table(parts=30, tests=5, seed=3, nulls=False)
    path = tmp_path / "s.stdf"
    write(path, t, META)
    f = read(path)
    pcr = f.records["PCR"].to_pylist()[0]
    parts = t.group_by(["part_id", "part_ts"]).aggregate([("part_pass", "min"), ("retest", "max")])
    assert pcr["PART_CNT"] == parts.num_rows == f.stats["counts"]["PIR"] == f.stats["counts"]["PRR"]
    assert pcr["GOOD_CNT"] == sum(parts.column("part_pass_min").to_pylist())
    assert pcr["RTST_CNT"] == sum(parts.column("retest_max").to_pylist())
    hbr = {r["HBIN_NUM"]: r["HBIN_CNT"] for r in f.records["HBR"].to_pylist()}
    assert sum(hbr.values()) == parts.num_rows
    tsr = f.records["TSR"].to_pylist()
    assert len(tsr) == 5 and all(r["EXEC_CNT"] == 30 for r in tsr)
    fails = t.filter(pa.compute.invert(t.column("test_pass")))
    assert sum(r["FAIL_CNT"] for r in tsr) == fails.num_rows
    assert f.stats["counts"]["MRR"] == 1 and f.stats["counts"]["FAR"] == 1


def test_compact_ptrs_use_defaults(tmp_path):
    t = synth_table(parts=10, tests=3, seed=4, nulls=False)
    f = read_bytes(write_bytes(t, META))
    opt = f.records["PTR"].column("OPT_FLAG")
    # first PTR of each test carries OPT_FLAG + limits + units; later ones end after ALARM_ID
    assert opt.null_count == t.num_rows - 3
    assert f.records["PTR"].column("UNITS").null_count == t.num_rows - 3
    assert f.measurements.column("units").null_count == 0


def test_dataframe_input_and_read_write_read(tmp_path):
    t = synth_table(parts=12, tests=4, seed=5)
    df = t.to_pandas()
    a = tmp_path / "a.stdf"
    write(a, df, META)
    f1 = read(a)
    b = tmp_path / "b.stdf"
    write(b, f1.measurements, f1.meta, byte_order=">")  # meta from a read feeds back in
    f2 = read(b)
    assert f2.measurements.equals(f1.measurements)
    assert {k: v for k, v in f2.meta.items() if k not in ("SETUP_T", "START_T")} == {
        k: v for k, v in f1.meta.items() if k not in ("SETUP_T", "START_T")
    }


def test_writer_rejects_mixed_station_and_bad_meta(tmp_path):
    t1 = synth_table(parts=3, tests=2, station="ST1")
    t2 = synth_table(parts=3, tests=2, station="ST2")
    both = pa.concat_tables([t1, t2])
    with pytest.raises(ValueError, match="write_many"):
        write(tmp_path / "x.stdf", both, {k: v for k, v in META.items() if k != "station"})
    with pytest.raises(ValueError, match="disagrees"):
        write(tmp_path / "x.stdf", t1, {**META, "station": "OTHER"})
    with pytest.raises(ValueError, match="data_origin"):
        write(tmp_path / "x.stdf", t1.drop_columns(["data_origin"]).append_column(
            "data_origin", pa.array(["MADE_UP"] * t1.num_rows)), {k: v for k, v in META.items() if k != "data_origin"})
    with pytest.raises(ValueError, match="part_ts"):
        write(tmp_path / "x.stdf", t1, {**META, "part_ts": 1})
    with pytest.raises(ValueError, match="byte_order"):
        write(tmp_path / "x.stdf", t1, META, byte_order="middle")
    long_name = t1.set_column(t1.schema.get_field_index("test_name"), "test_name",
                              pa.array(["x" * 300] * t1.num_rows))
    with pytest.raises(ValueError, match="255"):
        write(tmp_path / "x.stdf", long_name, META)
    assert not (tmp_path / "x.stdf").exists()  # partial output removed


def test_write_many_splits_by_station(tmp_path):
    t1 = synth_table(parts=3, tests=2, station="ST1")
    t2 = synth_table(parts=4, tests=2, station="ST2")
    paths = write_many(tmp_path, pa.concat_tables([t1, t2]), {"operator": "bench"})
    assert len(paths) == 2
    got = {read(p).meta["station"]: read(p) for p in paths}
    assert got["ST1"].measurements.num_rows == t1.num_rows
    assert got["ST2"].measurements.num_rows == t2.num_rows
    assert got["ST2"].meta["operator"] == "bench"


def test_meta_fills_from_table_and_empty_table(tmp_path):
    t = synth_table(parts=2, tests=2)
    f = read_bytes(write_bytes(t))  # no meta: lot/station/build/origin taken from the table
    assert f.meta["lot_id"] == "LOT-A" and f.meta["build"] == "EVT"
    empty = t.slice(0, 0)
    f = read_bytes(write_bytes(empty, META, byte_order=">"))
    assert f.measurements.num_rows == 0 and f.stats["counts"]["MIR"] == 1


def test_not_stdf_raises(tmp_path):
    p = tmp_path / "x.stdf"
    p.write_bytes(b"this is not an stdf file")
    with pytest.raises(StdfError):
        read(p)
    with pytest.raises(ValueError):  # StdfError is a ValueError
        read_bytes(b"")
    with pytest.raises(ValueError):
        read(tmp_path / "missing.stdf")


def _diff(a: pa.Table, b: pa.Table) -> str:
    if a.num_rows != b.num_rows:
        return f"rows {a.num_rows} != {b.num_rows}"
    for name in a.column_names:
        x, y = a.column(name).to_pylist(), b.column(name).to_pylist()
        for i, (u, v) in enumerate(zip(x, y)):
            if u != v and not (u != u and v != v):
                return f"column {name} row {i}: {u!r} != {v!r}"
    return "schemas or chunking differ"
