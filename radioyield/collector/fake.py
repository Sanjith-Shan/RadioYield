"""Serial test double: a fake DUT and REF speaking the rig line protocol over a toy radio
channel, so the collector can be tested end to end with no boards.

THIS IS A PROTOCOL TEST DOUBLE, NOT A MEASUREMENT. Its channel model (log-distance path
loss, Gaussian shadowing, a logistic PER waterfall) uses round-number parameters picked
for testing; none come from a datasheet or a measurement. Any file produced through it is
labelled data_origin=SIMULATED by the runner, never RIG_*.
"""
from __future__ import annotations

import math
import queue
import random
from dataclasses import dataclass, field
from typing import Callable

from radioyield.collector import protocol as P

FAKE_FW = "0.1.0-fake"


@dataclass
class LinkModel:
    """Toy link. All values are arbitrary test-double parameters (not datasheet values)."""

    distance_m: float = 2.0
    exponent: float = 2.2
    pl_1m_db: float = 40.0           # ~free-space loss at 1 m, 2.4 GHz (rounded)
    extra_loss_db: float = 0.0       # walls, body, orientation
    conducted_loss_db: float | None = None  # if set, replaces the path loss (attenuators + cable)
    shadow_sigma_db: float = 2.0     # per (step) slow fading
    fast_sigma_db: float = 1.0       # per packet
    sens_dbm: dict[str, float] = field(default_factory=lambda: {"wifi": -90.0, "ble": -93.0, "154": -96.0})
    waterfall_db: float = 1.0        # logistic scale of the PER waterfall
    rssi_floor_dbm: int = -100       # receiver does not report below this
    # Hooks for the virtual rig (radioyield.sim.rig); defaults leave the toy model unchanged.
    static_db: Callable[[str, int], float] | None = None  # fixed gain per (radio, ch) at this position
    tx_offset_db: float = 0.0        # this DUT's true TX power error vs its table
    rssi_bias_db: float = 0.0        # REF's RSSI reporting error (RSSI = level + bias)

    def path_loss_db(self) -> float:
        if self.conducted_loss_db is not None:
            return self.conducted_loss_db
        return self.pl_1m_db + 10 * self.exponent * math.log10(max(self.distance_m, 0.1)) + self.extra_loss_db

    def p_loss(self, radio: str, level_dbm: float) -> float:
        z = (level_dbm - self.sens_dbm[radio]) / self.waterfall_db
        return 1.0 / (1.0 + math.exp(min(60.0, z)))


@dataclass
class Faults:
    """Protocol-level faults to exercise the collector."""

    p_dup: float = 0.0           # REF prints a packet twice
    p_reorder: float = 0.0       # swap with the previous RX line
    p_corrupt_line: float = 0.0  # flip a character (checksum must catch it)
    p_noise_line: float = 0.0    # insert an unrelated log line
    p_tx_fail: float = 0.0       # DUT driver reports failure (packet not sent)
    ref_queue_cap: int | None = None  # REF drops (ovf) beyond this many lines per RX session


