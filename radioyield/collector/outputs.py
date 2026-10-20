"""Output tables: raw per-packet log, per-step summary (both Parquet, schemas below and in
README.md), and the MEASUREMENT_SCHEMA table that goes to STDF."""
from __future__ import annotations

import math
import re
from typing import Any

import pyarrow as pa

from radioyield.schema import MEASUREMENT_SCHEMA

#: Setup columns repeated on every packet/step row so each file stands alone.
SETUP_FIELDS = [
    pa.field("run_uid", pa.string(), nullable=False),
    pa.field("data_origin", pa.string(), nullable=False),
    pa.field("lot_id", pa.string()),
    pa.field("station", pa.string()),
    pa.field("build", pa.string()),
    pa.field("unit_serial", pa.string()),
    pa.field("mode", pa.string()),                 # OTA | CONDUCTED
    pa.field("distance_m", pa.float64()),          # null for CONDUCTED unless given
    pa.field("attenuation_total_db", pa.float64()),  # sum of fixed attenuators, null for OTA
    pa.field("cable_loss_db", pa.float64()),
    pa.field("orientation", pa.string()),
    pa.field("obstacles", pa.string()),
    pa.field("position_label", pa.string()),
]

STEP_KEY_FIELDS = [
    pa.field("step_idx", pa.int32(), nullable=False),
    pa.field("repeat", pa.int16(), nullable=False),
    pa.field("radio", pa.string(), nullable=False),          # wifi | ble | 154
    pa.field("channel", pa.int16(), nullable=False),         # commanded; BLE 0 = all of 37/38/39
    pa.field("channel_label", pa.string(), nullable=False),  # "6", "37", "ALL"
    pa.field("pwr_cmd_qdbm", pa.int16(), nullable=False),    # host-commanded, 0.25 dB units
    pa.field("pwr_act_qdbm", pa.int16()),                    # DUT driver read-back (incl. build offset)
]

PACKET_SCHEMA = pa.schema(SETUP_FIELDS + STEP_KEY_FIELDS + [
    pa.field("run_id", pa.uint16(), nullable=False),     # over-the-air run id of the step
    pa.field("seq", pa.uint32(), nullable=False),
    pa.field("rssi_dbm", pa.int16()),                    # REF stack RSSI; null if unavailable (BLE 127)
    pa.field("rx_channel", pa.int16()),                  # REF-reported channel; null if not reported (BLE)
    pa.field("lqi", pa.int16()),                         # 802.15.4 only
    pa.field("ref_t_us", pa.uint64(), nullable=False),   # REF esp_timer_get_time() at reception
    pa.field("host_t", pa.float64(), nullable=False),    # host unix time the line was read
    pa.field("dup", pa.bool_(), nullable=False),         # a repeat of a seq already logged in this step
    pa.field("dut_tag", pa.uint16(), nullable=False),
])

STEP_SCHEMA = pa.schema(SETUP_FIELDS + STEP_KEY_FIELDS + [
    pa.field("run_id", pa.uint16(), nullable=False),
    pa.field("payload_len", pa.int16()),
    pa.field("interval_ms", pa.int32()),
    pa.field("n_cmd", pa.int32()),
    pa.field("n_sent", pa.int32()),
    pa.field("n_tx_fail", pa.int32()),
    pa.field("n_onair", pa.int32()),
    pa.field("n_rx_unique", pa.int32()),
    pa.field("n_lost", pa.int32()),
    pa.field("n_dup", pa.int32()),
    pa.field("n_reorder", pa.int32()),
    pa.field("n_out_of_range", pa.int32()),
    pa.field("per_pct", pa.float64()),
    pa.field("per_meaningful", pa.bool_()),   # False for single-channel BLE (scan-schedule limited)
    pa.field("per_valid", pa.bool_()),        # False if REF overflowed, lines were corrupted, or TX aborted
    pa.field("rssi_mean", pa.float64()),
    pa.field("rssi_median", pa.float64()),
    pa.field("rssi_stdev", pa.float64()),
    pa.field("rssi_min", pa.float64()),
    pa.field("rssi_max", pa.float64()),
    pa.field("ref_ok", pa.int32()),
    pa.field("ref_bad", pa.int32()),
    pa.field("ref_other", pa.int32()),
    pa.field("ref_ovf", pa.int32()),
    pa.field("host_bad_lines", pa.int32()),
    pa.field("flags", pa.string()),
    pa.field("t_start", pa.float64()),
    pa.field("t_end", pa.float64()),
])

