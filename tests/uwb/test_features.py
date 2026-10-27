"""Synthetic-fixture tests for the UWB feature extractors (always run, no downloads)."""
import numpy as np
import pytest

from radioyield.uwb.features import (A_PRF16, A_PRF64, cir_features, diag_features,
                                     dw1000_fp_power_dbm, dw1000_rx_power_dbm)
from radioyield.uwb.ranging import decode_cir


def _pulse(L, at, amp, width=1.0):
    t = np.arange(L)
    return amp * np.exp(-0.5 * ((t - at) / width) ** 2)


def _los_nlos_pair(L=200, fp=50):
    los = _pulse(L, fp, 10.0) + _pulse(L, fp + 8, 2.0) + 0.01
    # NLOS: weak first path, strongest energy arriving later and spread out
    nlos = _pulse(L, fp, 1.5) + _pulse(L, fp + 6, 4.0, 2.0) + _pulse(L, fp + 15, 3.0, 4.0) + 0.01
    return np.vstack([los, nlos]), np.array([fp, fp], float)


def test_dw1000_power_formulas_match_user_manual():
    # UM 4.7.1: 10log10((F1^2+F2^2+F3^2)/N^2) - A
    f1, f2, f3, n = 10000.0, 12000.0, 11000.0, 1000.0
    want = 10 * np.log10((f1**2 + f2**2 + f3**2) / n**2) - A_PRF64
    assert dw1000_fp_power_dbm(f1, f2, f3, n, 64) == pytest.approx(want)
    # UM 4.7.2: 10log10(C*2^17/N^2) - A
    c = 9000.0
    assert dw1000_rx_power_dbm(c, n, 16) == pytest.approx(10 * np.log10(c * 2**17 / n**2) - A_PRF16)
    assert A_PRF64 == 121.74 and A_PRF16 == 113.77


def test_cir_features_los_vs_nlos_direction():
    cir, fp = _los_nlos_pair()
    f = cir_features(cir, fp, noise_std=np.array([0.05, 0.05]))
    los, nlos = f.iloc[0], f.iloc[1]
    assert los.fp_to_peak_delay == 0 and nlos.fp_to_peak_delay > 0
    assert nlos.rise_time > los.rise_time
    assert nlos.mean_excess_delay > los.mean_excess_delay
    assert nlos.rms_delay_spread > los.rms_delay_spread
    assert los["kurtosis"] > nlos["kurtosis"]
    assert los.e_frac_3ns > nlos.e_frac_3ns
    assert los.fp_to_peak_amp_ratio == pytest.approx(1.0, abs=1e-6)
    assert los.peak_snr_db == pytest.approx(20 * np.log10(los.max_amp / 0.05))


def test_single_impulse_has_zero_delay_spread():
    cir = np.zeros((1, 100))
    cir[0, 40] = 5.0
    f = cir_features(cir, np.array([40.0]))
    assert f.mean_excess_delay[0] == pytest.approx(0)
    assert f.rms_delay_spread[0] == pytest.approx(0)
    assert f.max_amp[0] == 5.0 and f.energy[0] == 25.0


def test_two_equal_paths_delay_spread():
    # two equal-power paths at 0 and 10 ns: mean excess 5 ns, rms spread 5 ns
    cir = np.zeros((1, 100))
    cir[0, 30] = 1.0
    cir[0, 40] = 1.0
    f = cir_features(cir, np.array([30.0]))
    assert f.mean_excess_delay[0] == pytest.approx(5.0)
    assert f.rms_delay_spread[0] == pytest.approx(5.0)


def test_window_clipped_at_edges():
    cir = np.ones((2, 20))
    f = cir_features(cir, np.array([0.0, 19.0]), pre=5, post=60)
    assert np.isfinite(f.to_numpy()).all()


def test_diag_features_ratio_and_diff():
    d = diag_features([10000], [12000], [11000], [9000], [1000], [3.0], 64)
    assert d.fp_rx_diff_db[0] == pytest.approx(d.rx_power_dbm[0] - d.fp_power_dbm[0])
    # linear ratio and dB difference are the same quantity
    assert 10 * np.log10(d.fp_to_total_ratio[0]) == pytest.approx(-d.fp_rx_diff_db[0])


def test_decode_cir_little_endian_iq():
    # (re=61, im=101), (re=-1, im=2)
    h = "3d006500ffff0200"
    c = decode_cir([h])
    assert c.shape == (1, 2)
    assert c[0, 0] == 61 + 101j and c[0, 1] == -1 + 2j
