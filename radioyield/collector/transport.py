"""Serial transport: one Board per USB port, a reader thread that sorts lines into
replies, events and RX data, and a transcript of every line in both directions.

The port object only needs pyserial's ``write(bytes)``, ``readline() -> bytes`` (returning
b"" on timeout) and ``close()``; radioyield.collector.fake.FakeSerial provides the same.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import IO, Any

from radioyield.collector import protocol as P


class BoardError(RuntimeError):
    pass


@dataclass
class BoardInfo:
    role: str
    fw: str
    proto: int
    idf: str
    mac: str
    tag: int
    radios: tuple[str, ...]
    pwr_offset_qdbm: int
    label: str
    ble_window_ms: int
    raw: str

    @classmethod
    def from_reply(cls, r: P.Reply) -> "BoardInfo":
        f = r.fields
        try:
            return cls(role=f["role"], fw=f.get("fw", "?"), proto=int(f.get("proto", 0)), idf=f.get("idf", "?"),
                       mac=f.get("mac", "?"), tag=int(f.get("tag", "0"), 16),
                       radios=tuple(x for x in f.get("radios", "").split(",") if x),
                       pwr_offset_qdbm=int(f.get("pwr_offset_qdbm", 0)), label=f.get("label", "none"),
                       ble_window_ms=int(f.get("ble_window_ms", 30)), raw=r.raw)
        except (KeyError, ValueError) as e:
            raise BoardError(f"unparseable INFO reply {r.raw!r}: {e}") from None


def open_serial(port: str, baud: int = 115200, timeout: float = 0.1):
    """Open a real port without toggling DTR/RTS (which can reset an ESP32-C6 on USB-JTAG)."""
    import serial  # pyserial, optional dependency (radioyield[rig])

    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = timeout
    s.dtr = False
    s.rts = False
    s.open()
    return s


class Board:
    def __init__(self, port: Any, name: str, transcript: IO[str] | None = None, simulated: bool = False):
        self.port = port
        self.name = name
        self.simulated = simulated
        self.info: BoardInfo | None = None
        self._transcript = transcript
        self._tlock = threading.Lock()
        self._replies: queue.Queue[P.Reply] = queue.Queue()
        self._events: queue.Queue[tuple[str, dict[str, str], str]] = queue.Queue()
        self._rx: list[tuple[float, str]] = []
        self._rx_lock = threading.Lock()
        self._stop = threading.Event()
        self._buf = b""
        self.noise_lines = 0
        self._thread = threading.Thread(target=self._reader, name=f"ry-reader-{name}", daemon=True)
        self._thread.start()

    # ---- plumbing ---------------------------------------------------------------
    def _log(self, direction: str, line: str) -> None:
        if self._transcript is not None:
            with self._tlock:
                self._transcript.write(f"{time.time():.6f} {self.name} {direction} {line}\n")

    def _reader(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self.port.readline()
            except Exception as e:  # port vanished (USB unplugged)
                self._events.put(("PORT_ERROR", {"error": str(e)}, str(e)))
                return
            if not chunk:
                continue
            self._buf += chunk
            if not self._buf.endswith(b"\n"):
                if len(self._buf) > 4096:
                    self._buf = b""
                continue
            raw, self._buf = self._buf, b""
            line = raw.decode("ascii", errors="replace").rstrip("\r\n")
            now = time.time()
            self._log("<", line)
            kind = P.classify(line)
            if kind == "rx":
                with self._rx_lock:
                    self._rx.append((now, line))
            elif kind == "reply":
                try:
                    self._replies.put(P.parse_reply(line))
                except P.ProtocolError:
                    self.noise_lines += 1
            elif kind == "event":
                name, kv = P.parse_event(line)
                self._events.put((name, kv, line))
            else:
                self.noise_lines += 1

    def close(self) -> None:
        self._stop.set()
        try:
            self.port.close()
        finally:
            self._thread.join(timeout=2)

    # ---- API ----------------------------------------------------------------------
    def send(self, line: str) -> None:
        self._log(">", line)
        self.port.write((line + "\n").encode("ascii"))

    def command(self, line: str, timeout: float = 3.0, check: bool = True) -> P.Reply:
        verb = line.split()[0]
        # discard stale replies (e.g. from a command that timed out earlier)
        while True:
            try:
                self._replies.get_nowait()
            except queue.Empty:
                break
        self.send(line)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise BoardError(f"{self.name}: no reply to {line!r} within {timeout:.1f} s")
            try:
                r = self._replies.get(timeout=left)
            except queue.Empty:
                continue
            if r.verb not in (verb, "?"):
                continue
            if check and not r.ok:
                raise BoardError(f"{self.name}: {line!r} -> {r.raw}")
            return r

    def wait_event(self, name: str, timeout: float) -> dict[str, str]:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise BoardError(f"{self.name}: no {name} within {timeout:.1f} s")
            try:
                ev, kv, raw = self._events.get(timeout=left)
            except queue.Empty:
                continue
            if ev == "PORT_ERROR":
                raise BoardError(f"{self.name}: serial port error: {kv.get('error')}")
            if ev == name:
                return kv

    def take_rx(self) -> list[tuple[float, str]]:
        with self._rx_lock:
            out, self._rx = self._rx, []
        return out

    def handshake(self, tries: int = 4, timeout: float = 1.5) -> BoardInfo:
        """Resync (blank line) and query INFO; tolerates a board that is still booting."""
        last: Exception | None = None
        for _ in range(tries):
            try:
                self.send("")
                r = self.command("INFO", timeout=timeout)
                self.info = BoardInfo.from_reply(r)
                if self.info.proto != P.PROTO_VERSION:
                    raise BoardError(f"{self.name}: firmware protocol {self.info.proto}, host expects "
                                     f"{P.PROTO_VERSION}")
                return self.info
            except BoardError as e:
                last = e
                if "protocol" in str(e):
                    raise
        raise BoardError(f"{self.name}: no INFO reply after {tries} tries ({last})")
