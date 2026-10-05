"""Known-answer scoring of every detector over many seeds. SIMULATED.

For each scenario and seed: generate a lot with exactly one injected fault, run
the detector built for it, and compare the flags with the truth. Clean lots run
every detector, so false alarms are counted where nothing was injected.

Detector thresholds are the module defaults (Holm alpha 0.05, PAT 6 robust
sigma, correlation tolerance 10% of the spec window, bimodality BIC>10 & Ashman
D>2), fixed from standard practice, not tuned on these seeds. The one exception
is the CUSUM decision interval h: the analytic budget assumes known in-control
parameters, and a 150-reading baseline is not that, so h is calibrated on
seeds 0..39 (clean-lot false-alarm rate <= 5%) and then scored on seeds
1000+, which the calibration never saw.

    python -m radioyield.sim.score --seeds 50 --out docs/results/sim_score.json
"""
from __future__ import annotations

import argparse
import json
import platform
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy import stats

from radioyield.analysis import capability, compare, drift, pat, station
from . import params as P
from .lots import SCENARIOS, LotConfig, generate_lot


def _wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def run_station_offset(lot):
    t = lot.truth
    gc = station.golden_correlation(lot.frame, n_boot=1000)
    flags = gc[gc["flag"]]
    hit = flags[(flags["station"] == t["station"]) & flags["test_name"].isin(t["tests"])]
    false = flags[~((flags["station"] == t["station"]) & flags["test_name"].isin(t["tests"]))]
    prod = station.station_production_check(lot.frame)
    pf = prod[prod["flag"]]
    phit = pf[(pf["station"] == t["station"]) & pf["test_name"].isin(t["tests"])]
    return {"detected": len(hit) > 0, "tests_recalled": len(hit) / len(t["tests"]), "false_flags": len(false),
            "prod_detected": len(phit) > 0, "prod_false_flags": int(len(pf) - len(phit))}


DRIFT_KW: dict = {}


def run_station_drift(lot):
    t = lot.truth
    d = drift.drift_table(lot.frame, **DRIFT_KW)
    al = d[d["alarm"]]
    target = al[(al["station"] == t["station"]) & al["test_name"].isin(t["tests"]) & (al["alarm_ts"] > t["onset_ts"])
                & (al["direction"] == "up")]
    false = al.drop(target.index)
    ttd = float((target["alarm_ts"].min() - t["onset_ts"]) / 3600) if len(target) else np.nan
    return {"detected": len(target) > 0, "tests_recalled": len(target) / len(t["tests"]),
            "time_to_detect_h": ttd, "false_flags": len(false), "charts": int((d["status"] == "charted").sum())}


def run_build_shift(lot):
    t = lot.truth
    c = compare.compare_builds(lot.frame, t["build_a"], t["build_b"], n_boot=1000).table
    sh = c[c["verdict"] == "shift"]
    hit = sh[sh["test_name"].isin(t["tests"])]
    err = hit["shift"] - t["shift_db"]
    covered = ((hit["shift_low"] <= t["shift_db"]) & (t["shift_db"] <= hit["shift_high"])).tolist()
    return {"detected": len(hit) == len(t["tests"]), "tests_recalled": len(hit) / len(t["tests"]),
            "false_flags": int(len(sh) - len(hit)), "shift_est_err_db": float(err.abs().mean()) if len(err) else np.nan,
            "ci_covers_truth": float(np.mean(covered)) if covered else np.nan}


def run_bimodal(lot):
    t = lot.truth
    b = capability.bimodality_table(lot.frame)
    fl = b[b["bimodal"]]
    hit = fl[fl["test_name"].isin(t["tests"])]
    return {"detected": len(hit) > 0, "tests_recalled": len(hit) / len(t["tests"]), "false_flags": int(len(fl) - len(hit))}


def run_pat(lot):
    t = lot.truth
    r = pat.pat(lot.frame)
    flagged = set(r.outlier_units)
    truth = set(t["units"])
    tp = len(flagged & truth)
    return {"detected": tp == len(truth), "unit_recall": tp / len(truth), "false_flags": len(flagged - truth),
            "n_injected": len(truth)}


