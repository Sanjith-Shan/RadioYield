import numpy as np
import pandas as pd
import pytest

from radioyield.analysis import compare, drift, pat, station
from radioyield.sim import params as P


def test_build_shift_detected_on_exactly_the_shifted_tests(lots):
    lot = lots("build_shift")
    c = compare.compare_builds(lot.frame, "EVT1", "EVT2", n_boot=500)
    assert set(c.shifted) == set(lot.truth["tests"])
    row = c.table.set_index("test_name").loc[P.WIFI_TXP_TESTS[0]]
    assert row["shift_low"] <= lot.truth["shift_db"] + 0.05 and row["shift_high"] >= lot.truth["shift_db"] - 0.05


def test_clean_builds_no_detectable_change(lots):
    c = compare.compare_builds(lots("clean").frame, "EVT1", "EVT2", n_boot=500)
    assert c.shifted == []


def test_practical_threshold(lots):
    c = compare.compare_builds(lots("build_shift").frame, "EVT1", "EVT2", n_boot=300, practical=5.0)
    assert c.shifted == [] and (c.table["verdict"] == "below practical threshold").sum() == 3


def test_golden_correlation_flags_offset_station(lots):
    lot = lots("station_offset")
    g = station.golden_correlation(lot.frame, n_boot=500)
    flagged = g[g["flag"]]
    assert set(flagged["station"]) == {"ST3"}
    assert set(flagged["test_name"]) == set(lot.truth["tests"])
    off = flagged["offset"].mean()
    assert off == pytest.approx(1.5, abs=0.2)


def test_golden_reference_station_mode(lots):
    g = station.golden_correlation(lots("station_offset").frame, reference="ST1", n_boot=300)
    assert "ST1" not in set(g["station"])
    assert set(g[g["flag"]]["station"]) == {"ST3"}


def test_gauge_rr_recovers_components():
    g = np.random.default_rng(5)
    parts, ops, reps = 10, 3, 3
    pv, ev, av = 1.0, 0.1, 0.2
    pe = g.normal(0, pv, parts)
    oe = g.normal(0, av, ops)
    rows = [dict(part_id=f"P{i}", station=f"S{j}", result=pe[i] + oe[j] + g.normal(0, ev))
            for i in range(parts) for j in range(ops) for _ in range(reps)]
    r = station.gauge_rr(pd.DataFrame(rows), -5, 5)
    assert r.repeatability_sd == pytest.approx(ev, rel=0.35)
    assert 0 < r.pct_grr_tol < 30 and r.ndc > 2


def test_gauge_rr_needs_crossed_design():
    df = pd.DataFrame(dict(part_id=["A", "A"], station=["S1", "S1"], result=[1.0, 1.1]))
    assert station.gauge_rr(df) is None


def test_cusum_arl_matches_textbook():
    # Montgomery, Introduction to SQC: k=0.5, h=5 -> ARL0 ~ 465
    assert drift.cusum_arl0(5.0, 0.5) == pytest.approx(465, rel=0.02)
    assert drift.cusum_arl0(drift.cusum_h_for_arl(1e5)) == pytest.approx(1e5, rel=1e-6)


def test_cusum_detects_step_not_noise():
    g = np.random.default_rng(1)
    x = g.normal(0, 1, 400)
    a, _, _ = drift.cusum(x, 0, 1, 0.5, 8)
    assert a is None
    x[200:] += 1.5
    a, _, _ = drift.cusum(x, 0, 1, 0.5, 8)
    assert a is not None and a.direction == "up" and 200 <= a.index < 240


def test_ewma_detects_step():
    x = np.r_[np.zeros(100), np.full(100, 1.0)] + np.random.default_rng(2).normal(0, 1, 200)
    a, _, _ = drift.ewma(x, 0, 1)
    assert a is not None and a.index >= 95


def test_drift_found_on_drifting_station_only(lots):
    lot = lots("station_drift")
    d = drift.drift_table(lot.frame, h=18)
    al = d[d["alarm"]]
    assert len(al) and set(al["station"]) == {"ST2"}
    assert (al["alarm_ts"] > lot.truth["onset_ts"]).all()
    assert set(d[d["status"] != "charted"]["test_name"]) == {"WIFI_RSSI_ERR_CH6", "WIFI_PER_HT20MCS7_M74P5"}


def test_pat_finds_injected_units_and_skips_skewed(lots):
    lot = lots("pat_marginal", seed=3)
    r = pat.pat(lot.frame)
    assert set(r.outlier_units) == set(lot.truth["units"])
    # flagged units pass spec: that is the point
    assert lot.frame[lot.frame["part_id"].isin(r.outlier_units) & ~lot.frame["retest"]]["part_pass"].all()
    skipped = r.limits[r.limits["status"].str.startswith("skipped")]["test_name"].tolist()
    assert "WIFI_PER_HT20MCS7_M74P5" in skipped


def test_pat_limits_never_exceed_spec(lots):
    r = pat.pat(lots("clean").frame)
    lim = r.limits.dropna(subset=["pat_lo"])
    tests = {t.name: t for t in P.TESTS}
    for row in lim.itertuples():
        t = tests[row.test_name]
        if t.lo is not None:
            assert row.pat_lo >= t.lo
        if t.hi is not None:
            assert row.pat_hi <= t.hi