class FakeAir:
    def __init__(self, link: LinkModel | None = None, faults: Faults | None = None, seed: int = 1):
        self.link = link or LinkModel()
        self.faults = faults or Faults()
        self.rng = random.Random(seed)
        self.refs: list["FakeSerial"] = []
        self.clock_us = 1_000_000

    def board(self, role: str, mac_last: int = 1, pwr_offset_qdbm: int = 0) -> "FakeSerial":
        b = FakeSerial(self, role, mac_last, pwr_offset_qdbm)
        if role == "REF":
            self.refs.append(b)
        return b

    def transmit(self, dut: "FakeSerial", run_id: int, n: int, interval_ms: int) -> tuple[int, int]:
        """Deliver n packets; returns (ok, fail)."""
        L, F, rng = self.link, self.faults, self.rng
        radio, ch = dut.radio, dut.ch
        shadow = rng.gauss(0.0, L.shadow_sigma_db) + L.tx_offset_db
        if L.static_db is not None:
            shadow += L.static_db(radio, ch)
        ok = fail = 0
        for seq in range(n):
            self.clock_us += interval_ms * 1000
            if rng.random() < F.p_tx_fail:
                fail += 1
                continue
            ok += 1
            for ref in self.refs:
                if not ref.rx_on or ref.radio != radio:
                    continue
                if radio != "ble" and ref.ch != ch:
                    continue
                level = dut.pwr_act / 4.0 - L.path_loss_db() + shadow + rng.gauss(0.0, L.fast_sigma_db)
                if rng.random() < L.p_loss(radio, level):
                    continue
                if radio == "ble" and ch != P.BLE_CH_ALL and rng.random() > 1 / 3:
                    continue  # REF scan parked on another advertising channel
                rssi = max(L.rssi_floor_dbm, int(round(level + L.rssi_bias_db)))
                rx_ch = 0 if radio == "ble" else ch
                rec = P.RxRecord(radio, rx_ch, ch, run_id, seq, rssi, dut.pwr_cmd, self.clock_us + 300,
                                 rng.randint(0, 255) if radio == "154" else -1, dut.tag)
                ref.deliver(P.format_rx_line(rec))
                if rng.random() < F.p_dup:
                    ref.deliver(P.format_rx_line(rec))
        return ok, fail


