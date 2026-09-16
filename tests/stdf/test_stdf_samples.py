"""Real public STDF files (stdf/fetch_samples.sh) against pystdf."""
from __future__ import annotations

import glob
import math
import os

import pytest

from _stdf_reference import measurements_from_records, pystdf_records
from radioyield.stdf import read, write
from radioyield.stdf._api import StdfWarning

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLES = sorted(glob.glob(os.path.join(ROOT, "data", "raw", "stdf_samples", "*.std*")))

pytest.importorskip("pystdf")

pytestmark = [
    pytest.mark.data,
    pytest.mark.skipif(not SAMPLES, reason="run stdf/fetch_samples.sh to download the public samples"),
    pytest.mark.filterwarnings("ignore::SyntaxWarning"),
    pytest.mark.filterwarnings("ignore::radioyield.stdf._api.StdfWarning"),
]

PTR_FIELDS = ["TEST_NUM", "HEAD_NUM", "SITE_NUM", "TEST_FLG", "PARM_FLG", "RESULT", "TEST_TXT", "ALARM_ID",
              "OPT_FLAG", "RES_SCAL", "LO_LIMIT", "HI_LIMIT", "UNITS"]


def _same(a, b):
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if b == "" and a is None:  # pystdf returns "" for zero-length Cn; we keep it as ""
        return True
    return a == b


@pytest.fixture(scope="module")
def parsed():
    cache = {}

    def get(path):
        if path not in cache:
            try:
                ref = pystdf_records(path)
                err = None
            except Exception as e:  # report, never hide, a pystdf failure
                ref, err = None, f"{type(e).__name__}: {e}"
            cache[path] = (read(path), ref, err)
        return cache[path]

    return get


@pytest.mark.parametrize("path", SAMPLES, ids=[os.path.basename(p) for p in SAMPLES])
def test_counts_match_pystdf(parsed, path):
    ours, ref, err = parsed(path)
    assert not ours.stats["truncated"]
    assert ours.stats["unknown"] == 0
    if ref is None:
        pytest.skip(f"pystdf failed on this file: {err}")
    counts = {}
    for name, _ in ref:
        counts[name] = counts.get(name, 0) + 1
    assert counts == ours.stats["counts"]


@pytest.mark.parametrize("path", SAMPLES, ids=[os.path.basename(p) for p in SAMPLES])
def test_ptr_values_match_pystdf(parsed, path):
    ours, ref, err = parsed(path)
    if ref is None:
        pytest.skip(f"pystdf failed on this file: {err}")
    ref_ptr = [f for name, f in ref if name == "PTR"]
    if not ref_ptr:
        assert "PTR" not in ours.records
        return
    tbl = ours.records["PTR"]
    assert tbl.num_rows == len(ref_ptr)
    step = max(1, len(ref_ptr) // 2000)  # every PTR for small files, ~2000 evenly spaced for big ones
    cols = {c: tbl.column(c).to_pylist() for c in PTR_FIELDS}
    for i in range(0, len(ref_ptr), step):
        for c in PTR_FIELDS:
            assert _same(cols[c][i], ref_ptr[i][c]), (i, c, cols[c][i], ref_ptr[i][c])


@pytest.mark.parametrize("path", SAMPLES, ids=[os.path.basename(p) for p in SAMPLES])
def test_measurements_match_reference_mapping(parsed, path):
    ours, ref, err = parsed(path)
    if ref is None:
        pytest.skip(f"pystdf failed on this file: {err}")
    want = measurements_from_records(ref)
    assert want.equals(ours.measurements)


@pytest.mark.parametrize("path", [p for p in SAMPLES if "lot2" in p or "8ALX" in p or "a595" in p],
                         ids=lambda p: os.path.basename(p))
def test_public_sample_rewrite(tmp_path, parsed, path):
    """Real data -> our writer -> our reader gives back the same measurement table."""
    ours, _, _ = parsed(path)
    m = ours.measurements
    meta = {k: v for k, v in ours.meta.items()}
    meta["build"] = "public"
    meta["data_origin"] = "PUBLIC_DATASET"
    m = m.set_column(m.schema.get_field_index("build"), "build", __import__("pyarrow").array(["public"] * m.num_rows))
    m = m.set_column(m.schema.get_field_index("data_origin"), "data_origin",
                     __import__("pyarrow").array(["PUBLIC_DATASET"] * m.num_rows))
    for bo in "<>":
        out = tmp_path / f"re{bo == '>'}.stdf"
        write(out, m, meta, byte_order=bo)
        back = read(out)
        assert back.stats["warnings"] == 0
        assert back.measurements.equals(m)


def test_bad_sample_reports_warnings():
    bad = [p for p in SAMPLES if "spreadsheet14" in p]
    if not bad:
        pytest.skip("sample missing")
    with pytest.warns(StdfWarning):
        f = read(bad[0])
    assert f.stats["warnings"] > 0
    assert f.stats["events"]["prr_without_pir"] == 4
