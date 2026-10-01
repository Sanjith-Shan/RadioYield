"""Build-over-build comparison (EVT1 vs EVT2, firmware A vs B, ...).

Per test, over product units' first insertions:

* **Location**: Mann-Whitney U (two-sided; no normality assumption) and the
  shift in medians (B - A) with a percentile-bootstrap 95% interval. Cliff's
  delta as a scale-free effect size.
* **Spread**: Brown-Forsythe (median-centred Levene) and the ratio of robust
  sigmas.
* **Multiplicity**: Holm correction across all tests in the comparison, separately
  for location and spread. Twenty tests at alpha=0.05 without correction would
  flag one test by chance in most comparisons.

Verdict per test:

* ``shift``: Holm-adjusted p < alpha **and** the shift interval excludes 0
  (and, if a practical threshold is given, |shift| >= it).
* ``below practical threshold``: statistically detectable but smaller than the
  threshold the caller says matters.
* ``no detectable change``: otherwise. This is a statement about the data, not
  a proof of equality; the interval says how large a shift could still hide.

The module never says "regression": whether up is bad depends on the test, and
that is the reader's call with the interval in hand.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from ._stats import DEFAULT_N_BOOT, SEED, holm, rng, robust_sigma
from .yields import GOLDEN_PREFIX, as_frame, product_rows


@dataclass
class BuildComparison:
    build_a: str
    build_b: str
    alpha: float
    table: pd.DataFrame

    @property
    def shifted(self) -> list[str]:
        return self.table.loc[self.table["verdict"] == "shift", "test_name"].tolist()


def _median_shift_ci(a, b, n_boot, seed):
    g = rng(seed)
    ma = np.median(a[g.integers(0, a.size, (n_boot, a.size))], axis=1)
    mb = np.median(b[g.integers(0, b.size, (n_boot, b.size))], axis=1)
    return np.quantile(mb - ma, [0.025, 0.975])


def compare_builds(data, build_a: str, build_b: str, *, alpha: float = 0.05,
                   practical: dict[str, float] | float | None = None,
                   golden_prefix: str = GOLDEN_PREFIX, n_boot: int = DEFAULT_N_BOOT,
                   seed: int = SEED) -> BuildComparison:
    df = product_rows(as_frame(data), golden_prefix)
    df = df[~df["retest"]]
    A = df[df["build"] == build_a]
    B = df[df["build"] == build_b]
    if A.empty or B.empty:
        raise ValueError(f"need both builds present; got {sorted(df['build'].unique())}")
    rows = []
    for (num, name), ga in A.groupby(["test_num", "test_name"], observed=True, sort=True):
        gb = B[B["test_num"] == num]
        a = ga["result"].to_numpy(float)
        b = gb["result"].to_numpy(float)
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        row = {"test_num": int(num), "test_name": str(name), "units": str(ga["units"].iloc[0]),
               "n_a": a.size, "n_b": b.size}
        if a.size < 3 or b.size < 3:
            row.update(verdict="insufficient data", p_location=np.nan, p_spread=np.nan)
            rows.append(row)
            continue
        u = stats.mannwhitneyu(b, a, alternative="two-sided")
        shift = float(np.median(b) - np.median(a))
        lo, hi = _median_shift_ci(a, b, n_boot, seed)
        sa, sb = robust_sigma(a), robust_sigma(b)
        pooled = np.sqrt((sa ** 2 + sb ** 2) / 2)
        try:
            p_spread = float(stats.levene(a, b, center="median").pvalue)
        except ValueError:
            p_spread = np.nan
        row.update(median_a=float(np.median(a)), median_b=float(np.median(b)), shift=shift,
                   shift_low=float(lo), shift_high=float(hi),
                   shift_sigmas=shift / pooled if pooled > 0 else np.nan,
                   cliffs_delta=float(2 * u.statistic / (a.size * b.size) - 1),
                   p_location=float(u.pvalue), robust_sigma_a=sa, robust_sigma_b=sb,
                   sigma_ratio=sb / sa if sa > 0 else np.nan, p_spread=p_spread)
        rows.append(row)
    t = pd.DataFrame(rows)
    ok = t["p_location"].notna()
    t["p_location_holm"] = np.nan
    t.loc[ok, "p_location_holm"] = holm(t.loc[ok, "p_location"].to_numpy())
    oks = t["p_spread"].notna()
    t["p_spread_holm"] = np.nan
    t.loc[oks, "p_spread_holm"] = holm(t.loc[oks, "p_spread"].to_numpy())

    def verdict(r):
        if r.get("verdict") == "insufficient data":
            return "insufficient data"
        sig = r["p_location_holm"] < alpha and not (r["shift_low"] <= 0 <= r["shift_high"])
        if not sig:
            return "no detectable change"
        thr = practical.get(r["test_name"]) if isinstance(practical, dict) else practical
        if thr is not None and abs(r["shift"]) < thr:
            return "below practical threshold"
        return "shift"

    t["verdict"] = t.apply(verdict, axis=1)
    t["spread_change"] = t["p_spread_holm"] < alpha
    return BuildComparison(build_a, build_b, alpha, t)
