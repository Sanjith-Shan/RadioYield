"""Dynamic Part Average Testing (AEC-Q001 Rev D).

Per lot and test, from the *current lot's* parts that passed the spec limits
(AEC-Q001 Rev D section 3.1.2):

    robust mean  = median (Q2)                       (section 2.4)
    robust sigma = (Q3 - Q1) / 1.35                  (section 2.4)
    PAT limits   = robust mean +/- 6 robust sigma    (section 3.1.2)

AEC-Q001 says PAT limits "shall not exceed the device specification limits"
(section 3.1). It gives no explicit formula, so clamping each PAT limit to the
spec limit (min with USL, max with LSL) is this module's reading of that rule.

A unit that passes spec but falls outside a PAT limit on any test is a PAT
outlier: it is not failing, it is unlike its lot, which is what predicts latent
and field failures. Static PAT (limits from >= 6 historical lots) is the same
formula over historical data; pass ``static_limits`` to use it.

Section 2.4 note 2: the 1.35 constant is inexact for n < 20; this module needs
``min_parts`` (default 30) passing parts per test, else it reports nothing for
that test rather than inexact limits.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ._stats import shape_gate
from .yields import GOLDEN_PREFIX, as_frame, product_rows

K_SIGMA = 6.0
IQR_DIVISOR = 1.35   # AEC-Q001 Rev D section 2.4, verbatim


@dataclass
class PatResult:
    limits: pd.DataFrame     # per (lot, test): n, robust mean/sigma, PAT lo/hi, clamped flags
    outliers: pd.DataFrame   # per (part, test) PAT violation among spec-passing readings
    outlier_units: list[str]


def pat(data, *, k: float = K_SIGMA, min_parts: int = 30, golden_prefix: str = GOLDEN_PREFIX,
        static_limits: pd.DataFrame | None = None) -> PatResult:
    df = product_rows(as_frame(data), golden_prefix)
    df = df[~df["retest"]]
    lim_rows, out = [], []
    for (lot, num, name), g in df.groupby(["lot_id", "test_num", "test_name"], observed=True, sort=True):
        passing = g[g["test_pass"]]
        x = passing["result"].to_numpy(float)
        x = x[np.isfinite(x)]
        lsl = g["lo_limit"].dropna()
        usl = g["hi_limit"].dropna()
        lsl = float(lsl.iloc[0]) if len(lsl) else -np.inf
        usl = float(usl.iloc[0]) if len(usl) else np.inf
        if static_limits is not None:
            s = static_limits[static_limits["test_name"] == name]
            if s.empty:
                continue
            med, rs = float(s["robust_mean"].iloc[0]), float(s["robust_sigma"].iloc[0])
            n = x.size
        else:
            n = x.size
            if n < min_parts:
                lim_rows.append({"lot_id": lot, "test_num": int(num), "test_name": str(name), "n": n,
                                 "status": f"skipped: {n} passing parts < {min_parts}"})
                continue
            why = shape_gate(x)
            if why:
                lim_rows.append({"lot_id": lot, "test_num": int(num), "test_name": str(name), "n": n,
                                 "status": f"skipped: {why}; AEC-Q001 3.1.1 asks for other means on non-normal data"})
                continue
            q1, med, q3 = np.percentile(x, [25, 50, 75])
            rs = (q3 - q1) / IQR_DIVISOR
        raw_lo, raw_hi = med - k * rs, med + k * rs
        plo, phi = max(raw_lo, lsl), min(raw_hi, usl)
        lim_rows.append({"lot_id": lot, "test_num": int(num), "test_name": str(name), "n": n,
                         "robust_mean": med, "robust_sigma": rs, "pat_lo": plo, "pat_hi": phi,
                         "lo_clamped_to_spec": raw_lo < lsl, "hi_clamped_to_spec": raw_hi > usl,
                         "status": "ok" if rs > 0 else "zero robust sigma"})
        if rs <= 0:
            continue
        v = passing[(passing["result"] < plo) | (passing["result"] > phi)]
        for _, r in v.iterrows():
            out.append({"lot_id": lot, "part_id": r["part_id"], "station": r["station"], "test_num": int(num),
                        "test_name": str(name), "result": float(r["result"]), "pat_lo": plo, "pat_hi": phi,
                        "robust_sigmas": float((r["result"] - med) / rs)})
    outliers = pd.DataFrame(out)
    units = sorted(outliers["part_id"].unique().tolist()) if len(outliers) else []
    return PatResult(pd.DataFrame(lim_rows), outliers, units)
