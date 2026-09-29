"""Process capability (Cp, Cpk) with honest refusals, and a bimodality check.

Cpk is a statement about a *normal* process: it converts the distance from the
mean to the nearest limit into "how many sigmas", and that is only a defect
rate if the tails are Gaussian. So this module refuses to report Cpk, and says
why, when:

* fewer than ``MIN_N`` (30) parts -- the sigma estimate is too loose;
* the data are quantized to a handful of values (e.g. a PER from 100 packets);
* the distribution is bimodal (two populations: the lot is mixed, and one Cpk
  describes neither);
* the data are clearly non-normal: Shapiro-Wilk rejects at p < 0.001 *and* the
  shape is materially off (|skew| > 0.5 or |excess kurtosis| > 1). Both are
  required because at n in the thousands Shapiro-Wilk rejects harmless
  departures that do not change Cpk.

The interval is a percentile bootstrap (fixed seed) over parts.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from ._stats import DEFAULT_N_BOOT, SEED, Interval, rng
from .yields import GOLDEN_PREFIX, RUN_KEY, as_frame, product_rows

MIN_N = 30
MIN_DISTINCT = 10


@dataclass
class Bimodality:
    bimodal: bool
    delta_bic: float      # BIC(1 component) - BIC(2 components); > 0 favours two
    ashman_d: float       # separation of the two fitted modes in pooled sigmas
    minor_weight: float
    means: tuple[float, float]


def bimodality(x: np.ndarray, seed: int = SEED) -> Bimodality:
    """Two-component Gaussian mixture vs one, judged by BIC *and* separation.

    Bimodal when BIC prefers two components by > 10 (very strong evidence on
    Kass-Raftery's scale), Ashman's D > 2 (modes clearly separated; a mere
    heavy tail gives D < 2) and the minor mode holds >= 5% of parts.
    """
    from sklearn.mixture import GaussianMixture

    x = np.asarray(x, float)
    x = x[np.isfinite(x)].reshape(-1, 1)
    if x.shape[0] < MIN_N or np.unique(x).size < MIN_DISTINCT:
        return Bimodality(False, np.nan, np.nan, np.nan, (np.nan, np.nan))
    g1 = GaussianMixture(1, random_state=seed).fit(x)
    g2 = GaussianMixture(2, random_state=seed, n_init=3).fit(x)
    dbic = float(g1.bic(x) - g2.bic(x))
    m = g2.means_.ravel()
    s = np.sqrt(g2.covariances_.ravel())
    w = g2.weights_.ravel()
    d = float(np.sqrt(2) * abs(m[0] - m[1]) / np.sqrt(s[0] ** 2 + s[1] ** 2))
    minor = float(w.min())
    return Bimodality(dbic > 10 and d > 2 and minor >= 0.05, dbic, d, minor, (float(m[0]), float(m[1])))


@dataclass
class CapabilityResult:
    test_num: int
    test_name: str
    units: str
    n: int
    mean: float
    sigma: float
    lo_limit: float
    hi_limit: float
    cp: float
    cpk: float
    cpk_ci: Interval | None
    status: str          # "reported" | "refused"
    reason: str

    def row(self) -> dict:
        d = {k: getattr(self, k) for k in
             ("test_num", "test_name", "units", "n", "mean", "sigma", "lo_limit", "hi_limit", "cp", "cpk", "status", "reason")}
        d["cpk_low"] = self.cpk_ci.low if self.cpk_ci else np.nan
        d["cpk_high"] = self.cpk_ci.high if self.cpk_ci else np.nan
        return d


def _cpk(mean, sd, lo, hi):
    parts = []
    if np.isfinite(hi):
        parts.append((hi - mean) / (3 * sd))
    if np.isfinite(lo):
        parts.append((mean - lo) / (3 * sd))
    return np.minimum.reduce(parts) if parts else np.full_like(np.asarray(mean, float), np.nan)


def capability(x: np.ndarray, lo: float, hi: float, *, test_num: int = 0, test_name: str = "",
               units: str = "", n_boot: int = DEFAULT_N_BOOT, seed: int = SEED) -> CapabilityResult:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    lo = np.nan if lo is None else float(lo)
    hi = np.nan if hi is None else float(hi)
    n = x.size
    mean = float(x.mean()) if n else np.nan
    sd = float(x.std(ddof=1)) if n > 1 else np.nan

    def refuse(reason):
        return CapabilityResult(test_num, test_name, units, n, mean, sd, lo, hi, np.nan, np.nan, None, "refused", reason)

    if not (np.isfinite(lo) or np.isfinite(hi)):
        return refuse("no spec limits")
    if n < MIN_N:
        return refuse(f"n={n} < {MIN_N}: sigma too uncertain for a capability index")
    if sd == 0 or not np.isfinite(sd):
        return refuse("zero variance")
    if np.unique(x).size < MIN_DISTINCT:
        return refuse(f"only {np.unique(x).size} distinct values: quantized data, Cpk's normal tails do not apply")
    bm = bimodality(x, seed)
    if bm.bimodal:
        return refuse(f"bimodal (two populations near {bm.means[0]:.2f} and {bm.means[1]:.2f}, "
                      f"Ashman D={bm.ashman_d:.1f}, minor mode {bm.minor_weight:.0%}): split the lot first")
    sample = x if n <= 5000 else rng(seed).choice(x, 5000, replace=False)
    p_sw = float(stats.shapiro(sample).pvalue)
    skew = float(stats.skew(x))
    kurt = float(stats.kurtosis(x))
    if p_sw < 1e-3 and (abs(skew) > 0.5 or abs(kurt) > 1):
        return refuse(f"clearly non-normal (Shapiro-Wilk p={p_sw:.1e}, skew={skew:.2f}, excess kurtosis={kurt:.2f})")

    cp = float((hi - lo) / (6 * sd)) if np.isfinite(lo) and np.isfinite(hi) else np.nan
    cpk = float(_cpk(mean, sd, lo, hi))
    g = rng(seed)
    idx = g.integers(0, n, size=(n_boot, n))
    xs = x[idx]
    boots = _cpk(xs.mean(axis=1), xs.std(axis=1, ddof=1), lo, hi)
    lo_ci, hi_ci = np.quantile(boots, [0.025, 0.975])
    ci = Interval(cpk, float(lo_ci), float(hi_ci), 0.95, f"percentile bootstrap, {n_boot} resamples")
    one_sided = "" if np.isfinite(cp) else " (one-sided: single spec limit)"
    return CapabilityResult(test_num, test_name, units, n, mean, sd, lo, hi, cp, cpk, ci, "reported", "ok" + one_sided)


def capability_table(data, *, golden_prefix: str = GOLDEN_PREFIX, first_insertion_only: bool = True,
                     n_boot: int = DEFAULT_N_BOOT) -> pd.DataFrame:
    """Cpk for every test in a lot, over product units (first insertion by default:
    retests are a biased subsample of the failures)."""
    df = product_rows(as_frame(data), golden_prefix)
    if first_insertion_only:
        df = df[~df["retest"]]
    rows = []
    for (num, name), g in df.groupby(["test_num", "test_name"], observed=True, sort=True):
        lo = g["lo_limit"].dropna()
        hi = g["hi_limit"].dropna()
        r = capability(g["result"].to_numpy(), lo.iloc[0] if len(lo) else np.nan, hi.iloc[0] if len(hi) else np.nan,
                       test_num=int(num), test_name=str(name), units=str(g["units"].iloc[0]), n_boot=n_boot)
        rows.append(r.row())
    return pd.DataFrame(rows)


def bimodality_table(data, *, golden_prefix: str = GOLDEN_PREFIX) -> pd.DataFrame:
    df = product_rows(as_frame(data), golden_prefix)
    df = df[~df["retest"]]
    rows = []
    for (num, name), g in df.groupby(["test_num", "test_name"], observed=True, sort=True):
        b = bimodality(g["result"].to_numpy())
        rows.append({"test_num": int(num), "test_name": str(name), "bimodal": b.bimodal, "delta_bic": b.delta_bic,
                     "ashman_d": b.ashman_d, "minor_weight": b.minor_weight, "mode_a": b.means[0], "mode_b": b.means[1]})
    return pd.DataFrame(rows)
