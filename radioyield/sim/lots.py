"""Synthetic production lots with known injected faults. SIMULATED -- never a measurement.

A lot is one shift on a line of ``n_stations`` test stations. Units are routed
to stations at random, tested in time order, and a unit that fails is retested
once on a random station. Five golden units (``GU-01``..) are run three times on
every station at the start and at the end of the shift.

Scenarios (one fault each; ``truth`` records exactly what was injected):

  clean           nothing injected (false-alarm baseline); two builds, no shift
  station_offset  one station reads +``offset_db`` on every TX power test
  station_drift   one station's TX power readings drift +``drift_db_per_h``
                  starting ``drift_onset_h`` into the shift
  build_shift     build EVT2 transmits +``shift_db`` on the Wi-Fi TX power tests
  bimodal_antenna ``bimodal_frac`` of units read -``bimodal_db`` on the OTA test
  pat_marginal    ``pat_frac`` of units have one test pushed 6.5-9 robust sigma
                  out, still inside the spec limits
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from radioyield import schema
from . import params as P

SCENARIOS = ("clean", "station_offset", "station_drift", "build_shift", "bimodal_antenna", "pat_marginal")
EPOCH = 1_767_225_600.0   # 2026-01-01T00:00:00Z, the arbitrary start of the simulated shift


@dataclass
class LotConfig:
    scenario: str = "clean"
    seed: int = 0
    n_units: int = 2000
    n_stations: int = 4
    shift_hours: float = 8.0
    builds: tuple[str, ...] = ("EVT1", "EVT2")
    n_golden: int = 5
    golden_repeats: int = 3
    offset_station: str = "ST3"
    offset_db: float = 1.5
    drift_station: str = "ST2"
    drift_db_per_h: float = 0.1
    drift_onset_h: float = 3.0
    shift_db: float = 0.8
    bimodal_frac: float = 0.3
    bimodal_db: float = 3.0
    pat_frac: float = 0.005


@dataclass
class Lot:
    frame: pd.DataFrame
    meta: dict
    truth: dict = field(default_factory=dict)

    def table(self):
        return schema.to_table(self.frame)


def _quantize(x, q):
    return np.round(x / q) * q if q else x


def generate_lot(cfg: LotConfig) -> Lot:
    if cfg.scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {cfg.scenario!r}; one of {SCENARIOS}")
    g = np.random.default_rng([cfg.seed, SCENARIOS.index(cfg.scenario)])
    tests = P.TESTS
    T = len(tests)
    stations = [f"ST{i + 1}" for i in range(cfg.n_stations)]
    lot_id = f"SIM-{cfg.scenario.upper()}-{cfg.seed:04d}"
    truth: dict = {"scenario": cfg.scenario, "seed": cfg.seed}

    # ---- per-station systematic offsets on a clean line (small), per test
    st_off = {s: np.array([g.normal(0, P.STATION_SD.get(t.units, 0.0)) for t in tests]) for s in stations}
    if cfg.scenario == "station_offset":
        for j, t in enumerate(tests):
            if t.kind == "txp":
                st_off[cfg.offset_station][j] += cfg.offset_db
        truth.update(station=cfg.offset_station, tests=list(P.TXP_TESTS), offset_db=cfg.offset_db)

    # ---- per-unit true values
    U = cfg.n_units
    unit_ids = np.array([f"U{cfg.seed:04d}-{i:05d}" for i in range(U)])
    pa = g.normal(0, P.PA_SD, U)
    true = np.empty((U, T))
    for j, t in enumerate(tests):
        true[:, j] = t.nominal + g.normal(0, t.unit_sd, U) + t.pa_load * pa
    sens_off = g.normal(0, P.PER_SENS_UNIT_SD, U)      # per-unit receiver sensitivity offset (dB)
    build = np.array(cfg.builds)[np.arange(U) * len(cfg.builds) // U] if cfg.builds else np.full(U, "EVT1")

    if cfg.scenario == "build_shift":
        for j, t in enumerate(tests):
            if t.name in P.WIFI_TXP_TESTS:
                true[build == cfg.builds[-1], j] += cfg.shift_db
        truth.update(build_a=cfg.builds[0], build_b=cfg.builds[-1], tests=list(P.WIFI_TXP_TESTS), shift_db=cfg.shift_db)
    if cfg.scenario == "bimodal_antenna":
        j = [t.name for t in tests].index("OTA_COUPLER_RSSI")
        bad = g.random(U) < cfg.bimodal_frac
        true[bad, j] -= cfg.bimodal_db
        truth.update(tests=["OTA_COUPLER_RSSI"], bad_units=int(bad.sum()), bimodal_db=cfg.bimodal_db)
    if cfg.scenario == "pat_marginal":
        k = max(1, int(round(cfg.pat_frac * U)))
        idx = g.choice(U, k, replace=False)
        # only tests whose spec window leaves room for a 6.5-9 sigma excursion that still passes
        eligible = [j for j, t in enumerate(tests) if t.lo is not None and t.hi is not None and t.kind != "per"
                    and min(t.hi - t.nominal, t.nominal - t.lo) >= 8.0 * P.total_sd(t)]
        injected = []
        for i in idx:
            j = int(g.choice(eligible))
            t = tests[j]
            sd = P.total_sd(t)
            sign = g.choice([-1, 1])
            room = (t.hi - t.nominal) if sign > 0 else (t.nominal - t.lo)
            mag = g.uniform(6.5 * sd, min(9.0 * sd, room - 1.0 * sd))
            true[i, j] = t.nominal + sign * mag
            injected.append((unit_ids[i], t.name))
        truth.update(units=sorted(u for u, _ in injected), injected=injected)

    # ---- routing and timing: each station tests its units evenly over the shift
    st_of = g.integers(0, cfg.n_stations, U)
    ts = np.empty(U)
    shift_s = cfg.shift_hours * 3600
    golden_block = 1200.0   # golden checks take the first and last 20 minutes
    for s in range(cfg.n_stations):
        m = np.flatnonzero(st_of == s)
        g.shuffle(m)
        ts[m] = EPOCH + golden_block + np.sort(g.uniform(0, shift_s - 2 * golden_block, m.size))
    if cfg.scenario == "station_drift":
        truth.update(station=cfg.drift_station, tests=list(P.TXP_TESTS), drift_db_per_h=cfg.drift_db_per_h,
                     onset_ts=EPOCH + cfg.drift_onset_h * 3600)

    def measure(vals, station, t_s, sens):
        """One insertion: vals is (n, T) true values; returns (n, T) readings."""
        n = vals.shape[0]
        out = np.empty_like(vals)
        for j, t in enumerate(tests):
            st = np.array([st_off[s][j] for s in station])
            r = vals[:, j] + st + g.normal(0, t.meas_sd, n) if t.meas_sd else vals[:, j] + st
            if cfg.scenario == "station_drift" and t.kind == "txp":
                hrs = np.clip((t_s - (EPOCH + cfg.drift_onset_h * 3600)) / 3600, 0, None)
                r = r + np.where(station == cfg.drift_station, cfg.drift_db_per_h * hrs, 0.0)
            if t.kind == "per":
                p = 1 / (1 + np.exp((P.PER_LEVEL_DBM - (P.PER_SENS50_DBM + sens)) / P.PER_SLOPE_DB))
                r = 100.0 * g.binomial(P.PER_PACKETS, p) / P.PER_PACKETS
            out[:, j] = _quantize(r, t.quantum)
        return out

    lo = np.array([np.nan if t.lo is None else t.lo for t in tests])
    hi = np.array([np.nan if t.hi is None else t.hi for t in tests])

    def judge(read):
        ok = np.ones_like(read, bool)
        ok &= ~(np.isfinite(lo) & (read < lo))
        ok &= ~(np.isfinite(hi) & (read > hi))
        return ok

    frames = []

    def emit(pids, station, t_s, read, okm, retest, bld):
        n = len(pids)
        part_pass = okm.all(axis=1)
        first_fail = np.where(part_pass, -1, np.argmax(~okm, axis=1))
        soft = np.where(part_pass, 1, np.array([tests[j].soft_bin for j in range(T)])[np.maximum(first_fail, 0)])
        hard = np.where(part_pass, 1, 5)
        frames.append(pd.DataFrame({
            "part_id": np.repeat(pids, T), "station": np.repeat(station, T), "part_ts": np.repeat(t_s, T),
            "retest": np.repeat(retest, T), "build": np.repeat(bld, T),
            "test_num": np.tile([t.num for t in tests], n), "test_name": np.tile([t.name for t in tests], n),
            "units": np.tile([t.units for t in tests], n), "lo_limit": np.tile(lo, n), "hi_limit": np.tile(hi, n),
            "result": read.ravel(), "test_pass": okm.ravel(), "part_pass": np.repeat(part_pass, T),
            "hard_bin": np.repeat(hard, T), "soft_bin": np.repeat(soft, T),
        }))
        return part_pass

    st_names = np.array(stations)[st_of]
    read = measure(true, st_names, ts, sens_off)
    okm = judge(read)
    passed = emit(unit_ids, st_names, ts, read, okm, np.zeros(U, bool), build)

    # retest failures once, on a random station, a few minutes later
    f = np.flatnonzero(~passed)
    if f.size:
        rst = np.array(stations)[g.integers(0, cfg.n_stations, f.size)]
        rts = np.minimum(ts[f] + g.uniform(120, 900, f.size), EPOCH + shift_s - golden_block)
        r2 = measure(true[f], rst, rts, sens_off[f])
        emit(unit_ids[f], rst, rts, r2, judge(r2), np.ones(f.size, bool), build[f])

    # golden units: drawn from the same process, run on every station at start and end
    G = cfg.n_golden
    gtrue = np.empty((G, T))
    gpa = g.normal(0, P.PA_SD, G)
    for j, t in enumerate(tests):
        gtrue[:, j] = t.nominal + g.normal(0, t.unit_sd, G) + t.pa_load * gpa
    gsens = g.normal(0, P.PER_SENS_UNIT_SD, G)
    gids = np.array([f"GU-{i + 1:02d}" for i in range(G)])
    for block_start in (EPOCH, EPOCH + shift_s - golden_block):
        for si, s in enumerate(stations):
            for rep in range(cfg.golden_repeats):
                tt = block_start + si * 60 + rep * 240 + np.arange(G) * 10.0
                rd = measure(gtrue, np.full(G, s), tt, gsens)
                emit(gids, np.full(G, s), tt, rd, judge(rd), np.zeros(G, bool), np.full(G, "GOLDEN"))

    df = pd.concat(frames, ignore_index=True)
    df.insert(0, "lot_id", lot_id)
    df.insert(3, "data_origin", "SIMULATED")
    df["site_num"] = 1
    df = df[schema.COLUMNS].sort_values(["part_ts", "part_id", "test_num"], kind="stable").reset_index(drop=True)
    meta = {"lot_id": lot_id, "data_origin": "SIMULATED", "scenario": cfg.scenario, "seed": cfg.seed,
            "generator": "radioyield.sim.lots", "n_units": U, "stations": ",".join(stations)}
    return Lot(df, meta, truth)
