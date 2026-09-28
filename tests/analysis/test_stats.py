import numpy as np
import pytest

from radioyield.analysis import _stats as S


def test_holm_matches_hand_computation():
    p = np.array([0.01, 0.04, 0.03, 0.20])
    # sorted: .01*4=.04, .03*3=.09, .04*2=.08 -> max(.09,.08)=.09, .20*1=.2
    assert np.allclose(S.holm(p), [0.04, 0.09, 0.09, 0.20])


def test_holm_caps_at_one_and_empty():
    assert S.holm(np.array([0.6, 0.7])).max() == 1.0
    assert S.holm(np.array([])).size == 0


@pytest.mark.parametrize("x,method", [([], "undefined"), ([1.0, 2.0], "observed range"), ([3.0] * 10, "degenerate")])
def test_bootstrap_degenerate_cases_say_so(x, method):
    assert S.bootstrap_ci(np.array(x), np.mean).method.startswith(method)


def test_bootstrap_is_deterministic_and_covers():
    x = np.random.default_rng(1).normal(5, 1, 200)
    a = S.bootstrap_ci(x, np.mean, vectorized=lambda m: m.mean(axis=1))
    b = S.bootstrap_ci(x, np.mean, vectorized=lambda m: m.mean(axis=1))
    assert (a.low, a.high) == (b.low, b.high)
    assert a.contains(5.0)


def test_robust_sigmas_are_normal_consistent():
    x = np.random.default_rng(2).normal(0, 2.0, 200_000)
    assert S.robust_sigma(x) == pytest.approx(2.0, rel=0.01)
    assert S.mad_sigma(x) == pytest.approx(2.0, rel=0.01)


def test_shape_gate():
    g = np.random.default_rng(3)
    assert S.shape_gate(g.normal(0, 1, 2000)) is None
    assert S.shape_gate(np.round(g.normal(0, 1, 2000))).startswith("quantized")
    assert S.shape_gate(g.lognormal(0, 1, 2000)).startswith("skewed")
    # a few far outliers must not make a normal sample look unfit (PAT needs them screened, not refused)
    x = g.normal(0, 1, 2000)
    x[:10] = 9.0
    assert S.shape_gate(x) is None
