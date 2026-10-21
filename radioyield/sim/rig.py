"""Virtual rig: the whole M2 session (docs/RIG.md section 5) run through the real collector,
the real STDF writer and the real RF fits, against a simulated hallway whose truth is known.

    python -m radioyield.sim.rig session --seed 7 --out out/sim_rig/seed7     # one session
    python -m radioyield.sim.rig score --seeds 30 --out docs/results/sim_rig.json
    python -m radioyield.sim.rig score --seeds 30 --as-written --out docs/results/sim_rig_as_written.json

WHAT THIS IS: a rehearsal. It checks that every plan in the runbook produces data the
analysis can use, and how close each fit lands to a known answer. Every file it writes is
data_origin=SIMULATED (the collector refuses to label test-double data otherwise).

WHAT THIS IS NOT: a measurement. Nothing here says what a real hallway or a real ESP32-C6
will do. The truth below mixes values from published sources with assumptions; each is
labelled. Rig numbers stay NOT YET MEASURED in docs/results/rig.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import shutil
import sys
import tempfile
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from radioyield.collector import plan as PL
from radioyield.collector import protocol as P
from radioyield.collector.fake import FakeAir, LinkModel
from radioyield.collector.runner import run_plan
from radioyield.collector.transport import Board

ROOT = Path(__file__).resolve().parents[2]
PLANS = ROOT / "plans"
SPEED_OF_LIGHT = 299_792_458.0


def friis_1m_db(f_hz: float = 2.44e9) -> float:
    """Free-space loss at 1 m: 20 log10(4 pi d / lambda)."""
    return 20 * math.log10(4 * math.pi * 1.0 * f_hz / SPEED_OF_LIGHT)


def sens50(level_at_crit: float, crit: float, scale_db: float) -> float:
    """50%-loss point of a logistic waterfall that passes through (level_at_crit, crit)."""
    return level_at_crit - scale_db * math.log((1 - crit) / crit)


@dataclass
class HallTruth:
    """The simulated hallway and boards. SOURCE / ASSUMPTION says where each value comes from."""

    seed: int = 0
    # SOURCE: ITU-R P.1238-13 (2025) Sec. 3.1 site-general, corridor LoS: alpha 1.57, sigma 3.77 dB
    exponent: float = 1.57
    shadow_sigma_db: float = 3.77          # large-scale, per position, same on every channel/radio
    # SOURCE: Friis at 2.44 GHz (40.2 dB); antenna gains taken as 0 dBi (ASSUMPTION)
    pl_1m_db: float = field(default_factory=friis_1m_db)
    # ASSUMPTIONS (no source; chosen to be plausible, stated so they can be argued with)
    freq_sel_sigma_db: float = 2.0         # per (position, channel) frequency-selective term
    temporal_sigma_db: float = 0.5         # per step (people walking, etc.)
    fast_sigma_db: float = 1.0             # per packet
    wall_loss_db: float = 6.0              # one interior wall
    wall_distance_m: float = 6.0
    waterfall_scale_db: float = 1.0        # logistic scale of every PER waterfall
    rssi_bias_sd_db: float = 2.0           # REF RSSI is uncalibrated (datasheet states no accuracy)
    tx_error_sd_db: float = 0.5            # unit-to-unit TX error (datasheet states no tolerance)
    atten_tol_db: float = 0.5              # each fixed attenuator, uniform +/-
    cable_true_db: float = 1.3             # vs 1.0 written in the conducted plan
    orientation_effect_db: float = 0.0     # ORIENT_B truth: no change (tests the null)
    drift_db: float = 0.0                  # smoke start vs end truth: no drift
    # SOURCE: ESP32-C6 datasheet v1.5 Tables 6-4, 6-12, 6-18 (see docs/baselines/references.md).
    # ASSUMPTION: ESP-NOW broadcast goes out at the driver default, 802.11b 1 Mbps.
    sens_ds: dict = field(default_factory=lambda: {
        "wifi": (-99.2, 0.08),   # 11b 1 Mbps, at 8% PER
        "ble": (-98.5, 0.308),   # 1M PHY, at 30.8% PER
        "154": (-104.0, 0.01),   # at 1% PER
    })

    def __post_init__(self):
        rng = np.random.default_rng([self.seed, 0xA11])
        self.rssi_bias_db = float(rng.normal(0, self.rssi_bias_sd_db))
        self.tx_error_db = {"DUT-01": float(rng.normal(0, self.tx_error_sd_db)),
                            "DUT-02": float(rng.normal(0, self.tx_error_sd_db))}
        self.atten_errors_db = [float(e) for e in rng.uniform(-self.atten_tol_db, self.atten_tol_db, 8)]

    # ---- per-position static gain (deterministic per seed, position, radio, channel) ----
    def _draw(self, *key) -> float:
        h = zlib.crc32("|".join(map(str, key)).encode())
        return float(np.random.default_rng([self.seed, h]).normal())

    def static_db(self, position: str, radio: str, ch: int) -> float:
        large = -self.shadow_sigma_db * self._draw("S", position)
        if radio == "ble" and ch == P.BLE_CH_ALL:
            fs = np.mean([self._draw("F", position, "ble", c) for c in (37, 38, 39)])
        else:
            fs = self._draw("F", position, radio, ch)
        return large - self.freq_sel_sigma_db * float(fs)

    def sens50(self, radio: str) -> float:
        lvl, crit = self.sens_ds[radio]
        return sens50(lvl, crit, self.waterfall_scale_db)

    def level_at_per(self, radio: str, per: float = 0.10) -> float:
        """True input level where PER = per."""
        return self.sens50(radio) + self.waterfall_scale_db * math.log((1 - per) / per)

    def mean_rssi_at_1m(self, tx_dbm: float, unit: str = "DUT-01") -> float:
        return tx_dbm + self.tx_error_db[unit] - self.pl_1m_db + self.rssi_bias_db

    def to_json(self) -> dict:
        d = asdict(self)
        d.update(rssi_bias_db=self.rssi_bias_db, tx_error_db=self.tx_error_db, atten_errors_db=self.atten_errors_db,
                 level_at_10pct_per={r: self.level_at_per(r) for r in self.sens_ds})
        return d


# ---- the session, step by step from docs/RIG.md section 5 --------------------------------
@dataclass
class Run:
    rig_step: str
    plan: str
    distance_m: float | None = None
    position: str | None = None
    obstacles: str | None = None
    orientation: str | None = None
    build: str | None = None
    unit: str = "DUT-01"
    fw_offset_qdbm: int = 0          # EVT2 TX table: -2 dB = -8 qdBm
    extra_loss_db: float = 0.0       # wall, orientation effect, drift


# docs/RIG.md step 2. The first version used (1, 2, 4, 8, 16); the rehearsal showed the
# exponent CI was ~2.8 wide with 5 positions, so the runbook now walks 10 marks.
PATHLOSS_POSITIONS_M = (1, 1.5, 2, 3, 4, 6, 8, 11, 16, 20)
PATHLOSS_POSITIONS_V1_M = (1, 2, 4, 8, 16)
# The other two plan values the rehearsal changed, kept so `score --as-written` reproduces it.
AS_WRITTEN = {"build_compare_repeats": 5, "conducted_attenuation_db": [30, 30, 20]}


def session_runs(t: HallTruth, conducted: bool = True, positions=PATHLOSS_POSITIONS_M) -> list[Run]:
    wall = "one interior wall (simulated)"
    runs = [Run("1", "smoke_wifi_1m", 1.0, "1m"), Run("1", "smoke_ble_1m", 1.0, "1m")]
    for d in positions:
        for p in ("pathloss_wifi", "pathloss_ble"):
            runs.append(Run("2", p, float(d), f"tape-{d:g}m"))
    for p in ("pathloss_wifi", "pathloss_ble"):
        runs.append(Run("3", p, t.wall_distance_m, "wall", obstacles=wall, extra_loss_db=t.wall_loss_db))
    runs += [Run("4", "txlin_wifi", 2.0, "2m"), Run("4", "txlin_ble", 2.0, "2m")]
    runs += [Run("5", "build_compare_wifi", 4.0, "4m", build="EVT1"),
             Run("6", "build_compare_wifi", 4.0, "4m", build="EVT2", fw_offset_qdbm=-8),
             Run("7", "build_compare_wifi", 4.0, "4m", build="ORIENT_A"),
             Run("7", "build_compare_wifi", 4.0, "4m", build="ORIENT_B", orientation="DUT rotated 90 deg (simulated)",
                 extra_loss_db=t.orientation_effect_db)]
    for p in ("waterfall_wifi", "waterfall_ble"):
        runs.append(Run("8", p, t.wall_distance_m, "wall", obstacles=wall, extra_loss_db=t.wall_loss_db))
    runs += [Run("9", "ieee154_smoke", 2.0, "2m"),
             Run("10", "smoke_wifi_1m", 1.0, "1m", extra_loss_db=t.drift_db),
             Run("11", "txlin_wifi", 2.0, "2m", unit="DUT-02")]
    if conducted:
        runs += [Run("M3", "conducted_wifi"), Run("M3", "conducted_ble")]
    return runs


def _plan_for(run: Run, as_written: bool = False) -> PL.Plan:
    plan = PL.load(PLANS / f"{run.plan}.yaml")
    if as_written:
        if plan.name == "build_compare_wifi":
            plan.repeats = AS_WRITTEN["build_compare_repeats"]
        if plan.mode == "CONDUCTED":
            plan.setup["attenuation_db"] = list(AS_WRITTEN["conducted_attenuation_db"])
    plan.settle_ms = 0  # nothing to settle in the test double; keeps a session to seconds
    plan.unit_serial = run.unit
    if run.build:
        plan.build = run.build
    if plan.mode != "CONDUCTED":
        if run.distance_m is not None:
            plan.setup["distance_m"] = run.distance_m
        if run.position:
            plan.setup["position_label"] = run.position
        if run.obstacles:
            plan.setup["obstacles"] = run.obstacles
        if run.orientation:
            plan.setup["orientation"] = run.orientation
    return plan


def _link_for(run: Run, plan: PL.Plan, t: HallTruth) -> LinkModel:
    link = LinkModel(distance_m=plan.setup.get("distance_m") or 1.0, exponent=t.exponent, pl_1m_db=t.pl_1m_db,
                     extra_loss_db=run.extra_loss_db, shadow_sigma_db=t.temporal_sigma_db,
                     fast_sigma_db=t.fast_sigma_db, waterfall_db=t.waterfall_scale_db,
                     sens_dbm={r: t.sens50(r) for r in t.sens_ds},
                     tx_offset_db=t.tx_error_db[run.unit], rssi_bias_db=t.rssi_bias_db)
    if plan.mode == "CONDUCTED":
        n = len(plan.setup.get("attenuation_db") or [])
        true_atten = (plan.attenuation_total_db or 0) + sum(t.atten_errors_db[:n])
        link.conducted_loss_db = true_atten + t.cable_true_db
    else:
        pos = run.position or f"{run.distance_m}m"
        link.static_db = lambda radio, ch, _p=pos: t.static_db(_p, radio, ch)
    return link


def run_session(seed: int, out_dir: str | Path, *, conducted: bool = True, quiet: bool = True,
                truth: HallTruth | None = None, as_written: bool = False) -> HallTruth:
    t = truth or HallTruth(seed=seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.timezone.utc)
    positions = PATHLOSS_POSITIONS_V1_M if as_written else PATHLOSS_POSITIONS_M
    for i, run in enumerate(session_runs(t, conducted, positions)):
        plan = _plan_for(run, as_written)
        air = FakeAir(_link_for(run, plan, t), seed=seed * 1000 + i)
        dut = Board(air.board("DUT", 0x11 if run.unit == "DUT-01" else 0x12, pwr_offset_qdbm=run.fw_offset_qdbm),
                    "DUT", None, simulated=True)
        ref = Board(air.board("REF", 0x22), "REF", None, simulated=True)
        try:
            run_plan(plan, dut, ref, out, progress=None if quiet else print, run_id_seed=seed * 1000 + i,
                     now=t0 + dt.timedelta(minutes=3 * i))
        finally:
            dut.close()
            ref.close()
    (out / "TRUTH.json").write_text(json.dumps(t.to_json(), indent=2, default=float))
    return t


# ---- analysis vs truth ------------------------------------------------------------------
def _measurements(d: Path, plan: str) -> pd.DataFrame:
    fs = [f for f in sorted(d.glob(f"{plan}__*.measurements.parquet"))]
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()


def _build_cmp(meas: pd.DataFrame, a: str, b: str, truth_shift: dict[int, float]) -> dict:
    from radioyield.analysis.compare import compare_builds

    m = meas[meas["test_name"].str.endswith("RSSI_MEDIAN")]
    c = compare_builds(m, a, b, n_boot=500).table
    c["truth"] = [truth_shift.get(int(n), np.nan) for n in c["test_num"]]
    flagged = c["verdict"] == "shift"
    return {"n_tests": int(len(c)), "n_flagged": int(flagged.sum()),
            "min_p_raw": float(c["p_location"].min()), "min_p_holm": float(c["p_location_holm"].min()),
            "median_shift_db": float(c["shift"].median()), "truth_median_shift_db": float(np.nanmedian(c["truth"])),
            "ci_covers_truth": float(((c["shift_low"] <= c["truth"]) & (c["truth"] <= c["shift_high"])).mean())}


def analyze_session(d: str | Path, t: HallTruth) -> dict:
    from radioyield.analysis.rig import load_rig_dir, rf_from_rig

    d = Path(d)
    steps, packets = load_rig_dir(d)
    rf, notes = rf_from_rig(steps, packets)
    r: dict = {"notes": notes, "n_runs": int(steps["run_uid"].nunique()), "n_steps": int(len(steps)),
               "n_packets": int(len(packets))}

    pl = rf.get("path_loss")
    if pl:
        f = pl["fit"]
        e = f.exponent
        r["path_loss"] = {"n": e.estimate, "lo": e.low, "hi": e.high, "truth": t.exponent,
                          "covers": bool(e.low <= t.exponent <= e.high), "sigma_db": f.shadowing_sigma_db,
                          "truth_sigma_db": math.hypot(t.shadow_sigma_db, t.freq_sel_sigma_db / math.sqrt(3)),
                          "intercept": f.intercept_dbm, "truth_intercept": t.mean_rssi_at_1m(20.0)}

    tx = rf.get("tx_linearity", {})
    r["tx_linearity"] = {k: {"slope": v["fit"].slope.estimate, "lo": v["fit"].slope.low, "hi": v["fit"].slope.high,
                             "truth": 1.0, "covers": bool(v["fit"].slope.low <= 1.0 <= v["fit"].slope.high),
                             "max_resid_db": v["fit"].max_abs_residual_db} for k, v in tx.items()}

    wf = rf.get("waterfall", {})
    r["waterfall"] = {}
    for key, v in wf.items():
        radio, mode = key.split()
        lv = v["fit"].level_at_target
        if mode == "CONDUCTED":
            truth = t.level_at_per(radio)          # true input level at 10% PER
        else:
            truth = t.level_at_per(radio) + t.rssi_bias_db  # what an unbiased RSSI would read there
        r["waterfall"][key] = {"level": lv.estimate, "lo": lv.low, "hi": lv.high, "truth": truth,
                               "error": lv.estimate - truth, "covers": bool(lv.low <= truth <= lv.high),
                               "bracketed": v["fit"].bracketed}
    for radio in ("wifi", "ble"):
        for mode in ("OTA", "CONDUCTED"):
            if f"{radio} {mode}" not in r["waterfall"]:
                g = steps[(steps["plan"].str.startswith(("waterfall", "conducted"))) & (steps["radio"] == radio)
                          & (steps["mode"] == mode)]
                if len(g):
                    truth = t.level_at_per(radio) + (0.0 if mode == "CONDUCTED" else t.rssi_bias_db)
                    r["waterfall"][f"{radio} {mode}"] = {"fitted": False, "truth": truth, "per_range_pct": [
                        float(g["per_pct"].min()), float(g["per_pct"].max())]}

    bc = _measurements(d, "build_compare_wifi")
    if len(bc):
        # truth per test: EVT2 table is -8 qdBm before the driver's power quantisation
        truth_evt = {}
        for ch in (1, 6, 11):
            for q in (44, 60, 80):
                from radioyield.collector.outputs import test_num
                truth_evt[test_num("wifi", ch, q, "RSSI_MEDIAN")] = (
                    P.expected_wifi_qdbm(max(8, q - 8)) - P.expected_wifi_qdbm(q)) / 4
        truth_or = {k: t.orientation_effect_db * -1 for k in truth_evt}
        r["build_evt"] = _build_cmp(bc, "EVT1", "EVT2", truth_evt)
        r["build_orient"] = _build_cmp(bc, "ORIENT_A", "ORIENT_B", truth_or)

    tl = steps[steps["plan"].str.startswith("txlin_wifi")]
    if tl["unit_serial"].nunique() == 2:
        med = tl.groupby("unit_serial")["rssi_median"].median()
        r["unit_to_unit"] = {"est_db": float(med["DUT-02"] - med["DUT-01"]),
                             "truth_db": t.tx_error_db["DUT-02"] - t.tx_error_db["DUT-01"]}
    sm = steps[steps["plan"] == "smoke_wifi_1m"].sort_values("t_start")
    if sm["run_uid"].nunique() == 2:
        first, last = sm["run_uid"].unique()[[0, -1]]
        r["drift"] = {"est_db": float(sm.loc[sm["run_uid"] == last, "rssi_median"].median()
                                      - sm.loc[sm["run_uid"] == first, "rssi_median"].median()),
                      "truth_db": -t.drift_db}
    g154 = steps[steps["radio"] == "154"]
    r["ieee154"] = {"steps": int(len(g154)), "max_per_pct": float(g154["per_pct"].max()) if len(g154) else None}
    return r


def _one(args) -> dict:
    seed, keep, as_written = args
    d = Path(keep) / f"seed{seed}" if keep else Path(tempfile.mkdtemp(prefix=f"ry_rig_{seed}_"))
    try:
        t = run_session(seed, d, as_written=as_written)
        res = analyze_session(d, t)
        res["seed"] = seed
        res["truth"] = {"rssi_bias_db": t.rssi_bias_db, "tx_error_db": t.tx_error_db}
        return res
    finally:
        if not keep:
            shutil.rmtree(d, ignore_errors=True)


def _wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    if n == 0:
        return [math.nan, math.nan]
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, c - h), min(1.0, c + h)]


def summarize(results: list[dict]) -> dict:
    n = len(results)
    s: dict = {"n_sessions": n, "label": "SIMULATED"}

    def rate(vals):
        k = int(sum(bool(v) for v in vals))
        return {"k": k, "n": len(vals), "rate": k / len(vals) if vals else math.nan, "wilson95": _wilson(k, len(vals))}

    def dist(vals):
        a = np.asarray([v for v in vals if v is not None and np.isfinite(v)], float)
        if not a.size:
            return None
        return {"median": float(np.median(a)), "p05": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95)),
                "mean": float(a.mean()), "n": int(a.size)}

    pl = [r["path_loss"] for r in results if "path_loss" in r]
    s["path_loss"] = {"fitted": rate(["path_loss" in r for r in results]),
                      "exponent_error": dist([p["n"] - p["truth"] for p in pl]),
                      "ci_coverage": rate([p["covers"] for p in pl]),
                      "ci_width": dist([p["hi"] - p["lo"] for p in pl]),
                      "intercept_error_db": dist([p["intercept"] - p["truth_intercept"] for p in pl]),
                      "sigma_est_db": dist([p["sigma_db"] for p in pl])}
    s["tx_linearity"] = {k: {"slope_error": dist([r["tx_linearity"][k]["slope"] - 1 for r in results
                                                  if k in r["tx_linearity"]]),
                             "ci_coverage": rate([r["tx_linearity"][k]["covers"] for r in results
                                                  if k in r["tx_linearity"]])}
                         for k in ("wifi", "ble")}
    wf_keys = sorted({k for r in results for k in r["waterfall"]})
    s["waterfall"] = {}
    for k in wf_keys:
        rows = [r["waterfall"][k] for r in results if k in r["waterfall"]]
        fitted = [w for w in rows if w.get("fitted", True)]
        s["waterfall"][k] = {"fitted": rate([w.get("fitted", True) for w in rows]),
                             "error_db": dist([w["error"] for w in fitted]),
                             "ci_coverage": rate([w["covers"] for w in fitted]) if fitted else None,
                             "per_range_when_not_fitted": [w["per_range_pct"] for w in rows if not w.get("fitted", True)][:3]}
    for key in ("build_evt", "build_orient"):
        rows = [r[key] for r in results if key in r]
        s[key] = {"any_flag": rate([b["n_flagged"] > 0 for b in rows]),
                  "flagged_tests": dist([b["n_flagged"] for b in rows]),
                  "n_tests": rows[0]["n_tests"] if rows else None,
                  "min_p_raw": dist([b["min_p_raw"] for b in rows]),
                  "min_p_holm": dist([b["min_p_holm"] for b in rows]),
                  "median_shift_error_db": dist([b["median_shift_db"] - b["truth_median_shift_db"] for b in rows]),
                  "ci_coverage_mean": dist([b["ci_covers_truth"] for b in rows])}
    s["unit_to_unit_error_db"] = dist([r["unit_to_unit"]["est_db"] - r["unit_to_unit"]["truth_db"]
                                       for r in results if "unit_to_unit" in r])
    s["drift_error_db"] = dist([r["drift"]["est_db"] - r["drift"]["truth_db"] for r in results if "drift" in r])
    s["notes_seen"] = sorted({nt for r in results for nt in r["notes"]})[:20]
    s["per_session"] = {"runs": results[0]["n_runs"], "steps": results[0]["n_steps"], "packets": results[0]["n_packets"]}
    return s


def score(seeds: list[int], workers: int | None = None, keep: str | None = None,
          as_written: bool = False) -> dict:
    workers = workers or min(8, os.cpu_count() or 1)
    with ProcessPoolExecutor(workers) as ex:
        results = list(ex.map(_one, [(s, keep, as_written) for s in seeds]))
    summ = summarize(results)
    summ["seeds"] = [seeds[0], seeds[-1]]
    summ["truth_defaults"] = {k: v for k, v in HallTruth().to_json().items()
                              if k not in ("seed", "rssi_bias_db", "tx_error_db", "atten_errors_db")}
    return {"summary": summ, "sessions": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m radioyield.sim.rig", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("session", help="run the whole RIG.md session once against the virtual hallway")
    a.add_argument("--seed", type=int, default=7)
    a.add_argument("--out", default="out/sim_rig/seed7")
    a.add_argument("--no-conducted", action="store_true")
    b = sub.add_parser("score", help="run many sessions and score every fit against the truth")
    b.add_argument("--seeds", type=int, default=30)
    b.add_argument("--seed-offset", type=int, default=2000)
    b.add_argument("--workers", type=int)
    b.add_argument("--out", default="docs/results/sim_rig.json")
    b.add_argument("--as-written", action="store_true",
                   help="the plans as first written: 5 path-loss marks, 5 build repeats, 80 dB conducted chain")
    args = ap.parse_args(argv)
    if args.cmd == "session":
        if Path(args.out).exists():
            shutil.rmtree(args.out)
        t = run_session(args.seed, args.out, conducted=not args.no_conducted, quiet=False)
        print(json.dumps(analyze_session(args.out, t), indent=2, default=float))
        return 0
    res = score(list(range(args.seed_offset, args.seed_offset + args.seeds)), args.workers,
                as_written=args.as_written)
    res["summary"]["pathloss_positions_m"] = list(PATHLOSS_POSITIONS_V1_M if args.as_written else PATHLOSS_POSITIONS_M)
    if args.as_written:
        res["summary"]["plans"] = AS_WRITTEN
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, indent=2, default=float))
    print(json.dumps(res["summary"], indent=2, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