METRIC_INDEX = {"PER": 0, "RX_FRAC": 1, "RSSI_MEAN": 2, "RSSI_MEDIAN": 3, "RSSI_STDEV": 4, "N_RX": 5, "N_TX": 6}
METRIC_UNITS = {"PER": "%", "RX_FRAC": "%", "RSSI_MEAN": "dBm", "RSSI_MEDIAN": "dBm", "RSSI_STDEV": "dB",
                "N_RX": "count", "N_TX": "count"}
RADIO_CODE = {"wifi": 1, "ble": 2, "154": 3}
RADIO_TAG = {"wifi": "WIFI", "ble": "BLE", "154": "IEEE154"}


def test_name(radio: str, channel_label: str, pwr_qdbm: int, metric: str) -> str:
    p = f"P{pwr_qdbm}" if pwr_qdbm >= 0 else f"PN{-pwr_qdbm}"
    return f"{RADIO_TAG[radio]}_CH{channel_label}_{p}_{metric}"


def test_num(radio: str, channel: int, pwr_qdbm: int, metric: str) -> int:
    """Deterministic, so the same test has the same number in every run and build."""
    return RADIO_CODE[radio] * 1_000_000 + channel * 10_000 + (pwr_qdbm + 200) * 10 + METRIC_INDEX[metric]


def table(rows: list[dict[str, Any]], schema: pa.Schema) -> pa.Table:
    cols = {f.name: [r.get(f.name) for r in rows] for f in schema}
    return pa.Table.from_pydict(cols, schema=schema)


def _nan_to_none(x):
    return None if (isinstance(x, float) and math.isnan(x)) else x


def measurement_rows(step_rows: list[dict[str, Any]], limits: dict, meta: dict, part_ts: dict[int, float]) -> pa.Table:
    """One part per repeat (part_id = unit serial, site 1); one test per (step, metric)."""
    rows: list[dict[str, Any]] = []
    by_rep: dict[int, list[dict[str, Any]]] = {}
    for s in step_rows:
        rep = int(s["repeat"])
        metrics: list[tuple[str, float]] = []
        if s["per_valid"] and s["n_onair"] > 0:
            if s["per_meaningful"]:
                metrics.append(("PER", s["per_pct"]))
            else:
                metrics.append(("RX_FRAC", 100.0 * s["n_rx_unique"] / s["n_onair"]))
        if s["n_rx_unique"] > 0 and s["rssi_mean"] is not None:
            metrics += [("RSSI_MEAN", s["rssi_mean"]), ("RSSI_MEDIAN", s["rssi_median"])]
            if s["rssi_stdev"] is not None:
                metrics.append(("RSSI_STDEV", s["rssi_stdev"]))
        metrics += [("N_RX", float(s["n_rx_unique"])), ("N_TX", float(s["n_onair"]))]
        for m, v in metrics:
            lim = limits.get(m)
            lo = lim.lo if lim else None
            hi = lim.hi if lim else None
            ok = (lo is None or v >= lo) and (hi is None or v <= hi)
            by_rep.setdefault(rep, []).append({
                "test_num": test_num(s["radio"], int(s["channel"]), int(s["pwr_cmd_qdbm"]), m),
                "test_name": test_name(s["radio"], s["channel_label"], int(s["pwr_cmd_qdbm"]), m),
                "units": METRIC_UNITS[m], "lo_limit": lo, "hi_limit": hi, "result": float(v), "test_pass": ok,
            })
    for rep in sorted(by_rep):
        tests = by_rep[rep]
        part_ok = all(t["test_pass"] for t in tests)
        for t in tests:
            rows.append({
                "lot_id": meta["lot_id"], "station": meta["station"], "build": meta["build"],
                "data_origin": meta["data_origin"], "site_num": 1, "part_id": meta["unit_serial"],
                "part_ts": part_ts.get(rep), "retest": False, **t, "part_pass": part_ok,
                "hard_bin": 1 if part_ok else 2, "soft_bin": 1 if part_ok else 2,
            })
    return table(rows, MEASUREMENT_SCHEMA)


def safe_stem(*parts: str) -> str:
    return "__".join(re.sub(r"[^A-Za-z0-9._-]+", "-", p).strip("-") or "x" for p in parts)


__all__ = ["PACKET_SCHEMA", "STEP_SCHEMA", "measurement_rows", "test_name", "test_num", "table", "safe_stem",
           "_nan_to_none"]
