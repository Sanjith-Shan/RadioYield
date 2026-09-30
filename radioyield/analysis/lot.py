"""Run the whole analysis engine over one lot (or one comparison) in one call."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import capability, compare, drift, pat, station, yields


@dataclass
class LotAnalysis:
    meta: dict
    yields: yields.YieldResult
    capability: pd.DataFrame
    bimodality: pd.DataFrame
    golden: pd.DataFrame
    station_production: pd.DataFrame
    gauge_rr: pd.DataFrame
    drift: pd.DataFrame
    pat: pat.PatResult
    builds: list[str]
    comparison: compare.BuildComparison | None
    frame: pd.DataFrame = field(repr=False, default=None)


def analyze_lot(data, meta: dict | None = None, *, build_a: str | None = None, build_b: str | None = None,
                n_boot: int = 2000, drift_h: float | None = None) -> LotAnalysis:
    df = yields.as_frame(data)
    meta = dict(meta or {})
    origins = sorted(df["data_origin"].unique().tolist())
    meta.setdefault("data_origin", ",".join(origins))
    meta.setdefault("lot_id", ",".join(sorted(df["lot_id"].unique().tolist())))
    prod = yields.product_rows(df)
    builds = sorted(b for b in prod["build"].unique().tolist() if b)
    comp = None
    if build_a is None and len(builds) >= 2:
        build_a, build_b = builds[0], builds[-1]
    if build_a and build_b:
        comp = compare.compare_builds(df, build_a, build_b, n_boot=n_boot)
    return LotAnalysis(
        meta=meta,
        yields=yields.compute_yield(df),
        capability=capability.capability_table(df, n_boot=n_boot),
        bimodality=capability.bimodality_table(df),
        golden=station.golden_correlation(df, n_boot=n_boot),
        station_production=station.station_production_check(df),
        gauge_rr=station.gauge_rr_table(df),
        drift=drift.drift_table(df, h=drift_h, keep_traces=True),
        pat=pat.pat(df),
        builds=builds,
        comparison=comp,
        frame=df,
    )
