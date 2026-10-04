import numpy as np
import pytest

from radioyield.analysis import rf


def test_waterfall_recovers_level_at_10pct():
    g = np.random.default_rng(0)
    l50, s = -95.0, 0.8
    level = np.arange(-100, -88, 1.0)
    p = 1 / (1 + np.exp((level - l50) / s))
    n = np.full(level.size, 2000)
    lost = g.binomial(n, p)
    w = rf.per_waterfall(level, n, lost, n_boot=300)
    truth = l50 + s * np.log(9)
    assert w.level_at_target.estimate == pytest.approx(truth, abs=0.2)
    assert w.level_at_target.contains(truth) and w.bracketed


def test_waterfall_warns_on_extrapolation():
    level = np.array([-80.0, -79, -78, -77])
    w = rf.per_waterfall(level, [500] * 4, [0, 1, 0, 0], n_boot=50)
    assert not w.bracketed and w.notes


def test_path_loss_recovers_exponent():
    g = np.random.default_rng(1)
    d = np.repeat([1, 2, 4, 8, 16, 24], 200).astype(float)
    shadow = dict(zip([1, 2, 4, 8, 16, 24], g.normal(0, 1.5, 6)))
    rssi = -40 - 10 * 3.0 * np.log10(d) + np.array([shadow[x] for x in d]) + g.normal(0, 3, d.size)
    f = rf.path_loss_fit(d, np.round(rssi))
    assert f.exponent.contains(3.0)
    assert f.closest and all(len(c) == 3 for c in f.closest)


def test_tx_linearity_flags_clamping():
    c = np.arange(0, 21, 2.0)
    m = -50 + c
    m[c > 18] = -50 + 18   # power table clamps
    f = rf.tx_linearity(c, m + np.random.default_rng(2).normal(0, 0.05, c.size))
    assert f.slope.estimate < 1.0
    perfect = rf.tx_linearity(c, -50 + c)
    assert perfect.slope.estimate == pytest.approx(1.0) and not perfect.flagged_steps


def test_waterfall_refuses_when_no_step_is_on_the_slope():
    with pytest.raises(ValueError, match="never reached"):
        rf.per_waterfall([-60.0, -55, -50], [300] * 3, [0, 0, 0])