def run_clean(lot):
    gc = station.golden_correlation(lot.frame, n_boot=1000)
    prod = station.station_production_check(lot.frame)
    d = drift.drift_table(lot.frame, **DRIFT_KW)
    c = compare.compare_builds(lot.frame, "EVT1", "EVT2", n_boot=1000).table
    b = capability.bimodality_table(lot.frame)
    r = pat.pat(lot.frame)
    return {
        "station_golden_flags": int(gc["flag"].sum()), "station_golden_charts": len(gc),
        "station_prod_flags": int(prod["flag"].sum()), "station_prod_checks": len(prod),
        "drift_alarms": int(d["alarm"].sum()), "drift_charts": int((d["status"] == "charted").sum()),
        "drift_readings_charted": int(d.get("charted", 0).sum()),
        "build_shift_flags": int((c["verdict"] == "shift").sum()), "build_tests": len(c),
        "bimodal_flags": int(b["bimodal"].sum()), "bimodal_tests": len(b),
        "pat_units_flagged": len(r.outlier_units), "pat_units_screened": int(lot.frame["part_id"].nunique()),
    }


RUNNERS = {"clean": run_clean, "station_offset": run_station_offset, "station_drift": run_station_drift,
           "build_shift": run_build_shift, "bimodal_antenna": run_bimodal, "pat_marginal": run_pat}


def _one(args):
    scenario, seed, overrides, *rest = args
    if rest:
        DRIFT_KW.clear()
        DRIFT_KW.update(rest[0])
    lot = generate_lot(LotConfig(scenario=scenario, seed=seed, **overrides))
    return scenario, seed, RUNNERS[scenario](lot)


def summarize(results):
    out = {}
    for sc in SCENARIOS:
        rs = [r for s, _, r in results if s == sc]
        if not rs:
            continue
        n = len(rs)
        if sc == "clean":
            agg = {}
            for key in rs[0]:
                agg[key] = int(sum(r[key] for r in rs))
            lots_any = {
                "station_golden": sum(r["station_golden_flags"] > 0 for r in rs),
                "station_prod": sum(r["station_prod_flags"] > 0 for r in rs),
                "drift": sum(r["drift_alarms"] > 0 for r in rs),
                "build_shift": sum(r["build_shift_flags"] > 0 for r in rs),
                "bimodal": sum(r["bimodal_flags"] > 0 for r in rs),
                "pat": sum(r["pat_units_flagged"] > 0 for r in rs),
            }
            out[sc] = {"n_lots": n, "totals": agg,
                       "lots_with_any_false_alarm": {k: {"lots": v, "rate": v / n, "wilson95": _wilson(v, n)}
                                                     for k, v in lots_any.items()}}
            continue
        det = sum(bool(r["detected"]) for r in rs)
        s = {"n_lots": n, "detected": det, "detection_rate": det / n, "wilson95": _wilson(det, n),
             "lots_with_false_flags": sum(r["false_flags"] > 0 for r in rs),
             "false_flags_total": int(sum(r["false_flags"] for r in rs))}
        for key in ("tests_recalled", "unit_recall", "shift_est_err_db", "ci_covers_truth"):
            vals = [r[key] for r in rs if key in r and np.isfinite(r[key])]
            if vals:
                s[f"mean_{key}"] = float(np.mean(vals))
        if sc == "station_drift":
            ttd = np.array([r["time_to_detect_h"] for r in rs if np.isfinite(r["time_to_detect_h"])])
            if ttd.size:
                s["time_to_detect_h"] = {"median": float(np.median(ttd)), "p90": float(np.percentile(ttd, 90)),
                                         "max": float(ttd.max()), "n": int(ttd.size)}
        if sc == "station_offset":
            s["production_check_detected"] = sum(bool(r["prod_detected"]) for r in rs)
            s["production_check_lots_with_false_flags"] = sum(r["prod_false_flags"] > 0 for r in rs)
        out[sc] = s
    return out


def sensitivity(seeds, workers):
    """Detection rate vs fault size, for the two faults with a natural size knob."""
    grid = {"build_shift": ("shift_db", [0.02, 0.05, 0.1, 0.2, 0.4, 0.8]),
            "station_offset": ("offset_db", [0.2, 0.4, 0.6, 0.8, 1.0, 1.5])}
    jobs = [(sc, s, {k: v}) for sc, (k, vals) in grid.items() for v in vals for s in seeds]
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_one, jobs, chunksize=4))
    out = {}
    for (sc, s, ov), (_, _, r) in zip(jobs, res):
        k, v = next(iter(ov.items()))
        out.setdefault(sc, {}).setdefault(str(v), []).append(bool(r["detected"]))
    return {sc: {"knob": grid[sc][0], "points": {v: {"rate": float(np.mean(d)), "n": len(d), "wilson95": _wilson(sum(d), len(d))}
                                                 for v, d in pts.items()}} for sc, pts in out.items()}


