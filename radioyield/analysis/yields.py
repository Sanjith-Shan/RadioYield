"""Limits, yield and failure Pareto.

Definitions (stated, because "yield" means different things on different lines):

* A **part run** is one insertion of one unit: the rows sharing
  (lot_id, station, part_id, part_ts).
* **First-pass yield (FPY)**: of the distinct units, the fraction whose *first*
  insertion passed.
* **Final yield**: the fraction whose *last* insertion passed (retest included).
* **Yield by test**: per test, the fraction of first insertions where that test
  passed. A test that was not executed on a run does not count against it.
* **Pareto**: first-insertion failures counted by test (a unit failing three
  tests counts once for each) and by soft bin (a unit counts once).

Golden/reference units (part_id prefix ``GU-`` by default) are excluded: they
are measurement-system checks, not product.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

RUN_KEY = ["lot_id", "station", "part_id", "part_ts"]
GOLDEN_PREFIX = "GU-"


def as_frame(data) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data
    return data.to_pandas()


def product_rows(df: pd.DataFrame, golden_prefix: str = GOLDEN_PREFIX) -> pd.DataFrame:
    return df[~df["part_id"].str.startswith(golden_prefix)]


def part_runs(df: pd.DataFrame) -> pd.DataFrame:
    """One row per insertion, ordered in time, with its pass flag and bins."""
    runs = (
        df.groupby(RUN_KEY, sort=False, observed=True)
        .agg(part_pass=("part_pass", "first"), hard_bin=("hard_bin", "first"),
             soft_bin=("soft_bin", "first"), retest=("retest", "first"), build=("build", "first"))
        .reset_index()
        .sort_values(["part_id", "part_ts", "retest"], kind="stable")
    )
    runs["insertion"] = runs.groupby("part_id").cumcount() + 1
    return runs


@dataclass
class YieldResult:
    n_units: int
    n_insertions: int
    first_pass_yield: float
    final_yield: float
    retested_units: int
    recovered_by_retest: int
    by_test: pd.DataFrame          # test_num, test_name, n, n_fail, yield
    pareto_by_test: pd.DataFrame   # test_name, fails, pct_of_failing_units, cum_pct
    pareto_by_bin: pd.DataFrame    # soft_bin, units, pct, cum_pct
    notes: list[str] = field(default_factory=list)


def compute_yield(data, golden_prefix: str = GOLDEN_PREFIX) -> YieldResult:
    df = product_rows(as_frame(data), golden_prefix)
    notes: list[str] = []
    if df.empty:
        empty = pd.DataFrame()
        return YieldResult(0, 0, np.nan, np.nan, 0, 0, empty, empty, empty, ["no product rows"])
    runs = part_runs(df)
    first = runs.groupby("part_id").head(1)
    last = runs.groupby("part_id").tail(1)
    n_units = len(first)
    fpy = float(first["part_pass"].mean())
    final = float(last["part_pass"].mean())
    retested = int((runs.groupby("part_id").size() > 1).sum())
    recovered = int((~first.set_index("part_id")["part_pass"] & last.set_index("part_id")["part_pass"]).sum())

    # first-insertion rows
    first_keys = first[RUN_KEY]
    fr = df.merge(first_keys, on=RUN_KEY, how="inner")
    by_test = (
        fr.groupby(["test_num", "test_name"], observed=True)
        .agg(n=("test_pass", "size"), n_fail=("test_pass", lambda s: int((~s).sum())))
        .reset_index()
    )
    by_test["yield"] = 1 - by_test["n_fail"] / by_test["n"]
    by_test = by_test.sort_values("test_num").reset_index(drop=True)

    failing_units = int((~first["part_pass"]).sum())
    pt = by_test[by_test["n_fail"] > 0][["test_num", "test_name", "n_fail"]].rename(columns={"n_fail": "fails"})
    pt = pt.sort_values("fails", ascending=False).reset_index(drop=True)
    total_fail_events = pt["fails"].sum()
    pt["pct_of_fail_events"] = 100 * pt["fails"] / total_fail_events if total_fail_events else 0.0
    pt["cum_pct"] = pt["pct_of_fail_events"].cumsum()
    pt["pct_of_failing_units"] = 100 * pt["fails"] / failing_units if failing_units else 0.0

    fb = first[~first["part_pass"]].groupby("soft_bin").size().rename("units").reset_index()
    fb = fb.sort_values("units", ascending=False).reset_index(drop=True)
    fb["pct"] = 100 * fb["units"] / fb["units"].sum() if len(fb) else 0.0
    fb["cum_pct"] = fb["pct"].cumsum()

    # Consistency check: a part flagged pass with a failing test (or vice versa) is a data problem
    incons = fr.groupby(RUN_KEY, observed=True).agg(pp=("part_pass", "first"), allp=("test_pass", "all"))
    bad = int((incons["pp"] & ~incons["allp"]).sum())
    if bad:
        notes.append(f"{bad} insertions are marked PASS but contain a failing test (check bin/limit logic upstream)")
    return YieldResult(n_units, len(runs), fpy, final, retested, recovered, by_test, pt, fb, notes)
