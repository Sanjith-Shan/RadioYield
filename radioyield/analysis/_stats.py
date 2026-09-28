"""Statistics primitives shared by every detector.

Conventions (same discipline as Dyno's stats layer):

* Every interval is a percentile bootstrap with a **fixed seed**, so the same
  data always produces the same interval and therefore the same verdict. A gate
  that flips between two runs on identical data gets muted, and a muted gate
  catches nothing.
* Every interval carries a ``method`` string saying what it actually is,
  including the degenerate cases (too few samples, zero variance), so a report
  never shows a fabricated interval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

SEED = 0x5EED
DEFAULT_N_BOOT = 4000


def rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float
    high: float
    level: float
    method: str

    def contains(self, x: float) -> bool:
        return bool(self.low <= x <= self.high)

    def excludes(self, x: float) -> bool:
        return not np.isnan(self.low) and not self.contains(x)

    def as_dict(self) -> dict:
        return {
            "estimate": self.estimate,
            "low": self.low,
            "high": self.high,
            "level": self.level,
            "method": self.method,
        }


def bootstrap_ci(
    x: np.ndarray,
    stat: Callable[[np.ndarray], float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    level: float = 0.95,
    seed: int = SEED,
    vectorized: Callable[[np.ndarray], np.ndarray] | None = None,
) -> Interval:
    """Percentile bootstrap of ``stat`` over a 1-D sample.

    ``vectorized`` (optional) computes the statistic along axis=1 of a
    (n_boot, n) matrix of resamples; it is much faster for mean/median/std.
    """
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = x.size
    if n == 0:
        return Interval(np.nan, np.nan, np.nan, level, "undefined (no samples)")
    est = float(stat(x))
    if n < 3:
        return Interval(est, float(x.min()), float(x.max()), level, f"observed range (n={n}, too few to bootstrap)")
    if np.all(x == x[0]):
        return Interval(est, est, est, level, "degenerate (zero variance)")
    g = rng(seed)
    idx = g.integers(0, n, size=(n_boot, n))
    if vectorized is not None:
        boots = vectorized(x[idx])
    else:
        boots = np.array([stat(x[i]) for i in idx])
    boots = boots[np.isfinite(boots)]
    a = (1 - level) / 2
    lo, hi = np.quantile(boots, [a, 1 - a])
    return Interval(est, float(lo), float(hi), level, f"percentile bootstrap, {n_boot} resamples")


def bootstrap_two_sample(
    a: np.ndarray,
    b: np.ndarray,
    stat: Callable[[np.ndarray, np.ndarray], float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    level: float = 0.95,
    seed: int = SEED,
) -> Interval:
    """Percentile bootstrap of a two-sample statistic, resampling each group independently."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if a.size == 0 or b.size == 0:
        return Interval(np.nan, np.nan, np.nan, level, "undefined (empty group)")
    est = float(stat(a, b))
    if a.size < 3 or b.size < 3:
        return Interval(est, np.nan, np.nan, level, f"not bootstrapped (n={a.size},{b.size} too few)")
    g = rng(seed)
    ia = g.integers(0, a.size, size=(n_boot, a.size))
    ib = g.integers(0, b.size, size=(n_boot, b.size))
    boots = np.array([stat(a[i], b[j]) for i, j in zip(ia, ib)])
    q = (1 - level) / 2
    lo, hi = np.quantile(boots, [q, 1 - q])
    return Interval(est, float(lo), float(hi), level, f"percentile bootstrap (groups resampled independently), {n_boot} resamples")


def robust_sigma(x: np.ndarray) -> float:
    """IQR / 1.349: the normal-consistent robust sigma (equals sigma for normal data)."""
    q1, q3 = np.nanpercentile(x, [25, 75])
    return float((q3 - q1) / 1.349)


def mad_sigma(x: np.ndarray) -> float:
    """1.4826 * MAD: the other normal-consistent robust sigma."""
    med = np.nanmedian(x)
    return float(1.4826 * np.nanmedian(np.abs(x - med)))


def hodges_lehmann_shift(a: np.ndarray, b: np.ndarray, max_pairs: int = 400_000) -> float:
    """Median of all pairwise differences b_j - a_i (a robust location shift).

    For large groups, a deterministic subsample keeps it O(max_pairs).
    """
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    if a.size * b.size > max_pairs:
        g = rng(SEED + 1)
        k = int(np.sqrt(max_pairs))
        a = a[g.choice(a.size, min(k, a.size), replace=False)]
        b = b[g.choice(b.size, min(k, b.size), replace=False)]
    return float(np.median(np.subtract.outer(b, a)))


def holm(pvalues: np.ndarray) -> np.ndarray:
    """Holm-Bonferroni step-down adjusted p-values (controls FWER, any dependence)."""
    p = np.asarray(pvalues, float)
    m = p.size
    if m == 0:
        return p
    order = np.argsort(p)
    adj = np.empty(m)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * p[i])
        adj[i] = min(1.0, running)
    return adj


def shape_gate(x: np.ndarray, *, min_distinct: int = 10, max_octile_skew: float = 0.2) -> str | None:
    """Reason a sample is unfit for normal-theory limits, or None if it is fit.

    Uses quantiles only, so a handful of genuine outliers (exactly what PAT and
    drift charts exist to catch) cannot make the gate refuse the test.
    * quantized: fewer than ``min_distinct`` distinct values
    * skewed: |octile skewness| > ``max_octile_skew`` (0 for any symmetric
      distribution; ~0.0 for normal, ~0.5 for a binomial PER near 1%)
    """
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    nd = np.unique(x).size
    if nd < min_distinct:
        return f"quantized ({nd} distinct values)"
    q1, q2, q3 = np.percentile(x, [12.5, 50, 87.5])
    if q3 == q1:
        return "degenerate central spread"
    os_ = ((q3 - q2) - (q2 - q1)) / (q3 - q1)
    if abs(os_) > max_octile_skew:
        return f"skewed (octile skewness {os_:+.2f})"
    return None