class FakeSerial:
    """pyserial-like object: write(bytes), readline() -> bytes (b'' on timeout), close()."""

    def __init__(self, air: FakeAir, role: str, mac_last: int, pwr_offset_qdbm: int = 0, timeout: float = 0.05):
        self.air = air
        self.role = role
        self.timeout = timeout
        self.mac = f"02:00:00:00:00:{mac_last:02x}"
        self.tag = mac_last & 0xFFFF
        self.pwr_offset_qdbm = pwr_offset_qdbm
        self.radio: str | None = None
        self.ch = 0
        self.pwr_cmd = 0
        self.pwr_act = 0
        self.len = 18
        self.rx_on = False
        self._q: queue.Queue[bytes] = queue.Queue()
        self._inbuf = b""
        self._closed = False
        self._rx_count = self._ovf = 0
        self._last_rx_line: str | None = None
        self._held: str | None = None
        self._out("ESP-ROM:esp32c6-20220919 (fake boot noise)")
        self._out(f"BOOT role={role} fw={FAKE_FW} proto={P.PROTO_VERSION}")

    # ---- pyserial surface ----------------------------------------------------------
    def write(self, data: bytes) -> int:
        self._inbuf += data
        while b"\n" in self._inbuf:
            line, self._inbuf = self._inbuf.split(b"\n", 1)
            self._handle(line.decode("ascii", errors="replace").strip())
        return len(data)

    def readline(self) -> bytes:
        if self._closed:
            return b""
        try:
            return self._q.get(timeout=self.timeout)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self._closed = True

    # ---- device side -----------------------------------------------------------------
    def _out(self, line: str) -> None:
        F, rng = self.air.faults, self.air.rng
        if F.p_noise_line and rng.random() < F.p_noise_line:
            self._q.put(b"W (12345) wifi: fake log noise\n")
        self._q.put((line + "\n").encode("ascii"))

    def deliver(self, line: str) -> None:
        F, rng = self.air.faults, self.air.rng
        if F.ref_queue_cap is not None and self._rx_count >= F.ref_queue_cap:
            self._ovf += 1
            return
        self._rx_count += 1
        if F.p_corrupt_line and rng.random() < F.p_corrupt_line:
            i = rng.randrange(3, len(line) - 3)
            line = line[:i] + ("7" if line[i] != "7" else "8") + line[i + 1:]
        if F.p_reorder and self._last_rx_line is not None and rng.random() < F.p_reorder:
            # hold this line back one slot: emit it after the next one
            self._held, self._last_rx_line = line, None
            return
        self._out(line)
        held = self._held
        if held:
            self._held = None
            self._out(held)
        self._last_rx_line = line

    def _err(self, verb: str, code: str, text: str = "") -> None:
        self._out(f"ERR {verb} {code} {text}".rstrip())

    def _handle(self, line: str) -> None:
        if not line:
            return
        toks = line.split()
        verb = toks[0]
        if not verb.isalpha() or not verb.isupper():
            return self._err("?", "SYNTAX")
        kv = dict(t.split("=", 1) for t in toks[1:] if "=" in t)
        arg = next((t for t in toks[1:] if "=" not in t), "")
        if verb == "INFO":
            return self._out(
                f"OK INFO role={self.role} fw={FAKE_FW} proto={P.PROTO_VERSION} idf=SIMULATED chip=esp32c6 "
                f"mac={self.mac} tag={self.tag:04x} radios=wifi,ble,154 pwr_offset_qdbm={self.pwr_offset_qdbm} "
                f"label=SIMULATED ble_window_ms=30 ble_itvl_ms=100")
        if verb == "PING":
            return self._out(f"OK PING t_us={self.air.clock_us}")
        if verb == "STOP":
            return self._out("OK STOP")
        if verb == "SET":
            return self._set(kv)
        if verb == "TX":
            if self.role != "DUT":
                return self._err("TX", "ROLE", "this board is a REF")
            if self.radio is None:
                return self._err("TX", "STATE", "no radio selected")
            try:
                n, itv, run = int(kv["n"]), int(kv["interval_ms"]), int(kv["run"])
            except (KeyError, ValueError):
                return self._err("TX", "MISSING")
            self._out(f"OK TX run={run} n={n} interval_ms={itv}")
            ok, fail = self.air.transmit(self, run, n, itv)
            return self._out(
                f"TXDONE run={run} n={n} sent={n} ok={ok} fail={fail} aborted=0 dur_ms={n * itv} radio={self.radio} "
                f"ch={self._chs()} pwr_qdbm={self.pwr_cmd} pwr_act_qdbm={self.pwr_act} len={self.len} err=none")
        if verb == "RX":
            if self.role != "REF":
                return self._err("RX", "ROLE", "this board is a DUT")
            if arg == "start":
                if self.radio is None:
                    return self._err("RX", "STATE", "no radio selected")
                self.rx_on = True
                self._rx_count = self._ovf = 0
                return self._out(f"OK RX start radio={self.radio} ch={self._chs()}")
            if arg == "stop":
                self.rx_on = False
                held = self._held
                if held:
                    self._held = None
                    self._out(held)
                return self._out(f"OK RX stop ok={self._rx_count} bad=0 other=0 ovf={self._ovf}")
            return self._err("RX", "SYNTAX")
        return self._err(verb, "UNKNOWN", "verb")

    def _chs(self) -> str:
        return "all" if (self.radio == "ble" and self.ch == P.BLE_CH_ALL) else str(self.ch)

    def _set(self, kv: dict[str, str]) -> None:
        radio = kv.get("radio")
        if radio not in P.RADIOS:
            return self._err("SET", "RANGE", "key=radio")
        try:
            ch = P.BLE_CH_ALL if (radio == "ble" and kv.get("ch") == "all") else int(kv["ch"])
            pwr = int(kv.get("pwr_qdbm", 80 if radio == "wifi" else 0))
            ln = int(kv.get("len", 18))
        except (KeyError, ValueError):
            return self._err("SET", "MISSING", "key=ch")
        lo, hi = {"wifi": (8, 84), "ble": (-60, 80), "154": (-60, 80)}[radio]
        if not lo <= pwr <= hi:
            return self._err("SET", "RANGE", "key=pwr_qdbm")
        eff = min(hi, max(lo, pwr + self.pwr_offset_qdbm))
        if radio == "wifi":
            act = P.expected_wifi_qdbm(eff)
        elif radio == "ble":
            act = P.expected_ble_qdbm(eff)
        else:
            act = int(eff / 4) * 4
        self.radio, self.ch, self.pwr_cmd, self.pwr_act, self.len = radio, ch, pwr, act, ln
        self.rx_on = False
        self._out(f"OK SET radio={radio} ch={self._chs()} pwr_qdbm={pwr} pwr_act_qdbm={act} len={ln} scan_ms=1000")