def _drift_curve(args):
    seed, grid = args
    clean = generate_lot(LotConfig(scenario="clean", seed=seed))
    dl = generate_lot(LotConfig(scenario="station_drift", seed=seed))
    out = []
    for h in grid:
        c = drift.drift_table(clean.frame, h=h)
        d = drift.drift_table(dl.frame, h=h)
        t = dl.truth
        hit = d[d["alarm"] & (d["station"] == t["station"]) & d["test_name"].isin(t["tests"])
                & (d.get("alarm_ts", 0) > t["onset_ts"])]
        out.append({"h": h, "clean_alarm": bool(c["alarm"].any()),
                    "ttd_h": float((hit["alarm_ts"].min() - t["onset_ts"]) / 3600) if len(hit) else np.nan})
    return out


def calibrate_drift(seeds, workers, budget=0.05, grid=(6, 8, 10, 12, 14, 16, 18, 20, 24)):
    """Pick the CUSUM h on *calibration* seeds (disjoint from the scored seeds):
    the smallest h whose clean-lot false-alarm rate is <= budget."""
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_drift_curve, [(s, grid) for s in seeds]))
    curve = []
    for i, h in enumerate(grid):
        fa = np.mean([r[i]["clean_alarm"] for r in res])
        ttd = np.array([r[i]["ttd_h"] for r in res], float)
        curve.append({"h": h, "clean_lot_fa_rate": float(fa), "detect_rate": float(np.mean(np.isfinite(ttd))),
                      "median_ttd_h": float(np.nanmedian(ttd)) if np.isfinite(ttd).any() else None})
    ok = [c for c in curve if c["clean_lot_fa_rate"] <= budget]
    chosen = ok[0]["h"] if ok else grid[-1]
    return chosen, curve


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--seed-offset", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sensitivity-seeds", type=int, default=20)
    ap.add_argument("--calibration-seeds", type=int, default=40)
    ap.add_argument("--out", default="docs/results/sim_score.json")
    a = ap.parse_args(argv)
    seeds = list(range(a.seed_offset, a.seed_offset + a.seeds))
    t0 = time.time()
    cal_seeds = list(range(0, a.calibration_seeds))
    h, curve = calibrate_drift(cal_seeds, a.workers) if a.calibration_seeds else (None, [])
    jobs = [(sc, s, {}, {"h": h} if h else {}) for sc in SCENARIOS for s in seeds]
    with ProcessPoolExecutor(a.workers) as ex:
        results = list(ex.map(_one, jobs, chunksize=2))
    summary = summarize(results)
    sens = sensitivity(list(range(a.seed_offset + 5000, a.seed_offset + 5000 + a.sensitivity_seeds)), a.workers) \
        if a.sensitivity_seeds else {}
    doc = {"label": "SIMULATED: known-answer scores on synthetic lots, not measurements",
           "command": f"python -m radioyield.sim.score --seeds {a.seeds} --seed-offset {a.seed_offset} "
                      f"--sensitivity-seeds {a.sensitivity_seeds} --calibration-seeds {a.calibration_seeds}",
           "seeds": [seeds[0], seeds[-1]], "machine": f"{platform.machine()} {platform.platform()}",
           "python": platform.python_version(), "elapsed_s": round(time.time() - t0, 1),
           "lot": {"n_units": LotConfig().n_units, "n_stations": LotConfig().n_stations,
                   "shift_hours": LotConfig().shift_hours, "tests": len(P.TESTS)},
           "drift_calibration": {"seeds": [cal_seeds[0], cal_seeds[-1]] if cal_seeds else None,
                                 "budget_clean_lot_fa": 0.05, "chosen_h": h, "curve": curve},
           "summary": summary, "sensitivity": sens,
           "per_lot": [{"scenario": s, "seed": sd, **{k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                                                     for k, v in r.items()}} for s, sd, r in results]}
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(json.dumps(summary, indent=1, default=str))
    print(f"wrote {a.out} in {doc['elapsed_s']} s")


if __name__ == "__main__":
    main()
