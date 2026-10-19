from pathlib import Path

import pytest

from radioyield.collector import protocol as P

GOLDEN = Path(__file__).resolve().parents[2] / "firmware" / "host_tests" / "golden_rx_lines.txt"


def golden():
    out = []
    for raw in GOLDEN.read_text().splitlines():
        if not raw or raw.startswith("#"):
            continue
        line, *f = raw.split("|")
        rec = P.RxRecord(f[0], int(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5]), int(f[6]), int(f[7]),
                         int(f[8]), int(f[9], 16))
        out.append((line, rec))
    return out


def test_golden_lines_match_c_codec():
    g = golden()
    assert len(g) >= 3
    for line, rec in g:
        assert P.format_rx_line(rec) == line
        assert P.parse_rx_line(line) == rec
        assert P.parse_rx_line(line + "\r\n") == rec


def good():
    return golden()[0][0]


def with_cs(body: str) -> str:
    return f"{body}*{P.line_xor(body):02X}"


@pytest.mark.parametrize("line", [
    "",
    "RX",
    "OK INFO role=REF",
    "RX,wifi,6,6,7,123,-45,40,1234567,-1,beef",          # no checksum
    "RX,wifi,6,6,7,123,-45,40,1234567,-1,beef*ZZ",
    with_cs("RX,wifi,6,6,7,123,-45,40,1234567,-1"),
    with_cs("RX,wifi,6,6,7,123,-45,40,1234567,-1,beef,x"),
    with_cs("RX,zigbee,6,6,7,123,-45,40,1234567,-1,beef"),
    with_cs("RX,wifi,6,6,7,123,-200,40,1234567,-1,beef"),
    with_cs("RX,wifi,6,6,70000,123,-45,40,1234567,-1,beef"),
    with_cs("RX,wifi,6,6,7,4294967296,-45,40,1234567,-1,beef"),
    with_cs("RX,wifi,6,6,7,123,-45,40,1234567,-1,beeg"),
    with_cs("RX,wifi,,6,7,123,-45,40,1234567,-1,beef"),
    with_cs("RX,wifi,6,6,7,123,--45,40,1234567,-1,beef"),
    with_cs("RX,wifi,6,6,7,123,-45,40,1234567,-2,beef"),
    with_cs("RX,wifi,6,6,7,12a,-45,40,1,-1,beef"),
])
def test_malformed_rx_lines_rejected(line):
    with pytest.raises(P.ProtocolError):
        P.parse_rx_line(line)


def test_single_byte_corruption_always_caught():
    line = good()
    body_len = len(line) - 3
    for i in range(body_len):
        for c in "0123456789,-abcRXz":
            if line[i] == c:
                continue
            bad = line[:i] + c + line[i + 1:]
            with pytest.raises(P.ProtocolError):
                P.parse_rx_line(bad)


def test_non_ascii_rejected():
    with pytest.raises(P.ProtocolError):
        P.parse_rx_line("RX,wifi,6,6,7,123,-45,40,1234567,-1,beéf*00")


def test_replies_and_events():
    r = P.parse_reply("OK SET radio=wifi ch=6 pwr_qdbm=40 pwr_act_qdbm=34 len=32")
    assert r.ok and r.verb == "SET" and r.int("pwr_act_qdbm") == 34
    e = P.parse_reply("ERR SET RANGE key=ch")
    assert not e.ok and e.code == "RANGE" and e.fields["key"] == "ch"
    q = P.parse_reply("ERR ? SYNTAX")
    assert q.verb == "?" and q.code == "SYNTAX"
    s = P.parse_reply("OK RX stop ok=10 bad=0 other=2 ovf=0")
    assert s.fields["_arg"] == "stop" and s.int("other") == 2
    with pytest.raises(P.ProtocolError):
        r.int("nope")
    with pytest.raises(P.ProtocolError):
        P.parse_reply("HELLO there")
    name, kv = P.parse_event("TXDONE run=7 n=500 sent=500 ok=499 fail=1 aborted=0")
    assert name == "TXDONE" and kv["fail"] == "1"
    assert P.classify("W (123) wifi: blah") == "noise"
    assert P.classify("BOOT role=DUT") == "event"


def test_format_command_validates():
    assert P.format_command("SET", radio="ble", ch="all", pwr_qdbm=-60) == "SET radio=ble ch=all pwr_qdbm=-60"
    assert P.format_command("RX", "start") == "RX start"
    for bad in [dict(verb="set"), dict(verb="SET", kv={"Ch": 1}), dict(verb="SET", kv={"ch": "6 7"}),
                dict(verb="SET", kv={"ch": ""}), dict(verb="SET", kv={"x": "a" * 40})]:
        with pytest.raises(P.ProtocolError):
            P.format_command(bad["verb"], **bad.get("kv", {}))


def test_power_quantization_tables():
    assert P.expected_wifi_qdbm(8) == 8
    assert P.expected_wifi_qdbm(40) == 34
    assert P.expected_wifi_qdbm(84) == 80
    with pytest.raises(ValueError):
        P.expected_wifi_qdbm(7)
    assert P.expected_ble_qdbm(36) == 36
    assert P.expected_ble_qdbm(-100) == -60
    assert P.expected_ble_qdbm(78) == 80
    assert P.expected_ble_qdbm(-54) == -60  # tie between -15 and -12 dBm goes down
