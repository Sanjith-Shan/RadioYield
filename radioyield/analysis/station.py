"""Station correlation and measurement-system analysis.

Three tools, from most to least direct:

1. **Golden-unit correlation** (`golden_correlation`). Reference units (part_id
   ``GU-*``) are run on every station. For each station and test, the offset is
   the mean of (reading on this station - that unit's reference value), with a
   bootstrap interval over readings, and the slope is the OLS fit of station
   reading on reference value across units. The reference defaults to the
   **consensus** (per unit, the median across stations of the per-station mean),
   so a single bad station cannot drag the reference with it -- including the
   case where the "reference station" is itself the bad one. A slope is only
   assessed when the golden units span >= 10x the station's repeatability sd
   (from the golden repeats); units that all sit near nominal cannot reveal a
   gain error, and the table says so instead of reporting a noisy slope.
   Slope tests are Holm-corrected across all assessed charts and need
   |slope - 1| > 10% to flag.

   A station is flagged on a test when |offset| > tolerance **and** the offset
   interval excludes 0. Default tolerance is 10% of the spec window (a common
   correlation guard band); pass ``tolerance`` to override per test.

2. **Production comparison** (`station_production_check`). Without golden data,
   compare each station's product medians with the other stations pooled
   (Mann-Whitney, Holm across station x test). Valid only when units are routed
   to stations without regard to their quality, which is normal for a line but
   must be true.

3. **Gauge R&R, ANOVA method** (`gauge_rr`), AIAG MSA crossed design: parts x
   stations x repeats. Reports repeatability, reproducibility, part variation,
   %GRR of total variation, %GRR of tolerance and ndc.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from ._stats import DEFAULT_N_BOOT, SEED, bootstrap_ci, holm
from .yields import GOLDEN_PREFIX, as_frame, product_rows


def _tolerance(lo, hi, frac=0.10):
    if np.isfinite(lo) and np.isfinite(hi):
        return frac * (hi - lo)
    return np.nan


def _limits(g):
    lo = g["lo_limit"].dropna()
    hi = g["hi_limit"].dropna()
    return (float(lo.iloc[0]) if len(lo) else np.nan, float(hi.iloc[0]) if len(hi) else np.nan)


def golden_correlation(data, *, reference: str = "consensus", tolerance: dict[str, float] | None = None,
                       tol_frac: float = 0.10, slope_tol: float = 0.10, min_span_rep: float = 10.0, golden_prefix: str = GOLDEN_PREFIX,
                       n_boot: int = DEFAULT_N_BOOT, seed: int = SEED) -> pd.DataFrame:
    df = as_frame(data)
    gd = df[df["part_id"].str.startswith(golden_prefix)]
    if gd.empty:
        return pd.DataFrame()
    rows = []
    for (num, name), g in gd.groupby(["test_num", "test_name"], observed=True, sort=True):
        lo, hi = _limits(g)
        tol = (tolerance or {}).get(str(name), _tolerance(lo, hi, tol_frac))
        unit_station = g.groupby(["part_id", "station"], observed=True)["result"].mean().unstack("station")
        if reference == "consensus":
            ref = unit_station.median(axis=1)
            ref_label = "consensus (median across stations)"
        else:
            if reference not in unit_station.columns:
                raise ValueError(f"reference station {reference!r} has no golden data")
            ref = unit_station[reference]
            ref_label = reference
        for st, gs in g.groupby("station", observed=True):
            if reference != "consensus" and st == reference:
                continue
            diffs = gs["result"].to_numpy() - gs["part_id"].map(ref).to_numpy()
            diffs = diffs[np.isfinite(diffs)]
            ci = bootstrap_ci(diffs, np.mean, n_boot=n_boot, seed=seed, vectorized=lambda m: m.mean(axis=1))
            per_unit = unit_station[st].dropna()
            x = ref.loc[per_unit.index].to_numpy()
            y = per_unit.to_numpy()
            slope = slope_lo = slope_hi = r = p_slope = np.nan
            rep_sd = float(np.sqrt(gs.groupby("part_id", observed=True)["result"].var(ddof=1).mean()))
            span_ok = bool(x.size >= 3 and np.ptp(x) > 0 and np.isfinite(rep_sd) and np.ptp(x) >= min_span_rep * rep_sd)
            if span_ok:
                lr = stats.linregress(x, y)
                slope, r = float(lr.slope), float(lr.rvalue)
                tcrit = stats.t.ppf(0.975, x.size - 2)
                slope_lo, slope_hi = slope - tcrit * lr.stderr, slope + tcrit * lr.stderr
                p_slope = float(2 * stats.t.sf(abs(slope - 1) / lr.stderr, x.size - 2)) if lr.stderr > 0 else 0.0
            offset_flag = bool(np.isfinite(tol) and abs(ci.estimate) > tol and ci.excludes(0.0))
            slope_status = "assessed" if span_ok else (
                f"not assessable: golden units span {np.ptp(x):.3g} {g['units'].iloc[0]}, "
                f"< {min_span_rep:g}x the station's repeatability sd ({rep_sd:.3g})")
            rows.append({"test_num": int(num), "test_name": str(name), "units": str(g["units"].iloc[0]),
                         "station": st, "reference": ref_label, "n_readings": diffs.size,
                         "n_golden_units": int(per_unit.size), "offset": ci.estimate, "offset_low": ci.low,
                         "offset_high": ci.high, "tolerance": tol, "slope": slope, "slope_low": slope_lo,
                         "slope_high": slope_hi, "r": r, "slope_status": slope_status, "p_slope": p_slope, "offset_flag": offset_flag})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    # slope: Holm across every assessed (station, test) chart -- 50-odd charts at
    # 5% each would otherwise flag a healthy line most of the time
    t["p_slope_holm"] = np.nan
    ok = t["p_slope"].notna()
    t.loc[ok, "p_slope_holm"] = holm(t.loc[ok, "p_slope"].to_numpy())
    t["slope_flag"] = (t["p_slope_holm"] < 0.05) & ((t["slope"] - 1).abs() > slope_tol)
    t["flag"] = t["offset_flag"] | t["slope_flag"]
    return t


def station_production_check(data, *, alpha: float = 0.05, tol_frac: float = 0.10,
                             golden_prefix: str = GOLDEN_PREFIX) -> pd.DataFrame:
    df = product_rows(as_frame(data), golden_prefix)
    df = df[~df["retest"]]
    rows = []
    for (num, name), g in df.groupby(["test_num", "test_name"], observed=True, sort=True):
        lo, hi = _limits(g)
        tol = _tolerance(lo, hi, tol_frac)
        for st in g["station"].unique():
            a = g.loc[g["station"] == st, "result"].to_numpy(float)
            b = g.loc[g["station"] != st, "result"].to_numpy(float)
            if a.size < 3 or b.size < 3:
                continue
            p = float(stats.mannwhitneyu(a, b).pvalue)
            rows.append({"test_num": int(num), "test_name": str(name), "station": st, "n": a.size,
                         "delta_median": float(np.median(a) - np.median(b)), "p": p, "tolerance": tol})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    t["p_holm"] = holm(t["p"].to_numpy())
    t["flag"] = (t["p_holm"] < alpha) & (t["delta_median"].abs() > t["tolerance"])
    return t


@dataclass
class GaugeRR:
    test_name: str
    n_parts: int
    n_stations: int
    n_repeats: int
    repeatability_sd: float
    reproducibility_sd: float
    grr_sd: float
    part_sd: float
    total_sd: float
    pct_grr_tv: float
    pct_grr_tol: float
    ndc: float
    p_interaction: float
    verdict: str

    def row(self):
        return self.__dict__.copy()


def gauge_rr(values: pd.DataFrame, lo: float = np.nan, hi: float = np.nan, test_name: str = "") -> GaugeRR | None:
    """Two-way crossed ANOVA GR&R. ``values`` has columns part_id, station, result.

    The design is balanced by truncating every cell to the smallest repeat count.
    Negative variance components are set to 0 (standard practice).
    """
    v = values[["part_id", "station", "result"]].dropna()
    counts = v.groupby(["part_id", "station"], observed=True).size()
    parts = v["part_id"].unique()
    sts = v["station"].unique()
    if len(counts) < len(parts) * len(sts) or counts.min() < 2 or len(parts) < 2 or len(sts) < 2:
        return None
    r = int(counts.min())
    v = v.groupby(["part_id", "station"], observed=True).head(r)
    p, o = len(parts), len(sts)
    grand = v["result"].mean()
    pm = v.groupby("part_id")["result"].mean()
    om = v.groupby("station")["result"].mean()
    cm = v.groupby(["part_id", "station"], observed=True)["result"].mean()
    ss_p = o * r * ((pm - grand) ** 2).sum()
    ss_o = p * r * ((om - grand) ** 2).sum()
    cell = cm.reset_index()
    cell["pm"] = cell["part_id"].map(pm)
    cell["om"] = cell["station"].map(om)
    ss_po = r * ((cell["result"] - cell["pm"] - cell["om"] + grand) ** 2).sum()
    vv = v.merge(cm.rename("cm").reset_index(), on=["part_id", "station"])
    ss_e = ((vv["result"] - vv["cm"]) ** 2).sum()
    df_p, df_o, df_po, df_e = p - 1, o - 1, (p - 1) * (o - 1), p * o * (r - 1)
    ms_p, ms_o, ms_po, ms_e = ss_p / df_p, ss_o / df_o, ss_po / df_po, ss_e / df_e
    p_int = float(stats.f.sf(ms_po / ms_e, df_po, df_e)) if ms_e > 0 else np.nan
    var_e = ms_e
    var_po = max(0.0, (ms_po - ms_e) / r)
    var_o = max(0.0, (ms_o - ms_po) / (p * r))
    var_p = max(0.0, (ms_p - ms_po) / (o * r))
    grr = var_e + var_o + var_po
    tv = grr + var_p
    pct_tv = 100 * np.sqrt(grr / tv) if tv > 0 else np.nan
    pct_tol = 100 * 6 * np.sqrt(grr) / (hi - lo) if np.isfinite(lo) and np.isfinite(hi) else np.nan
    ndc = 1.41 * np.sqrt(var_p / grr) if grr > 0 else np.inf
    basis = pct_tol if np.isfinite(pct_tol) else pct_tv
    verdict = "acceptable" if basis < 10 else ("marginal" if basis <= 30 else "unacceptable")
    return GaugeRR(test_name, p, o, r, float(np.sqrt(var_e)), float(np.sqrt(var_o + var_po)), float(np.sqrt(grr)),
                   float(np.sqrt(var_p)), float(np.sqrt(tv)), float(pct_tv), float(pct_tol), float(ndc), p_int,
                   f"{verdict} (AIAG: <10% acceptable, 10-30% marginal; basis: "
                   f"{'%tolerance' if np.isfinite(pct_tol) else '%TV'})")


def gauge_rr_table(data, golden_prefix: str = GOLDEN_PREFIX) -> pd.DataFrame:
    df = as_frame(data)
    gd = df[df["part_id"].str.startswith(golden_prefix)]
    rows = []
    for (num, name), g in gd.groupby(["test_num", "test_name"], observed=True, sort=True):
        lo, hi = _limits(g)
        res = gauge_rr(g, lo, hi, str(name))
        if res is not None:
            rows.append({"test_num": int(num), **res.row()})
    return pd.DataFrame(rows)
