"""Physics features from a UWB channel impulse response (CIR) and DW1000 diagnostics.

All CIR features take the *magnitude* CIR (one row per packet, 1 sample ~= 1 ns for the
DW1000 accumulator at 64 MHz PRF, sample period 1/(2*499.2 MHz) = 1.0016 ns) and the
first-path position in that array (fractional index allowed). Delays are in samples (ns).

Why NLOS changes each feature (summary; full text in docs/results/uwb.md):

* ``fp_power_dbm`` / ``rx_power_dbm`` / ``fp_rx_diff_db``: when the direct path is blocked
  the first path is attenuated far more than the total received energy (which is carried by
  reflections), so first-path power drops relative to total power. Decawave's own NLOS
  heuristic is RX - FP > 6 dB (APS006 part 3).
* ``fp_to_total_ratio``: the same quantity in linear form, first-path energy over CIR energy.
* ``rise_time``: time from 10 % to 90 % of the peak on the leading edge. In LOS the first
  path is the strongest and the edge is sharp; in NLOS energy arrives spread out and the
  strongest path comes later, so the edge is slow.
* ``fp_to_peak_delay``: samples between detected first path and the CIR maximum. ~0 in LOS.
* ``mean_excess_delay`` / ``rms_delay_spread``: first and second central moments of the
  power-delay profile relative to the first path. NLOS links are dominated by longer,
  multi-bounce paths, which raises both.
* ``kurtosis``: peakedness of the CIR amplitude window. A single dominant LOS pulse is
  very peaked (high kurtosis); a diffuse NLOS profile is flatter.
* ``max_amp`` / ``energy``: overall strength, lower through obstacles.
* ``peak_snr_db``: peak over noise standard deviation.
* ``e_frac_3ns`` / ``e_frac_10ns``: share of window energy within 3/10 ns after the first path.
* ``range_m``: NLOS links tend to be longer and every NLOS range carries a positive bias.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# DW1000 User Manual (checked v2.05 and v2.11), sec. 4.7.1 (first path power), 4.7.2 (receive power).
# A = 121.74 at 64 MHz PRF in both; 16 MHz PRF constant is 113.77 in v2.11 (115.72 in v2.05).
A_PRF16 = 113.77
A_PRF64 = 121.74


def dw1000_fp_power_dbm(f1, f2, f3, rxpacc, prf_mhz=64):
    """First-path power level, DW1000 UM sec. 4.7.1: 10 log10((F1^2+F2^2+F3^2)/N^2) - A."""
    a = A_PRF64 if np.all(np.asarray(prf_mhz) == 64) else np.where(np.asarray(prf_mhz) == 64, A_PRF64, A_PRF16)
    f1, f2, f3, n = (np.asarray(x, dtype=float) for x in (f1, f2, f3, rxpacc))
    return 10 * np.log10((f1**2 + f2**2 + f3**2) / n**2) - a


def dw1000_rx_power_dbm(cir_pwr, rxpacc, prf_mhz=64):
    """Estimated receive power level, DW1000 UM sec. 4.7.2: 10 log10(C * 2^17 / N^2) - A."""
    a = A_PRF64 if np.all(np.asarray(prf_mhz) == 64) else np.where(np.asarray(prf_mhz) == 64, A_PRF64, A_PRF16)
    c, n = np.asarray(cir_pwr, dtype=float), np.asarray(rxpacc, dtype=float)
    return 10 * np.log10(np.maximum(c, 1e-9) * 2**17 / n**2) - a


def cir_features(cir: np.ndarray, fp_pos: np.ndarray, noise_std: np.ndarray | None = None,
                 pre: int = 5, post: int = 60) -> pd.DataFrame:
    """Features from a magnitude CIR.

    cir: (n, L) non-negative magnitudes (already normalized, e.g. divided by RXPACC).
    fp_pos: (n,) first-path index into each row (may be fractional).
    noise_std: (n,) noise std on the same scale as ``cir`` (optional).
    The analysis window is [fp - pre, fp + post] samples.
    """
    cir = np.asarray(cir, dtype=float)
    n, L = cir.shape
    fp_pos = np.asarray(fp_pos, dtype=float)
    fp_i = np.clip(np.round(fp_pos).astype(int), 0, L - 1)
    offs = np.arange(-pre, post + 1)
    idx = np.clip(fp_i[:, None] + offs[None, :], 0, L - 1)
    w = np.take_along_axis(cir, idx, axis=1)  # (n, W) window aligned to first path
    p = w**2
    tau = offs.astype(float)  # delay relative to first path, samples (~ns)

    energy = p.sum(1)
    peak_k = w.argmax(1)
    max_amp = w.max(1)
    psum = np.maximum(energy, 1e-12)
    # moments of the power-delay profile, delays measured from the first path (tau >= 0 part)
    pos = tau >= 0
    pp = p[:, pos]
    tp = tau[pos]
    ppsum = np.maximum(pp.sum(1), 1e-12)
    mean_excess = (pp * tp).sum(1) / ppsum
    rms_ds = np.sqrt(np.maximum((pp * tp**2).sum(1) / ppsum - mean_excess**2, 0))

    # rise time on the leading edge: last crossing of 10 % before the peak to first crossing of 90 %
    rise = np.empty(n)
    for r in range(n):
        k = peak_k[r]
        seg = w[r, : k + 1]
        m = max_amp[r]
        if m <= 0 or k == 0:
            rise[r] = 0.0
            continue
        below10 = np.nonzero(seg < 0.1 * m)[0]
        t10 = below10[-1] if below10.size else 0
        t90 = np.nonzero(seg >= 0.9 * m)[0][0]
        rise[r] = max(t90 - t10, 0)

    # kurtosis of the amplitude window (Pearson, not excess)
    mu = w.mean(1, keepdims=True)
    var = np.maximum(((w - mu) ** 2).mean(1), 1e-12)
    kurt = ((w - mu) ** 4).mean(1) / var**2

    fp_amp = w[:, pre]
    e3 = p[:, pre : pre + 4].sum(1) / psum
    e10 = p[:, pre : pre + 11].sum(1) / psum
    out = {
        "max_amp": max_amp,
        "energy": energy,
        "fp_amp_cir": fp_amp,
        "fp_to_peak_delay": peak_k - pre,
        "fp_to_peak_amp_ratio": fp_amp / np.maximum(max_amp, 1e-12),
        "rise_time": rise,
        "mean_excess_delay": mean_excess,
        "rms_delay_spread": rms_ds,
        "kurtosis": kurt,
        "e_frac_3ns": e3,
        "e_frac_10ns": e10,
    }
    if noise_std is not None:
        ns = np.maximum(np.asarray(noise_std, dtype=float), 1e-12)
        out["peak_snr_db"] = 20 * np.log10(np.maximum(max_amp, 1e-12) / ns)
        out["fp_snr_db"] = 20 * np.log10(np.maximum(fp_amp, 1e-12) / ns)
    return pd.DataFrame(out)


def diag_features(f1, f2, f3, cir_pwr, rxpacc, range_m, prf_mhz=64) -> pd.DataFrame:
    """DW1000 diagnostic-register features (power levels per the user manual)."""
    fp = dw1000_fp_power_dbm(f1, f2, f3, rxpacc, prf_mhz)
    rx = dw1000_rx_power_dbm(cir_pwr, rxpacc, prf_mhz)
    f1, f2, f3, c = (np.asarray(x, float) for x in (f1, f2, f3, cir_pwr))
    return pd.DataFrame({
        "fp_power_dbm": fp,
        "rx_power_dbm": rx,
        "fp_rx_diff_db": rx - fp,
        # linear first-path to total ratio straight from the register values (same N^2 cancels):
        "fp_to_total_ratio": (f1**2 + f2**2 + f3**2) / np.maximum(c * 2**17, 1e-9),
        "rxpacc": np.asarray(rxpacc, float),
        "range_m": np.asarray(range_m, float),
    })
