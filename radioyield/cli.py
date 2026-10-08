"""radioyield command line.

    radioyield sim --scenario build_shift --seed 7 --out out/lot.parquet [--stdf out/stdf/]
    radioyield report out/lot.parquet [more.parquet | file.stdf ...] --out out/report.html
    radioyield report --rig out/rig/ --out out/rig_report.html   (rig session: lot + RF fits)
    radioyield score --seeds 50                      (known-answer scoring, SIMULATED)
    radioyield demo --out examples/                  (one report per simulated scenario)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd


def _load(paths: list[str]) -> tuple[pd.DataFrame, dict]:
    frames, meta = [], {}
    for p in paths:
        pp = Path(p)
        files = sorted(pp.rglob("*.stdf")) + sorted(pp.rglob("*.parquet")) if pp.is_dir() else [pp]
        for f in files:
            if f.suffix.lower() in (".stdf", ".std"):
                from radioyield import stdf
                s = stdf.read(str(f))
                frames.append(s.measurements.to_pandas())
                meta.update({k: v for k, v in s.meta.items() if isinstance(v, (str, int, float))})
            elif f.suffix == ".parquet":
                frames.append(pd.read_parquet(f))
                side = f.with_suffix(".meta.json")
                if side.exists():
                    meta.update(json.loads(side.read_text()))
            else:
                raise SystemExit(f"don't know how to read {f}")
    if not frames:
        raise SystemExit("no input data found")
    return pd.concat(frames, ignore_index=True), meta


def _write_lot(lot, out: Path, stdf_dir: Path | None):
    out.parent.mkdir(parents=True, exist_ok=True)
    lot.frame.to_parquet(out, index=False)
    out.with_suffix(".meta.json").write_text(json.dumps({**lot.meta, "truth": lot.truth}, indent=1, default=str))
    if stdf_dir:
        from radioyield import stdf
        stdf_dir.mkdir(parents=True, exist_ok=True)
        for (st, b), g in lot.frame.groupby(["station", "build"]):
            stdf.write(str(stdf_dir / f"{lot.meta['lot_id']}_{st}_{b}.stdf"), g.reset_index(drop=True),
                       {**lot.meta, "station": st, "build": b})


def cmd_sim(a):
    from radioyield.sim.lots import LotConfig, generate_lot
    lot = generate_lot(LotConfig(scenario=a.scenario, seed=a.seed, n_units=a.units))
    _write_lot(lot, Path(a.out), Path(a.stdf) if a.stdf else None)
    print(f"wrote {a.out} ({len(lot.frame):,} rows, SIMULATED); truth: {json.dumps(lot.truth, default=str)[:300]}")


def cmd_report(a):
    from radioyield.analysis.lot import analyze_lot
    from radioyield.report.html import write_lot_report
    rf, notes = None, []
    if a.rig and not a.inputs:
        from radioyield.report.rig_html import write_rig_report
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        write_rig_report(a.rig, a.out, title=a.title)
        print(f"wrote {a.out}")
        return 0
    if a.rig:
        from radioyield.analysis.rig import load_rig_dir, rf_from_rig
        steps, packets = load_rig_dir(a.rig)
        rf, notes = rf_from_rig(steps, packets)
        if not a.inputs:
            a.inputs = [str(f) for f in sorted(Path(a.rig).rglob("*.measurements.parquet"))]
    df, meta = _load(a.inputs)
    meta.pop("truth", None)
    an = analyze_lot(df, meta, drift_h=a.drift_h)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    write_lot_report(an, a.out, title=a.title, rf=rf, not_measured=notes)
    print(f"wrote {a.out}")


def cmd_demo(a):
    from radioyield.analysis.lot import analyze_lot
    from radioyield.report.html import write_lot_report
    from radioyield.sim.lots import SCENARIOS, LotConfig, generate_lot
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for sc in SCENARIOS:
        lot = generate_lot(LotConfig(scenario=sc, seed=a.seed))
        meta = {**lot.meta, "injected_fault": json.dumps({k: v for k, v in lot.truth.items() if k != "injected"},
                                                          default=str)}
        an = analyze_lot(lot.frame, meta, drift_h=a.drift_h)
        p = out / f"sim_{sc}.html"
        write_lot_report(an, p, title=f"Simulated lot: {sc.replace('_', ' ')}")
        print(f"wrote {p}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="radioyield", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sim", help="generate a SIMULATED lot")
    s.add_argument("--scenario", default="clean")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--units", type=int, default=2000)
    s.add_argument("--out", default="out/lot.parquet")
    s.add_argument("--stdf", help="also write one STDF file per (station, build) into this directory")
    s.set_defaults(fn=cmd_sim)
    r = sub.add_parser("report", help="analyse parquet/STDF input and write one HTML report")
    r.add_argument("inputs", nargs="*")
    r.add_argument("--rig", help="collector output directory: adds PER waterfall, path loss and TX linearity fits")
    r.add_argument("--out", default="out/report.html")
    r.add_argument("--title")
    r.add_argument("--drift-h", type=float, default=18.0,
                   help="CUSUM decision interval (default 18: calibrated on clean simulated lots; see docs/RESULTS.md)")
    r.set_defaults(fn=cmd_report)
    d = sub.add_parser("demo", help="one report per simulated scenario")
    d.add_argument("--out", default="examples")
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--drift-h", type=float, default=18.0)
    d.set_defaults(fn=cmd_demo)
    sc = sub.add_parser("score", help="known-answer scoring (passes remaining args through)")
    sc.set_defaults(fn=None)
    a, rest = ap.parse_known_args(argv)
    if a.cmd == "score":
        from radioyield.sim.score import main as score_main
        return score_main(rest)
    if rest:
        ap.error(f"unrecognized arguments: {rest}")
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
