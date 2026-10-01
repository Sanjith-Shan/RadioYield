"""Drift detection per station over time: tabular CUSUM and EWMA.

Each (station, test) stream is ordered by part timestamp. The in-control
centre and sigma come from a **baseline window** (the first ``n_baseline``
first-insertion product readings of that stream; gross outliers trimmed, then
mean and sd), or from the
caller (e.g. last week's qualification). Charting starts after the baseline.

* CUSUM (Page 1954, tabular form), k = 0.5 sigma (tuned to a 1-sigma shift).
  The decision interval h is **derived from a false-alarm budget**, not fixed:
  the textbook h = 5 gives an in-control ARL of ~465 readings, and a shift on
  a 4-station line charts ~50 test streams x ~400 readings, so h = 5 alarms on
  nearly every clean lot. By default h is chosen so that the expected number of
  false alarms per lot, over every chart in the lot, is ``family_fa`` (0.05),
  using Siegmund's approximation for the two-sided in-control ARL. That is a
  model of independent normal readings; ``radioyield.sim.score`` measures the
  realized rate on clean lots instead of trusting it.
* Streams unfit for a normal-theory chart (quantized, or skewed like a PER) are
  not charted, and the row says why. They need an attribute chart (p-chart).
* EWMA: lambda = 0.2, L = 3 with the exact time-varying limits.

The output gives the first alarm per chart (time, index, direction), so time to
detect = alarm time - onset is computable when the onset is known (simulation).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from scipy import optimize

from ._stats import mad_sigma, shape_gate
from .yields import GOLDEN_PREFIX, as_frame, product_rows


@dataclass
class Alarm:
    index: int          # position in the charted (post-baseline) stream
    ts: float           # part_ts of the alarming reading
    direction: str      # "up" | "down"


def cusum(x: np.ndarray, mu: float, sigma: float, k: float = 0.5, h: float = 5.0) -> tuple[Alarm | None, np.ndarray, np.ndarray]:
    z = (np.asarray(x, float) - mu) / sigma
    sp = np.zeros(z.size)
    sn = np.zeros(z.size)
    hi = lo = 0.0
    alarm = None
    for i, v in enumerate(z):
        hi = max(0.0, hi + v - k)
        lo = max(0.0, lo - v - k)
        sp[i], sn[i] = hi, lo
        if alarm is None and (hi > h or lo > h):
            alarm = Alarm(i, np.nan, "up" if hi > h else "down")
    return alarm, sp, sn


def ewma(x: np.ndarray, mu: float, sigma: float, lam: float = 0.2, L: float = 3.0) -> tuple[Alarm | None, np.ndarray, np.ndarray]:
    x = np.asarray(x, float)
    zs = np.empty(x.size)
    lims = np.empty(x.size)
    z = mu
    alarm = None
    for i, v in enumerate(x):
        z = lam * v + (1 - lam) * z
        zs[i] = z
        lims[i] = L * sigma * np.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * (i + 1))))
        if alarm is None and abs(z - mu) > lims[i]:
            alarm = Alarm(i, np.nan, "up" if z > mu else "down")
    return alarm, zs, lims


def cusum_arl0(h: float, k: float = 0.5) -> float:
    """Siegmund's approximation of the two-sided in-control ARL (standardized normal data)."""
    b = h + 1.166
    one_sided = (np.exp(2 * k * b) - 2 * k * b - 1) / (2 * k * k)
    return one_sided / 2


def cusum_h_for_arl(arl: float, k: float = 0.5) -> float:
    if arl <= cusum_arl0(0.5, k):
        return 0.5
    return float(optimize.brentq(lambda h: cusum_arl0(h, k) - arl, 0.5, 60))


def drift_table(data, *, n_baseline: int = 150, method: str = "cusum", k: float = 0.5, h: float | None = None,
                family_fa: float = 0.05,
                lam: float = 0.2, L: float = 3.0, golden_prefix: str = GOLDEN_PREFIX,
                tests: list[str] | None = None, keep_traces: bool = False) -> pd.DataFrame:
    """One row per (station, test) chart with its first alarm (if any)."""
    df = product_rows(as_frame(data), golden_prefix)
    df = df[~df["retest"]]
    if tests is not None:
        df = df[df["test_name"].isin(tests)]
    # chartability is a property of the test, judged on all of its readings (a
    # 100-reading window is too small to tell skew from noise)
    unfit = {name: shape_gate(g["result"].to_numpy(float)) for name, g in df.groupby("test_name", observed=True)}
    streams = []
    rows = []
    for (st, num, name), g in df.groupby(["station", "test_num", "test_name"], observed=True, sort=True):
        g = g.sort_values("part_ts", kind="stable")
        x = g["result"].to_numpy(float)
        ts = g["part_ts"].to_numpy(float)
        row = {"station": st, "test_num": int(num), "test_name": str(name), "n": x.size, "method": method, "alarm": False}
        if x.size <= n_baseline + 10:
            row.update(status="not charted: too few readings")
            rows.append(row)
            continue
        base = x[:n_baseline]
        why = unfit.get(name)
        if why:
            row.update(status=f"not charted: {why}; needs an attribute chart")
            rows.append(row)
            continue
        # Phase I: drop gross outliers (> 4 robust sigma), then the efficient
        # estimators. Mean/sd have ~25% lower variance than median/MAD, and the
        # error in mu is what drives a long chart's false alarms.
        rs = mad_sigma(base)
        clean = base[np.abs(base - np.median(base)) <= 4 * rs] if rs > 0 else base
        mu = float(np.mean(clean))
        sigma = float(np.std(clean, ddof=1))
        if not np.isfinite(sigma) or sigma <= 0:
            row.update(status="not charted: zero baseline variance")
            rows.append(row)
            continue
        streams.append((row, x[n_baseline:], ts[n_baseline:], mu, sigma))
    total = sum(s[1].size for s in streams)
    if h is None:
        h_used = cusum_h_for_arl(max(total, 1) / family_fa, k) if total else 5.0
        h_src = f"from budget: {family_fa} expected false alarms per lot over {total} charted readings"
    else:
        h_used, h_src = h, "fixed by caller"
    for row, stream, sts, mu, sigma in streams:
        if method == "cusum":
            a, s1, s2 = cusum(stream, mu, sigma, k, h_used)
            row.update(k=k, h=h_used, h_source=h_src)
        elif method == "ewma":
            a, s1, s2 = ewma(stream, mu, sigma, lam, L)
            row.update(lam=lam, L=L)
        else:
            raise ValueError(method)
        row.update(status="charted", baseline_mu=mu, baseline_sigma=sigma, charted=stream.size,
                   chart_start_ts=float(sts[0]), chart_end_ts=float(sts[-1]), alarm=a is not None)
        if a is not None:
            row.update(alarm_index=a.index, alarm_ts=float(sts[a.index]), direction=a.direction)
        if keep_traces:
            row["trace"] = {"ts": sts, "x": stream, "s1": s1, "s2": s2, "mu": mu, "sigma": sigma}
        rows.append(row)
    return pd.DataFrame(rows)
