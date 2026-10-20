"""YAML sweep plans: load, validate (every error at once, with its path), expand to steps.

See plans/*.yaml for examples and radioyield/collector/README.md for the full schema.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from radioyield.collector.protocol import BLE_CH_ALL, expected_ble_qdbm, expected_wifi_qdbm

METRICS = ("PER", "RX_FRAC", "RSSI_MEAN", "RSSI_MEDIAN", "RSSI_STDEV", "N_RX", "N_TX")
MODES = ("OTA", "CONDUCTED")
BLE_WINDOW_MS_DEFAULT = 30  # CONFIG_RY_BLE_ADV_WINDOW_MS default; INFO reports the real one

_RADIO = {
    # channels, pwr_qdbm range, payload len range, default interval_ms
    "wifi": dict(channels=set(range(1, 14)), pwr=(8, 84), len=(18, 250), interval=5),
    "ble": dict(channels={37, 38, 39, "all"}, pwr=(-60, 80), len=(18, 18), interval=50),
    "154": dict(channels=set(range(11, 27)), pwr=(-60, 80), len=(18, 116), interval=5),
}

_TOP_KEYS = {
    "plan", "description", "build", "build_note", "station", "lot_id", "unit_serial", "ref_serial",
    "radio", "channels", "tx_power_qdbm", "tx_power_dbm", "packets_per_step", "interval_ms",
    "payload_len", "repeats", "scan_ms", "limits", "setup", "settle_ms",
}
_SETUP_KEYS = {
    "mode", "distance_m", "orientation", "obstacles", "height_m", "attenuation_db", "cable_loss_db",
    "environment", "notes", "position_label",
}


class PlanError(ValueError):
    def __init__(self, source: str, errors: list[str]):
        self.source = source
        self.errors = errors
        super().__init__(f"{source}: {len(errors)} plan error(s):\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass(frozen=True)
class Limit:
    lo: float | None = None
    hi: float | None = None


@dataclass(frozen=True)
class Step:
    index: int
    repeat: int
    radio: str
    channel: int          # BLE: 37/38/39, or 0 for "all"
    pwr_qdbm: int
    n: int
    interval_ms: int
    payload_len: int

    @property
    def channel_label(self) -> str:
        return "ALL" if (self.radio == "ble" and self.channel == BLE_CH_ALL) else str(self.channel)

    @property
    def per_meaningful(self) -> bool:
        """False for single-channel BLE steps (see PROTOCOL.md section 4)."""
        return not (self.radio == "ble" and self.channel != BLE_CH_ALL)


@dataclass
class Plan:
    name: str
    build: str
    station: str
    unit_serial: str
    radio: str
    channels: list[int]
    tx_power_qdbm: list[int]
    packets_per_step: int
    interval_ms: int
    payload_len: int
    repeats: int
    setup: dict[str, Any]
    limits: dict[str, Limit] = field(default_factory=dict)
    lot_id: str | None = None
    ref_serial: str = ""
    build_note: str = ""
    description: str = ""
    scan_ms: int = 1000
    settle_ms: int = 300
    source: str = "<dict>"
    sha256: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def mode(self) -> str:
        return self.setup["mode"]

    @property
    def data_origin(self) -> str:
        return "RIG_CONDUCTED" if self.mode == "CONDUCTED" else "RIG_OTA"

    @property
    def attenuation_total_db(self) -> float | None:
        a = self.setup.get("attenuation_db")
        return float(sum(a)) if a else None

    def steps(self) -> list[Step]:
        out = []
        for r in range(self.repeats):
            for ch in self.channels:
                for p in self.tx_power_qdbm:
                    out.append(Step(len(out), r, self.radio, ch, p, self.packets_per_step, self.interval_ms,
                                    self.payload_len))
        return out

    def estimated_seconds(self, per_step_overhead_s: float = 1.5, ble_window_ms: int = BLE_WINDOW_MS_DEFAULT) -> float:
        per_pkt = self.interval_ms / 1000.0
        if self.radio == "ble":
            per_pkt = max(per_pkt, (ble_window_ms + 5) / 1000.0)
        return len(self.steps()) * (self.packets_per_step * per_pkt + per_step_overhead_s + self.settle_ms / 1000)


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def from_dict(d: Any, source: str = "<dict>", sha256: str = "") -> Plan:
    errs: list[str] = []
    warns: list[str] = []

    if not isinstance(d, dict):
        raise PlanError(source, ["top level must be a mapping"])
    for k in sorted(set(d) - _TOP_KEYS):
        errs.append(f"unknown key '{k}' (allowed: {', '.join(sorted(_TOP_KEYS))})")

    def need_str(key, default=None, pattern_ok=True):
        v = d.get(key, default)
        if v is None:
            errs.append(f"'{key}' is required")
            return ""
        if not isinstance(v, (str, int)) or isinstance(v, bool) or str(v).strip() == "":
            errs.append(f"'{key}' must be a non-empty string, got {v!r}")
            return ""
        s = str(v).strip()
        if pattern_ok and any(c in s for c in "\n\r\t="):
            errs.append(f"'{key}' must not contain tabs, newlines or '=' ({s!r})")
        return s

    name = need_str("plan")
    build = need_str("build")
    station = need_str("station")
    unit = need_str("unit_serial")
    ref_serial = str(d.get("ref_serial", "") or "")
    lot_id = d.get("lot_id")
    if lot_id is not None:
        lot_id = need_str("lot_id")

    radio = d.get("radio")
    if radio not in _RADIO:
        errs.append(f"'radio' must be one of wifi, ble, 154 (got {radio!r})")
        spec = None
    else:
        spec = _RADIO[radio]

    # channels
    chans_raw = d.get("channels")
    channels: list[int] = []
    if not isinstance(chans_raw, list) or not chans_raw:
        errs.append("'channels' must be a non-empty list")
    elif spec:
        for i, c in enumerate(chans_raw):
            cc = c.lower() if isinstance(c, str) else c
            if radio == "ble" and cc == "all":
                channels.append(BLE_CH_ALL)
            elif _is_int(cc) and cc in spec["channels"]:
                channels.append(cc)
            else:
                allowed = {"wifi": "1..13", "ble": "37, 38, 39 or 'all'", "154": "11..26"}[radio]
                errs.append(f"channels[{i}]={c!r}: {radio} channel must be {allowed}")
        if len(set(channels)) != len(channels):
            errs.append("'channels' has duplicates")

    # power
    pwr: list[int] = []
    if "tx_power_qdbm" in d and "tx_power_dbm" in d:
        errs.append("give either 'tx_power_qdbm' or 'tx_power_dbm', not both")
    elif "tx_power_qdbm" in d or "tx_power_dbm" in d:
        key = "tx_power_qdbm" if "tx_power_qdbm" in d else "tx_power_dbm"
        raw = d[key]
        if not isinstance(raw, list) or not raw:
            errs.append(f"'{key}' must be a non-empty list")
        else:
            for i, p in enumerate(raw):
                if not _is_num(p):
                    errs.append(f"{key}[{i}]={p!r}: not a number")
                    continue
                q = p if key == "tx_power_qdbm" else p * 4
                if q != int(q):
                    errs.append(f"{key}[{i}]={p!r}: must be a whole number of 0.25 dB")
                    continue
                q = int(q)
                if spec and not spec["pwr"][0] <= q <= spec["pwr"][1]:
                    lo, hi = spec["pwr"]
                    errs.append(f"{key}[{i}]={p!r}: {radio} power must be {lo}..{hi} qdBm "
                                f"({lo / 4:g}..{hi / 4:g} dBm)")
                    continue
                if radio == "154" and q % 4:
                    errs.append(f"{key}[{i}]={p!r}: 802.15.4 power is whole dBm (qdBm multiple of 4)")
                    continue
                pwr.append(q)
            if len(set(pwr)) != len(pwr):
                errs.append(f"'{key}' has duplicates")
            if radio == "wifi" and pwr:
                acts = [expected_wifi_qdbm(q) for q in pwr]
                if len(set(acts)) != len(acts):
                    warns.append(f"WiFi powers {pwr} map to driver steps {acts}; some steps will be identical")
                for q, a in zip(pwr, acts):
                    if q != a:
                        warns.append(f"WiFi pwr_qdbm {q} will be applied as {a} (esp_wifi quantization)")
            if radio == "ble" and pwr:
                for q in pwr:
                    a = expected_ble_qdbm(q)
                    if q != a:
                        warns.append(f"BLE pwr_qdbm {q} will be applied as {a} (nearest C6 level)")
    else:
        errs.append("'tx_power_qdbm' (or 'tx_power_dbm') is required")

    def int_key(key, default, lo, hi):
        v = d.get(key, default)
        if not _is_int(v) or not lo <= v <= hi:
            errs.append(f"'{key}' must be an integer in [{lo}, {hi}] (got {v!r})")
            return default if _is_int(default) else lo
        return v

    n = int_key("packets_per_step", None, 1, 100000)
    interval = int_key("interval_ms", spec["interval"] if spec else 5, 1, 60000)
    if radio == "ble" and _is_int(interval) and interval < BLE_WINDOW_MS_DEFAULT + 5:
        errs.append(f"'interval_ms' for BLE must be >= {BLE_WINDOW_MS_DEFAULT + 5} "
                    f"(advertising window {BLE_WINDOW_MS_DEFAULT} ms + 5)")
    lmin, lmax = spec["len"] if spec else (18, 250)
    plen = int_key("payload_len", lmin, lmin, lmax)
    repeats = int_key("repeats", 1, 1, 100)
    scan_ms = int_key("scan_ms", 1000, 3, 10240)
    settle_ms = int_key("settle_ms", 300, 0, 10000)
    if _is_int(n) and n < 100:
        warns.append(f"packets_per_step={n}: PER resolution is only {100 / n:.1f}%")

    # setup
    setup_raw = d.get("setup")
    setup: dict[str, Any] = {}
    if not isinstance(setup_raw, dict):
        errs.append("'setup' mapping is required (mode, distance_m / attenuation_db, orientation, obstacles)")
    else:
        for k in sorted(set(setup_raw) - _SETUP_KEYS):
            errs.append(f"setup: unknown key '{k}' (allowed: {', '.join(sorted(_SETUP_KEYS))})")
        mode = str(setup_raw.get("mode", "")).upper()
        if mode not in MODES:
            errs.append(f"setup.mode must be OTA or CONDUCTED (got {setup_raw.get('mode')!r})")
        setup["mode"] = mode
        for k in ("orientation", "obstacles"):
            v = setup_raw.get(k)
            if v is None or str(v).strip() == "":
                errs.append(f"setup.{k} is required (write 'none' if there is nothing to record)")
            else:
                setup[k] = str(v).strip()
        for k in ("environment", "notes", "position_label"):
            if setup_raw.get(k) is not None:
                setup[k] = str(setup_raw[k]).strip()
        for k in ("distance_m", "height_m", "cable_loss_db"):
            v = setup_raw.get(k)
            if v is not None:
                if not _is_num(v) or v < 0 or (k == "distance_m" and v == 0):
                    errs.append(f"setup.{k} must be a positive number (got {v!r})")
                else:
                    setup[k] = float(v)
        att = setup_raw.get("attenuation_db")
        if att is not None:
            if not isinstance(att, list) or not att or not all(_is_num(a) and a >= 0 for a in att):
                errs.append(f"setup.attenuation_db must be a non-empty list of non-negative numbers (got {att!r})")
            else:
                setup["attenuation_db"] = [float(a) for a in att]
        if mode == "OTA" and "distance_m" not in setup:
            errs.append("setup.distance_m is required for OTA (the path-loss fit needs it)")
        if mode == "CONDUCTED" and "attenuation_db" not in setup:
            errs.append("setup.attenuation_db is required for CONDUCTED (list of fixed attenuators, dB)")
        if mode == "OTA" and "attenuation_db" in setup:
            warns.append("setup.attenuation_db given for an OTA plan; it is recorded but unusual")

    # limits
    limits: dict[str, Limit] = {}
    lraw = d.get("limits") or {}
    if not isinstance(lraw, dict):
        errs.append("'limits' must be a mapping of METRIC: {lo: x, hi: y}")
    else:
        for m, lv in lraw.items():
            if m not in METRICS:
                errs.append(f"limits: unknown metric '{m}' (allowed: {', '.join(METRICS)})")
                continue
            if not isinstance(lv, dict) or not set(lv) <= {"lo", "hi"} or not lv:
                errs.append(f"limits.{m} must be a mapping with 'lo' and/or 'hi'")
                continue
            lo, hi = lv.get("lo"), lv.get("hi")
            if (lo is not None and not _is_num(lo)) or (hi is not None and not _is_num(hi)):
                errs.append(f"limits.{m}: lo/hi must be numbers")
                continue
            if lo is not None and hi is not None and lo > hi:
                errs.append(f"limits.{m}: lo {lo} > hi {hi}")
                continue
            limits[m] = Limit(None if lo is None else float(lo), None if hi is None else float(hi))

    if errs:
        raise PlanError(source, errs)
    return Plan(
        name=name, build=build, station=station, unit_serial=unit, radio=radio, channels=channels,
        tx_power_qdbm=pwr, packets_per_step=n, interval_ms=interval, payload_len=plen, repeats=repeats,
        setup=setup, limits=limits, lot_id=lot_id, ref_serial=ref_serial,
        build_note=str(d.get("build_note", "") or ""), description=str(d.get("description", "") or ""),
        scan_ms=scan_ms, settle_ms=settle_ms, source=source, sha256=sha256, warnings=warns,
    )


def load(path: str | Path) -> Plan:
    p = Path(path)
    try:
        text = p.read_text()
    except OSError as e:
        raise PlanError(str(p), [f"cannot read: {e}"]) from None
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PlanError(str(p), [f"invalid YAML: {e}"]) from None
    return from_dict(d, source=str(p), sha256=hashlib.sha256(text.encode()).hexdigest())
