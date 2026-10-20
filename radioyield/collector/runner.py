"""Drive one sweep plan across a DUT and a REF board and write the run's files.

Per step: SET both boards, REF ``RX start``, DUT ``TX n= interval_ms= run=``, wait for
``TXDONE``, settle, REF ``RX stop``, then compute PER/RSSI from the REF's lines.

Files per run (stem = <plan>__<build>__<unit>__<UTC time>):
  <stem>.stdf                  STDF V4 via radioyield.stdf.write (one part per repeat)
  <stem>.measurements.parquet  the same MEASUREMENT_SCHEMA table (re-export: `collector stdf`)
  <stem>.packets.parquet       raw per-packet log (PACKET_SCHEMA)
  <stem>.steps.parquet         per-step summary (STEP_SCHEMA)
  <stem>.json                  meta + the plan as loaded + warnings
  <stem>.serial.log            transcript of every serial line, both boards, both directions
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import math
import platform
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pyarrow.parquet as pq

from radioyield.collector import outputs as O
from radioyield.collector import protocol as P
from radioyield.collector import stats as S
from radioyield.collector.plan import Plan, Step
from radioyield.collector.transport import Board, BoardError

COLLECTOR_VERSION = "0.1.0"
NOT_MEASURED = "EVM,spectral_mask,TRP,TIS,absolute_TX_power"

StdfWriter = Callable[..., object]


@dataclass
class RunResult:
    stem: str
    out_dir: Path
    files: dict[str, Path]
    meta: dict[str, str]
    steps: list[dict]
    n_packets: int
    complete: bool
    stdf_error: str | None = None
    warnings: list[str] = field(default_factory=list)


def default_stdf_writer() -> StdfWriter:
    from radioyield.stdf import write  # built by stdf/ (C++); import late so the collector works without it

    return write


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (list, tuple)):
        return "+".join(_fmt(x) for x in v)
    return str(v)


def build_meta(plan: Plan, dut: Board, ref: Board, data_origin: str, run_uid: str, lot_id: str) -> dict[str, str]:
    di, ri = dut.info, ref.info
    assert di and ri
    m: dict[str, object] = {
        "lot_id": lot_id, "station": plan.station, "build": plan.build, "data_origin": data_origin,
        "run_uid": run_uid, "plan": plan.name, "plan_file": Path(plan.source).name, "plan_sha256": plan.sha256,
        "unit_serial": plan.unit_serial, "ref_serial": plan.ref_serial or "unrecorded", "radio": plan.radio,
        "mode": plan.mode, "orientation": plan.setup.get("orientation", ""),
        "obstacles": plan.setup.get("obstacles", ""), "packets_per_step": plan.packets_per_step,
        "interval_ms": plan.interval_ms, "payload_len": plan.payload_len, "repeats": plan.repeats,
        "dut_fw": di.fw, "dut_idf": di.idf, "dut_mac": di.mac, "dut_label": di.label,
        "dut_pwr_offset_qdbm": di.pwr_offset_qdbm, "ref_fw": ri.fw, "ref_idf": ri.idf, "ref_mac": ri.mac,
        "ref_label": ri.label, "collector_version": COLLECTOR_VERSION, "host": platform.node() or "unknown",
        "rssi_note": "receiver-reported RSSI, integer dBm, uncalibrated",
        "not_measured": NOT_MEASURED,
    }
    for k in ("distance_m", "height_m", "cable_loss_db", "environment", "position_label", "notes"):
        if k in plan.setup:
            m[k] = plan.setup[k]
    if "attenuation_db" in plan.setup:
        m["attenuation_db"] = plan.setup["attenuation_db"]
        m["attenuation_total_db"] = plan.attenuation_total_db
    if plan.build_note:
        m["build_note"] = plan.build_note
    if plan.radio == "ble":
        m["ble_rx_channel"] = "not reported by NimBLE; tx channel map in channel column"
        m["ble_window_ms"] = di.ble_window_ms
    if data_origin == "SIMULATED":
        m["simulated_note"] = "protocol test double (radioyield.collector.fake); not a measurement"
    return {k: _fmt(v) for k, v in m.items()}


def _setup_cols(plan: Plan, meta: dict[str, str]) -> dict:
    return {
        "run_uid": meta["run_uid"], "data_origin": meta["data_origin"], "lot_id": meta["lot_id"],
        "station": plan.station, "build": plan.build, "unit_serial": plan.unit_serial, "mode": plan.mode,
        "distance_m": plan.setup.get("distance_m"), "attenuation_total_db": plan.attenuation_total_db,
        "cable_loss_db": plan.setup.get("cable_loss_db"), "orientation": plan.setup.get("orientation"),
        "obstacles": plan.setup.get("obstacles"), "position_label": plan.setup.get("position_label"),
    }


def run_stem(plan: Plan, now: dt.datetime) -> str:
    return O.safe_stem(plan.name, plan.build, plan.unit_serial, now.strftime("%Y%m%dT%H%M%SZ"))


def run_step(dut: Board, ref: Board, step: Step, run_id: int, plan: Plan) -> tuple[dict, list[dict], int]:
    """Returns (step summary row without setup cols, packet rows, n accepted lines)."""
    di = dut.info
    assert di
    ch = "all" if step.channel_label == "ALL" else step.channel
    set_kw = dict(radio=step.radio, ch=ch, pwr_qdbm=step.pwr_qdbm, len=step.payload_len)
    ref.command(P.format_command("SET", **set_kw, scan_ms=plan.scan_ms))
    dut_set = dut.command(P.format_command("SET", **set_kw))
    pwr_act = dut_set.int("pwr_act_qdbm")
    ref.take_rx()  # drop anything stale
    ref.command("RX start")
    t0 = time.time()
    if plan.settle_ms:
        time.sleep(min(plan.settle_ms, 100) / 1000)
    dut.command(P.format_command("TX", n=step.n, interval_ms=step.interval_ms, run=run_id))
    per_pkt_s = (step.interval_ms + (di.ble_window_ms if step.radio == "ble" else 0)) / 1000
    try:
        done = dut.wait_event("TXDONE", timeout=step.n * per_pkt_s * 1.5 + 10)
    except BoardError:
        dut.command("STOP", check=False)
        raise
    time.sleep(plan.settle_ms / 1000)
    stop = ref.command("RX stop")
    t1 = time.time()
    lines = ref.take_rx()

    recs: list[tuple[float, P.RxRecord]] = []
    bad = 0
    for host_t, line in lines:
        try:
            r = P.parse_rx_line(line)
        except P.ProtocolError:
            bad += 1
            continue
        if r.run_id == run_id and r.tag == di.tag and r.radio == step.radio:
            recs.append((host_t, r))
    n_sent = int(done.get("sent", step.n))
    st = S.compute([r for _, r in recs], n_cmd=step.n, n_sent=n_sent, n_tx_fail=int(done.get("fail", 0)),
                   ref_ovf=int(stop.fields.get("ovf", 0)), ref_bad=int(stop.fields.get("bad", 0)),
                   host_bad_lines=bad, aborted=done.get("aborted", "0") != "0")
    key = {"step_idx": step.index, "repeat": step.repeat, "radio": step.radio, "channel": step.channel,
           "channel_label": step.channel_label, "pwr_cmd_qdbm": step.pwr_qdbm, "pwr_act_qdbm": pwr_act}
    row = dict(key)
    row.update({k: O._nan_to_none(v) if isinstance(v, float) else v
                for k, v in dataclasses.asdict(st).items() if k != "flags"})
    row.update({"run_id": run_id, "payload_len": step.payload_len, "interval_ms": step.interval_ms,
                "per_meaningful": step.per_meaningful, "ref_ok": int(stop.fields.get("ok", 0)),
                "ref_other": int(stop.fields.get("other", 0)), "flags": ";".join(st.flags), "t_start": t0,
                "t_end": t1})
    seen: set[int] = set()
    pkts = []
    for host_t, r in recs:
        dup = r.seq in seen
        seen.add(r.seq)
        pkts.append({**key, "run_id": r.run_id, "seq": r.seq,
                     "rssi_dbm": None if r.rssi == S.RSSI_UNAVAILABLE else r.rssi,
                     "rx_channel": r.rx_ch or None, "lqi": None if r.lqi < 0 else r.lqi, "ref_t_us": r.t_us,
                     "host_t": host_t, "dup": dup, "dut_tag": r.tag})
    return row, pkts, len(recs)


def run_plan(plan: Plan, dut: Board, ref: Board, out_dir: str | Path, *,
             stdf_writer: StdfWriter | None = None, progress: Callable[[str], None] | None = print,
             run_id_seed: int | None = None, now: dt.datetime | None = None) -> RunResult:
    say = progress or (lambda s: None)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    di, ri = dut.handshake(), ref.handshake()
    if di.role != "DUT" or ri.role != "REF":
        raise BoardError(f"role mismatch: --dut port reports {di.role}, --ref port reports {ri.role} "
                         "(swap the ports, or reflash; `python -m radioyield.collector ports` shows which is which)")
    for b in (dut, ref):
        if plan.radio not in b.info.radios:
            raise BoardError(f"{b.name} firmware does not include radio {plan.radio} (INFO radios={b.info.radios})")
    if plan.radio == "ble" and plan.interval_ms < di.ble_window_ms + 5:
        raise BoardError(f"BLE interval_ms {plan.interval_ms} < DUT ble_window_ms {di.ble_window_ms} + 5")

    # a fake board can never produce RIG_* data
    simulated = dut.simulated or ref.simulated or di.label == "SIMULATED" or ri.label == "SIMULATED"
    data_origin = "SIMULATED" if simulated else plan.data_origin
    now = now or dt.datetime.now(dt.timezone.utc)
    lot_id = plan.lot_id or f"RIG-{now.strftime('%Y%m%d')}"
    run_uid = stem = run_stem(plan, now)
    meta = build_meta(plan, dut, ref, data_origin, run_uid, lot_id)
    meta["start_utc"] = now.isoformat()

    rng = random.Random(run_id_seed)
    run_id = rng.randrange(1, 60000)
    steps = plan.steps()
    step_rows: list[dict] = []
    pkt_rows: list[dict] = []
    part_ts: dict[int, float] = {}
    warnings = list(plan.warnings)
    setup = _setup_cols(plan, meta)
    complete = False
    error: BaseException | None = None
    say(f"[{data_origin}] {plan.name}: {len(steps)} steps, DUT {di.mac} ({di.label}), REF {ri.mac}")
    try:
        for st in steps:
            part_ts.setdefault(st.repeat, time.time())
            row, pkts, _ = run_step(dut, ref, st, run_id, plan)
            row.update(setup)
            for p in pkts:
                p.update(setup)
            step_rows.append(row)
            pkt_rows.extend(pkts)
            run_id = (run_id + 1) % 65536
            metric = "PER" if st.per_meaningful else "RX_FRAC"
            val = row["per_pct"] if st.per_meaningful else (
                100.0 * row["n_rx_unique"] / row["n_onair"] if row["n_onair"] else math.nan)
            med = row["rssi_median"]
            say(f"  step {st.index + 1}/{len(steps)} {st.radio} ch{st.channel_label} pwr {st.pwr_qdbm} qdBm "
                f"(act {row['pwr_act_qdbm']}): tx {row['n_onair']} rx {row['n_rx_unique']} "
                f"{metric} {val:.2f}% RSSI med {('%.1f' % med) if med is not None else 'n/a'} dBm"
                + (f"  FLAGS {row['flags']}" if row["flags"] else ""))
            if not row["per_valid"]:
                warnings.append(f"step {st.index}: PER invalid ({row['flags']})")
        complete = True
    except (BoardError, KeyboardInterrupt) as e:
        error = e
        for b, cmd in ((dut, "STOP"), (ref, "RX stop")):
            try:
                b.command(cmd, timeout=1.0, check=False)
            except BoardError:
                pass
        warnings.append(f"run incomplete: {type(e).__name__}: {e}")
    meta["end_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    meta["complete"] = "true" if complete else "false"
    meta["n_steps_done"] = str(len(step_rows))
    meta["n_packets_logged"] = str(len(pkt_rows))

    files: dict[str, Path] = {}
    packets = O.table(pkt_rows, O.PACKET_SCHEMA).replace_schema_metadata({k.encode(): v.encode() for k, v in meta.items()})
    steps_t = O.table(step_rows, O.STEP_SCHEMA).replace_schema_metadata({k.encode(): v.encode() for k, v in meta.items()})
    files["packets"] = out / f"{stem}.packets.parquet"
    files["steps"] = out / f"{stem}.steps.parquet"
    pq.write_table(packets, files["packets"])
    pq.write_table(steps_t, files["steps"])
    meas = O.measurement_rows(step_rows, plan.limits, {**meta, "unit_serial": plan.unit_serial}, part_ts)
    files["measurements"] = out / f"{stem}.measurements.parquet"
    pq.write_table(meas.replace_schema_metadata({k.encode(): v.encode() for k, v in meta.items()}),
                   files["measurements"])

    stdf_error = None
    if meas.num_rows:
        try:
            writer = stdf_writer or default_stdf_writer()
            files["stdf"] = out / f"{stem}.stdf"
            mir = {"JOB_NAM": plan.name, "EXEC_TYP": "radioyield.collector", "EXEC_VER": COLLECTOR_VERSION,
                   "TSTR_TYP": "SIMULATED test double" if data_origin == "SIMULATED" else f"ESP32-C6 {plan.mode} rig",
                   "PART_TYP": "ESP32-C6 DUT", "SERL_NUM": plan.ref_serial or ""}
            writer(files["stdf"], meas, {**meta, **mir})
        except Exception as e:  # keep the run's data even if STDF fails; re-export later
            files.pop("stdf", None)
            stdf_error = f"{type(e).__name__}: {e}"
            warnings.append(f"STDF not written ({stdf_error}); re-export with "
                            f"`python -m radioyield.collector stdf {files['measurements']}`")
    files["json"] = out / f"{stem}.json"
    plan_dict = {k: v for k, v in dataclasses.asdict(plan).items() if k not in ("warnings",)}
    plan_dict["limits"] = {k: dataclasses.asdict(v) for k, v in plan.limits.items()}
    files["json"].write_text(json.dumps({"meta": meta, "plan": plan_dict, "warnings": warnings,
                                         "dut_info": di.raw, "ref_info": ri.raw,
                                         "files": {k: v.name for k, v in files.items()}}, indent=2, default=str))
    result = RunResult(stem=stem, out_dir=out, files=files, meta=meta, steps=step_rows, n_packets=len(pkt_rows),
                       complete=complete, stdf_error=stdf_error, warnings=warnings)
    if error is not None:
        if isinstance(error, KeyboardInterrupt):
            raise error
        raise BoardError(f"{error} (partial data written: {files['steps']})") from error
    return result
