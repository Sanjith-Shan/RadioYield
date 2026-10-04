"""RF fits: PER waterfall, log-distance path loss, TX power control linearity.

These take aggregated per-step data (the collector's per-packet Parquet reduces
to it) and return typed results with intervals. Units: dBm, dB, metres.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import optimize, stats

from ._stats import SEED, Interval, rng

# ---------------------------------------------------------------- PER waterfall


@dataclass
class Waterfall:
    l50: float
    slope_db: float              # logistic scale s (dB); PER falls from 90% to 10% over 2*s*ln9 dB
    level_at_target: Interval    # received level at target PER
    target_per: float
    bracketed: bool              # target PER lies inside the measured PER range
    n_steps: int
    n_packets: int
    notes: list[str] = field(default_factory=list)

    def per(self, level):
        return 1 / (1 + np.exp((np.asarray(level, float) - self.l50) / self.slope_db))


def _fit_logistic(level, n, lost):
    def nll(p):
        l50, log_s = p
        s = np.exp(log_s)
        q = 1 / (1 + np.exp(np.clip((level - l50) / s, -50, 50)))
        q = np.clip(q, 1e-12, 1 - 1e-12)
        return -np.sum(lost * np.log(q) + (n - lost) * np.log(1 - q))

    per = lost / n
    i = int(np.argmin(np.abs(per - 0.5)))
    res = optimize.minimize(nll, x0=[level[i], np.log(1.0)], method="Nelder-Mead",
                            options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 4000})
    return float(res.x[0]), float(np.exp(res.x[1]))


def per_waterfall(level_dbm, n_sent, n_lost, *, target_per: float = 0.10, n_boot: int = 1000,
                  seed: int = SEED) -> Waterfall:
    """Binomial maximum-likelihood logistic fit of PER vs received level.

    Interval: parametric-free bootstrap -- each step's lost count is redrawn
    from Binomial(n, observed PER of that step) and the curve refitted.
    """
    level = np.asarray(level_dbm, float)
    n = np.asarray(n_sent, float)
    lost = np.asarray(n_lost, float)
    keep = n > 0
    level, n, lost = level[keep], n[keep], lost[keep]
    notes = []
    if level.size < 3:
        raise ValueError("need at least 3 steps with packets")
    per_obs = lost / n
    if not np.any((per_obs > 0) & (per_obs < 1)):
        raise ValueError("no step has a PER strictly between 0% and 100%: the data never reached the "
                         "waterfall, so there is nothing to fit (go lower in level)")
    l50, s = _fit_logistic(level, n, lost)
    shift = s * np.log(1 / target_per - 1)
    est = l50 + shift
    g = rng(seed)
    p_obs = lost / n
    boots = []
    for _ in range(n_boot):
        lb = g.binomial(n.astype(int), p_obs)
        try:
            a, b = _fit_logistic(level, n, lb)
            boots.append(a + b * np.log(1 / target_per - 1))
        except Exception:  # pragma: no cover - optimizer failure on a pathological resample
            continue
    boots = np.asarray(boots)
    lo, hi = np.quantile(boots, [0.025, 0.975])
    per = lost / n
    bracketed = bool(per.min() <= target_per <= per.max())
    if not bracketed:
        notes.append(f"target PER {target_per:.0%} lies outside the measured PER range "
                     f"[{per.min():.1%}, {per.max():.1%}]: the level is an extrapolation")
    if (per > 0.02).sum() < 2 or (per < 0.98).sum() < 2:
        notes.append("fewer than two steps on the waterfall slope: the slope is poorly constrained")
    return Waterfall(l50, s, Interval(est, float(lo), float(hi), 0.95, f"binomial bootstrap, {boots.size} refits"),
                     target_per, bracketed, int(level.size), int(n.sum()), notes)


# ---------------------------------------------------------------- path loss

# Reference values, verified from the ITU PDFs (see docs/baselines/references.md).
# ITU-R P.1238-10 (08/2019) Table 2, power loss coefficient N = 10 n at 2.4 GHz.
P1238_10_N_24GHZ = {"residential": 2.8, "office": 3.0}
# ITU-R P.1238-13 (09/2025) Table 2 site-general model, distance coefficient alpha
# (L = 10 alpha log10 d + beta + 10 gamma log10 f), sigma in dB.
P1238_13_SITE_GENERAL = {
    "office LoS": (1.47, 3.68), "office NLoS": (2.39, 5.01),
    "corridor LoS": (1.57, 3.77), "corridor NLoS": (2.78, 7.58),
    "industrial LoS": (2.27, 2.62), "industrial NLoS": (2.80, 5.70),
    "conference LoS": (1.56, 2.92), "conference NLoS": (1.40, 3.33),
}
FREE_SPACE_N = 2.0


@dataclass
class PathLoss:
    exponent: Interval          # n in RSSI(d) = A - 10 n log10(d / 1 m)
    intercept_dbm: float        # A: RSSI at 1 m (includes TX power and both antenna gains)
    shadowing_sigma_db: float   # std of per-position residuals (fast fading averaged out per position)
    n_positions: int
    residuals: np.ndarray
    closest: list[tuple[str, float, bool]]   # (reference, n_ref, inside our CI)
    notes: list[str] = field(default_factory=list)


def path_loss_fit(distance_m, rssi_dbm) -> PathLoss:
    """Log-distance fit on per-position medians (one point per tape mark).

    Pass one RSSI value per packet with its distance; per-packet scatter is fast
    fading plus RSSI quantization, so the shadowing sigma is computed on
    per-position medians, not per packet.
    """
    d = np.asarray(distance_m, float)
    r = np.asarray(rssi_dbm, float)
    pos = np.unique(d)
    med = np.array([np.median(r[d == p]) for p in pos])
    x = 10 * np.log10(pos)
    if pos.size < 3:
        raise ValueError("need >= 3 distinct distances")
    lr = stats.linregress(x, med)
    n_hat = -lr.slope
    tcrit = stats.t.ppf(0.975, pos.size - 2)
    ci = Interval(float(n_hat), float(n_hat - tcrit * lr.stderr), float(n_hat + tcrit * lr.stderr), 0.95,
                  f"OLS t-interval on {pos.size} per-position medians")
    resid = med - (lr.intercept + lr.slope * x)
    sigma = float(np.std(resid, ddof=2)) if pos.size > 2 else np.nan
    refs = {"free space": FREE_SPACE_N}
    refs.update({f"P.1238-10 {k} (N/10)": v for k, v in P1238_10_N_24GHZ.items()})
    refs.update({f"P.1238-13 {k} (alpha)": v[0] for k, v in P1238_13_SITE_GENERAL.items()})
    closest = sorted(((k, v, ci.contains(v)) for k, v in refs.items()), key=lambda t: abs(t[1] - n_hat))[:4]
    notes = []
    if pos.size < 5:
        notes.append(f"only {pos.size} positions: the exponent interval is wide by construction")
    return PathLoss(ci, float(lr.intercept), sigma, int(pos.size), resid, closest, notes)


# ---------------------------------------------------------------- TX power linearity


@dataclass
class TxLinearity:
    slope: Interval             # dB measured per dB commanded (ideal 1.0)
    intercept: float
    residuals_db: np.ndarray
    max_abs_residual_db: float
    step_errors_db: np.ndarray  # measured step - commanded step, per adjacent pair
    flagged_steps: list[float]  # commanded levels with |residual| > tol
    notes: list[str] = field(default_factory=list)


def tx_linearity(commanded_dbm, measured_dbm, *, tol_db: float = 1.0) -> TxLinearity:
    c = np.asarray(commanded_dbm, float)
    m = np.asarray(measured_dbm, float)
    order = np.argsort(c)
    c, m = c[order], m[order]
    if c.size < 3:
        raise ValueError("need >= 3 power steps")
    lr = stats.linregress(c, m)
    tcrit = stats.t.ppf(0.975, c.size - 2)
    resid = m - (lr.intercept + lr.slope * c)
    steps = np.diff(m) - np.diff(c)
    flagged = c[np.abs(resid) > tol_db].tolist()
    notes = []
    if flagged:
        notes.append(f"{len(flagged)} step(s) deviate > {tol_db} dB from the linear fit; look for clamping at the "
                     "top of the range or a coarse power table")
    return TxLinearity(Interval(float(lr.slope), float(lr.slope - tcrit * lr.stderr),
                                float(lr.slope + tcrit * lr.stderr), 0.95, f"OLS t-interval, {c.size} steps"),
                       float(lr.intercept), resid, float(np.max(np.abs(resid))), steps, flagged, notes)
