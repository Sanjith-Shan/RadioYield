import numpy as np
import pandas as pd
import pytest

from radioyield.analysis import capability as C
from radioyield.analysis import yields as Y


def test_cpk_known_answer():
    g = np.random.default_rng(0)
    x = g.normal(10.0, 1.0, 5000)
    r = C.capability(x, 4.0, 19.0)
    # true Cpk = min(9, 6)/3 = 2.0 ; Cp = 15/6 = 2.5
    assert r.status == "reported"
    assert r.cpk == pytest.approx(2.0, abs=0.05) and r.cp == pytest.approx(2.5, abs=0.06)
    assert r.cpk_ci.contains(2.0)


def test_cpk_one_sided():
    x = np.random.default_rng(1).normal(-30, 0.5, 2000)
    r = C.capability(x, None, -27.0)
    assert r.status == "reported" and np.isnan(r.cp) and r.cpk == pytest.approx(2.0, abs=0.08)


@pytest.mark.parametrize("x,why", [
    (np.random.default_rng(2).normal(0, 1, 20), "n=20"),
    (np.round(np.random.default_rng(3).normal(0, 1, 500)), "distinct"),
    (np.r_[np.random.default_rng(4).normal(0, 0.3, 700), np.random.default_rng(5).normal(3, 0.3, 300)], "bimodal"),
    (np.random.default_rng(6).exponential(1, 2000), "non-normal"),
])
def test_cpk_refusals_say_why(x, why):
    r = C.capability(x, -10, 10)
    assert r.status == "refused" and why in r.reason


def test_bimodality_not_fooled_by_heavy_tail():
    x = np.random.default_rng(7).standard_t(5, 3000)
    assert not C.bimodality(x).bimodal


def _frame(rows):
    base = dict(lot_id="L", station="S1", build="B", data_origin="SIMULATED", site_num=1, test_num=1,
                test_name="T", units="dB", lo_limit=0.0, hi_limit=1.0, hard_bin=1, soft_bin=1)
    return pd.DataFrame([{**base, **r} for r in rows])


def test_yield_first_pass_and_final():
    rows = [
        dict(part_id="A", part_ts=1, retest=False, result=0.5, test_pass=True, part_pass=True),
        dict(part_id="B", part_ts=2, retest=False, result=1.5, test_pass=False, part_pass=False, soft_bin=7),
        dict(part_id="B", part_ts=3, retest=True, result=0.9, test_pass=True, part_pass=True),
        dict(part_id="C", part_ts=4, retest=False, result=2.0, test_pass=False, part_pass=False, soft_bin=7),
        dict(part_id="GU-01", part_ts=0, retest=False, result=5.0, test_pass=False, part_pass=False),
    ]
    y = Y.compute_yield(_frame(rows))
    assert y.n_units == 3 and y.n_insertions == 4
    assert y.first_pass_yield == pytest.approx(1 / 3) and y.final_yield == pytest.approx(2 / 3)
    assert y.recovered_by_retest == 1 and y.retested_units == 1
    assert y.pareto_by_test["fails"].tolist() == [2]
    assert y.pareto_by_bin["units"].tolist() == [2]


def test_yield_flags_inconsistent_bins():
    rows = [dict(part_id="A", part_ts=1, retest=False, result=2.0, test_pass=False, part_pass=True)]
    assert Y.compute_yield(_frame(rows)).notes
