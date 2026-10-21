"""Virtual rig: one full RIG.md session through the real collector and fits (SIMULATED)."""
import pandas as pd
import pytest

from radioyield.sim.rig import HallTruth, analyze_session, run_session


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    d = tmp_path_factory.mktemp("rig")
    t = run_session(11, d)
    return d, t, analyze_session(d, t)


def test_every_file_is_labelled_simulated(session):
    d, _, r = session
    origins = {o for f in d.glob("*.steps.parquet") for o in pd.read_parquet(f)["data_origin"].unique()}
    assert origins == {"SIMULATED"}
    assert r["n_runs"] == 37 and r["n_packets"] > 100_000


def test_fits_land_near_the_truth(session):
    _, t, r = session
    assert abs(r["path_loss"]["n"] - t.exponent) < 0.8
    for k in ("wifi", "ble"):
        assert abs(r["tx_linearity"][k]["slope"] - 1) < 0.06
    for k in ("wifi CONDUCTED", "ble CONDUCTED"):
        assert abs(r["waterfall"][k]["error"]) < 3


def test_known_physics_findings(session):
    _, _, r = session
    # a hallway cannot reach the waterfall at the ESP32-C6's lowest powers
    assert r["waterfall"]["wifi OTA"]["fitted"] is False
    assert r["waterfall"]["ble OTA"]["fitted"] is False
    # the deliberate EVT2 -2 dB table change is found; the null orientation change is not
    assert r["build_evt"]["n_flagged"] == r["build_evt"]["n_tests"] == 9
    assert r["build_evt"]["median_shift_db"] == pytest.approx(-2.0, abs=0.5)
    assert r["build_orient"]["n_flagged"] == 0


def test_as_written_plans_fail_the_way_the_doc_says(tmp_path):
    t = run_session(12, tmp_path, as_written=True)
    r = analyze_session(tmp_path, t)
    assert r["waterfall"]["wifi CONDUCTED"]["fitted"] is False   # 80 dB chain is too weak
    assert r["build_evt"]["min_p_holm"] > 0.05 or r["build_evt"]["n_flagged"] <= 1


def test_truth_is_deterministic():
    a, b = HallTruth(seed=5), HallTruth(seed=5)
    assert a.rssi_bias_db == b.rssi_bias_db and a.static_db("4m", "wifi", 6) == b.static_db("4m", "wifi", 6)
    assert a.level_at_per("wifi") == pytest.approx(-99.445, abs=0.01)


def test_rig_report_renders(session, tmp_path):
    from radioyield.report.rig_html import render_rig
    d, _, _ = session
    html = render_rig(d)
    assert "SIMULATED RIG SESSION" in html and "Known-answer check" in html
    assert "Build over build: EVT2 vs EVT1" in html and "ORIENT_B vs ORIENT_A" in html
