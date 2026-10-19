"""Python side of the rig line protocol (firmware/PROTOCOL.md).

Mirrors firmware/components/ry_codec for the parts the host needs: RX data lines,
OK/ERR replies, TXDONE events, and command formatting. The golden vectors in
firmware/host_tests/golden_rx_lines.txt are checked by both the C and Python tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PROTO_VERSION = 1
RADIOS = ("wifi", "ble", "154")
BLE_CH_ALL = 0
LINE_MAX = 160

#: esp_wifi_set_max_tx_power quantization, from esp_wifi.h (ESP-IDF v5.5):
#: {set value range, actual value}, 0.25 dBm units, valid set range [8, 84].
WIFI_QDBM_STEPS = ((8, 19, 8), (20, 27, 20), (28, 33, 28), (34, 43, 34), (44, 51, 44), (52, 55, 52),
                   (56, 59, 56), (60, 65, 60), (66, 71, 66), (72, 79, 72), (80, 84, 80))
#: ESP32-C6 BLE TX levels (esp_bt.h, esp_power_level_t), dBm.
BLE_DBM_LEVELS = (-15, -12, -9, -6, -3, 0, 3, 6, 9, 12, 15, 18, 20)

_KEY = re.compile(r"^[a-z0-9_]{1,15}$")
_VAL = re.compile(r"^[A-Za-z0-9_.:+-]{1,31}$")
_VERB = re.compile(r"^[A-Z]{1,11}$")


class ProtocolError(ValueError):
    pass


def line_xor(s: str) -> int:
    x = 0
    for b in s.encode("ascii"):
        x ^= b
    return x


def expected_wifi_qdbm(q: int) -> int:
    """Power the WiFi driver should apply for a set value (per the esp_wifi.h table)."""
    for lo, hi, act in WIFI_QDBM_STEPS:
        if lo <= q <= hi:
            return act
    raise ValueError(f"WiFi pwr_qdbm {q} outside [8, 84]")


def expected_ble_qdbm(q: int) -> int:
    """Nearest ESP32-C6 BLE level, ties toward the lower level (matches radio_ble.c)."""
    best = BLE_DBM_LEVELS[0]
    for lv in BLE_DBM_LEVELS[1:]:
        if abs(q - lv * 4) < abs(q - best * 4):
            best = lv
    return best * 4


# ---- RX data lines -----------------------------------------------------------------

@dataclass(frozen=True)
class RxRecord:
    radio: str
    rx_ch: int
    tx_ch: int
    run_id: int
    seq: int
    rssi: int
    pwr_qdbm: int
    t_us: int
    lqi: int
    tag: int


def format_rx_line(r: RxRecord) -> str:
    if r.radio not in RADIOS:
        raise ProtocolError(f"bad radio {r.radio!r}")
    body = (f"RX,{r.radio},{r.rx_ch},{r.tx_ch},{r.run_id},{r.seq},{r.rssi},{r.pwr_qdbm},"
            f"{r.t_us},{r.lqi},{r.tag:04x}")
    return f"{body}*{line_xor(body):02X}"


def _uint(s: str, maxv: int, name: str) -> int:
    if not s or len(s) > 20 or not s.isdigit() or not s.isascii():
        raise ProtocolError(f"{name}: not an unsigned integer: {s!r}")
    v = int(s)
    if v > maxv:
        raise ProtocolError(f"{name}: {v} > {maxv}")
    return v


def _int(s: str, lo: int, hi: int, name: str) -> int:
    neg = s.startswith("-")
    v = _uint(s[1:] if neg else s, 2**31, name)
    v = -v if neg else v
    if not lo <= v <= hi:
        raise ProtocolError(f"{name}: {v} outside [{lo}, {hi}]")
    return v


def parse_rx_line(line: str) -> RxRecord:
    """Parse one REF data line; raises ProtocolError (checksum, field count, ranges)."""
    line = line.rstrip("\r\n")
    if len(line) > LINE_MAX:
        raise ProtocolError("line too long")
    if not line.startswith("RX,"):
        raise ProtocolError("not an RX line")
    if len(line) < 4 or line[-3] != "*":
        raise ProtocolError("missing checksum")
    body, cs = line[:-3], line[-2:]
    try:
        want = int(cs, 16)
    except ValueError:
        raise ProtocolError("bad checksum digits") from None
    if not all(c in "0123456789abcdefABCDEF" for c in cs):
        raise ProtocolError("bad checksum digits")
    try:
        got = line_xor(body)
    except UnicodeEncodeError:
        raise ProtocolError("non-ASCII line") from None
    if got != want:
        raise ProtocolError(f"checksum mismatch: {got:02X} != {want:02X}")
    f = body.split(",")
    if len(f) != 11:
        raise ProtocolError(f"expected 11 fields, got {len(f)}")
    radio = f[1]
    if radio not in RADIOS:
        raise ProtocolError(f"bad radio {radio!r}")
    tag = f[10]
    if len(tag) != 4 or not all(c in "0123456789abcdefABCDEF" for c in tag):
        raise ProtocolError(f"bad tag {tag!r}")
    return RxRecord(
        radio=radio,
        rx_ch=_uint(f[2], 255, "rx_ch"),
        tx_ch=_uint(f[3], 255, "tx_ch"),
        run_id=_uint(f[4], 65535, "run_id"),
        seq=_uint(f[5], 2**32 - 1, "seq"),
        rssi=_int(f[6], -128, 127, "rssi"),
        pwr_qdbm=_int(f[7], -32768, 32767, "pwr_qdbm"),
        t_us=_uint(f[8], 2**64 - 1, "t_us"),
        lqi=_int(f[9], -1, 255, "lqi"),
        tag=int(tag, 16),
    )


# ---- replies / events -------------------------------------------------------------

@dataclass
class Reply:
    ok: bool
    verb: str
    fields: dict[str, str]
    code: str = ""
    text: str = ""
    raw: str = ""

    def int(self, key: str) -> int:
        try:
            return int(self.fields[key])
        except (KeyError, ValueError):
            raise ProtocolError(f"reply {self.raw!r} lacks integer field {key!r}") from None


def _kv(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    kv, rest = {}, []
    for t in tokens:
        if "=" in t:
            k, v = t.split("=", 1)
            kv[k] = v
        else:
            rest.append(t)
    return kv, rest


def parse_reply(line: str) -> Reply:
    """'OK <VERB> k=v ...' or 'ERR <VERB> <CODE> text...'."""
    line = line.strip()
    toks = line.split()
    if len(toks) < 2 or toks[0] not in ("OK", "ERR"):
        raise ProtocolError(f"not a reply: {line!r}")
    if toks[0] == "OK":
        kv, rest = _kv(toks[2:])
        if rest:
            kv["_arg"] = " ".join(rest)
        return Reply(True, toks[1], kv, raw=line)
    code = toks[2] if len(toks) > 2 else ""
    text = " ".join(toks[3:])
    kv, _ = _kv(toks[3:])
    return Reply(False, toks[1], kv, code=code, text=text, raw=line)


def parse_event(line: str) -> tuple[str, dict[str, str]]:
    """'TXDONE k=v ...' / 'BOOT k=v ...' -> (name, fields)."""
    toks = line.strip().split()
    if not toks:
        raise ProtocolError("empty event")
    kv, _ = _kv(toks[1:])
    return toks[0], kv


def classify(line: str) -> str:
    """'rx' | 'reply' | 'event' | 'noise' (anything else is ignored per the spec)."""
    if line.startswith("RX,"):
        return "rx"
    if line.startswith("OK ") or line.startswith("ERR "):
        return "reply"
    if line.startswith("TXDONE ") or line.startswith("BOOT "):
        return "event"
    return "noise"


def format_command(verb: str, arg: str | None = None, **kv) -> str:
    """Build a command line, validating it against the grammar in PROTOCOL.md."""
    if not _VERB.match(verb):
        raise ProtocolError(f"bad verb {verb!r}")
    parts = [verb]
    if arg is not None:
        if not _KEY.match(arg):
            raise ProtocolError(f"bad arg {arg!r}")
        parts.append(arg)
    for k, v in kv.items():
        v = str(v)
        if not _KEY.match(k) or not _VAL.match(v):
            raise ProtocolError(f"bad key/value {k}={v!r}")
        parts.append(f"{k}={v}")
    line = " ".join(parts)
    if len(line) > LINE_MAX or len(kv) > 12:
        raise ProtocolError("command too long")
    return line
