"""End to end through the serial test double (no boards). Everything here is SIMULATED."""
import copy
import importlib.util
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from radioyield.collector import outputs as O
from radioyield.collector import plan as PL
from radioyield.collector.cli import main as cli_main
from radioyield.collector.fake import FakeAir, Faults, LinkModel
from radioyield.collector.runner import run_plan
from radioyield.collector.transport import Board, BoardError
from radioyield.schema import MEASUREMENT_SCHEMA, validate

BASE = {
    "plan": "e2e", "build": "EVT1", "station": "S1", "unit_serial": "DUT-07", "radio": "wifi",
    "channels": [1, 6, 11], "tx_power_qdbm": [80, 44], "packets_per_step": 200, "settle_ms": 0,
    "setup": {"mode": "OTA", "distance_m": 2, "orientation": "upright", "obstacles": "none"},
    "limits": {"PER": {"hi": 5}, "RSSI_MEDIAN": {"lo": -80}},
}


class CaptureWriter:
    def __init__(self):
        self.calls = []

    def __call__(self, path, table, meta):
        validate(table)
        self.calls.append((Path(path), table, meta))
        Path(path).write_bytes(b"fake-stdf")


def boards(air=None, swap=False, offset=0):
    air = air or FakeAir(seed=3)
    d = Board(air.board("DUT", 0x11, pwr_offset_qdbm=offset), "DUT", simulated=True)
    r = Board(air.board("REF", 0x22), "REF", simulated=True)
    return (r, d) if swap else (d, r)


_DEFAULT = object()


def run(tmp_path, plan_over=None, air=None, offset=0, writer=_DEFAULT):
    d = copy.deepcopy(BASE)
    d.update(plan_over or {})
    plan = PL.from_dict(d, source="e2e.yaml")
    dut, ref = boards(air, offset=offset)
    try:
        res = run_plan(plan, dut, ref, tmp_path, stdf_writer=CaptureWriter() if writer is _DEFAULT else writer,
                       progress=None,
                       run_id_seed=5)
    finally:
        dut.close()
        ref.close()
    return res


def test_wifi_sweep_outputs(tmp_path):
    w = CaptureWriter()
    res = run(tmp_path, writer=w)
    assert res.complete and res.meta["data_origin"] == "SIMULATED"
    assert len(res.steps) == 6 and res.n_packets > 0
    # STDF writer contract
    (path, table, meta), = w.calls
    assert path.suffix == ".stdf"
    for k in ("lot_id", "station", "build", "data_origin"):
        assert meta[k]
    assert meta["data_origin"] == "SIMULATED" and meta["mode"] == "OTA" and meta["distance_m"] == "2"
    assert meta["JOB_NAM"] == "e2e" and "SIMULATED" in meta["TSTR_TYP"]
    assert all(isinstance(v, str) for v in meta.values())
    df = table.to_pandas()
    assert set(df.data_origin) == {"SIMULATED"} and set(df.part_id) == {"DUT-07"}
    assert "WIFI_CH6_P80_PER" in set(df.test_name) and "WIFI_CH11_P44_RSSI_MEDIAN" in set(df.test_name)
    per = df[df.test_name == "WIFI_CH6_P80_PER"].iloc[0]
    assert per.units == "%" and per.hi_limit == 5 and per.lo_limit != per.lo_limit  # NaN/None lo
    assert df.test_num.is_unique  # one part, so every test once
    # parquet files
    pk = pq.read_table(res.files["packets"])
    assert pk.schema.equals(O.PACKET_SCHEMA, check_metadata=False)
    assert pk.num_rows == res.n_packets
    assert pk.schema.metadata[b"data_origin"] == b"SIMULATED"
    st = pq.read_table(res.files["steps"]).to_pandas()
    assert list(st.pwr_act_qdbm.unique()) == [80, 44]
    assert (st.n_onair == 200).all()
    m = pq.read_table(res.files["measurements"])
    assert m.schema.equals(MEASUREMENT_SCHEMA, check_metadata=False)
    j = json.loads(res.files["json"].read_text())
    assert j["meta"]["data_origin"] == "SIMULATED" and j["plan"]["radio"] == "wifi"


def test_per_matches_ground_truth_under_protocol_faults(tmp_path):
    """Duplicates, reorders and log noise must not change PER; we compare against the
    loss pattern implied by the REF's own (deduplicated) output."""
    air = FakeAir(LinkModel(distance_m=54, exponent=3.0), Faults(p_dup=0.05, p_reorder=0.05, p_noise_line=0.05),
                  seed=9)
    res = run(tmp_path, {"channels": [6], "tx_power_qdbm": [8, 80], "packets_per_step": 400, "limits": {}}, air=air)
    pk = pq.read_table(res.files["packets"]).to_pandas()
    for s in res.steps:
        rows = pk[pk.step_idx == s["step_idx"]]
        uniq = rows.seq.nunique()
        assert s["n_rx_unique"] == uniq
        assert s["n_lost"] == 400 - uniq
        assert s["n_dup"] == int(rows.dup.sum())
        assert s["per_valid"]
    weak, strong = res.steps
    assert 5 < weak["per_pct"] < 95 and strong["per_pct"] < weak["per_pct"]  # toy waterfall shows up
    assert all(s["n_dup"] > 0 for s in res.steps) and any(s["n_reorder"] > 0 for s in res.steps)


def test_corrupted_lines_and_overflow_invalidate_per(tmp_path):
    air = FakeAir(faults=Faults(p_corrupt_line=0.02), seed=4)
    res = run(tmp_path, {"channels": [6], "tx_power_qdbm": [80]}, air=air)
    s = res.steps[0]
    assert s["host_bad_lines"] > 0 and not s["per_valid"]
    assert any("PER invalid" in w for w in res.warnings)
    air = FakeAir(faults=Faults(ref_queue_cap=50), seed=4)
    w = CaptureWriter()
    res = run(tmp_path / "b", {"channels": [6], "tx_power_qdbm": [80]}, air=air, writer=w)
    s = res.steps[0]
    assert s["ref_ovf"] == 150 and not s["per_valid"]
    names = set(w.calls[0][1].to_pandas().test_name)
    assert "WIFI_CH6_P80_PER" not in names     # an invalid PER is not written as a measurement
    assert "WIFI_CH6_P80_RSSI_MEDIAN" in names


def test_tx_failures_are_not_rf_loss(tmp_path):
    air = FakeAir(LinkModel(distance_m=1), Faults(p_tx_fail=0.1), seed=2)
    res = run(tmp_path, {"channels": [6], "tx_power_qdbm": [80]}, air=air)
    s = res.steps[0]
    assert s["n_tx_fail"] > 0 and s["n_onair"] == 200 - s["n_tx_fail"]
    assert s["per_pct"] == 0 and "tx_fail" in s["flags"]


def test_ble_single_channel_reports_rx_frac(tmp_path):
    w = CaptureWriter()
    res = run(tmp_path, {"radio": "ble", "channels": ["all", 38], "tx_power_qdbm": [36], "interval_ms": 40},
              writer=w)
    names = set(w.calls[0][1].to_pandas().test_name)
    assert "BLE_CHALL_P36_PER" in names and "BLE_CH38_P36_RX_FRAC" in names
    assert "BLE_CH38_P36_PER" not in names
    pk = pq.read_table(res.files["packets"]).to_pandas()
    assert pk.rx_channel.isna().all()          # BLE: stack does not report the channel
    assert res.meta["ble_rx_channel"].startswith("not reported")


def test_ieee154_and_negative_power_names(tmp_path):
    w = CaptureWriter()
    run(tmp_path, {"radio": "154", "channels": [11, 26], "tx_power_qdbm": [-20, 80]}, writer=w)
    names = set(w.calls[0][1].to_pandas().test_name)
    assert "IEEE154_CH26_PN20_PER" in names and "IEEE154_CH11_P80_RSSI_MEAN" in names


def test_repeats_make_parts_and_limits_bin(tmp_path):
    w = CaptureWriter()
    air = FakeAir(LinkModel(distance_m=60, exponent=3.5), seed=1)
    run(tmp_path, {"channels": [6], "tx_power_qdbm": [8], "repeats": 3, "limits": {"PER": {"hi": 1}}},
        air=air, writer=w)
    df = w.calls[0][1].to_pandas()
    assert df.part_ts.nunique() == 3 and (df.part_id == "DUT-07").all()
    per = df[df.test_name == "WIFI_CH6_P8_PER"]
    assert len(per) == 3 and (~per.test_pass).all() and (~per.part_pass).all() and (per.hard_bin == 2).all()


def test_build_offset_shows_in_actual_power(tmp_path):
    res = run(tmp_path, {"channels": [6], "tx_power_qdbm": [80], "build": "EVT2"}, offset=-8)
    assert res.steps[0]["pwr_act_qdbm"] == 72 and res.steps[0]["pwr_cmd_qdbm"] == 80
    assert res.meta["dut_pwr_offset_qdbm"] == "-8" and res.meta["build"] == "EVT2"


def test_swapped_ports_are_caught(tmp_path):
    plan = PL.from_dict(copy.deepcopy(BASE))
    dut, ref = boards(swap=True)
    with pytest.raises(BoardError, match="swap the ports"):
        run_plan(plan, dut, ref, tmp_path, stdf_writer=CaptureWriter(), progress=None)
    dut.close(); ref.close()


def test_stdf_failure_keeps_data(tmp_path):
    def broken(path, table, meta):
        raise RuntimeError("writer exploded")
    res = run(tmp_path, {"channels": [6], "tx_power_qdbm": [80]}, writer=broken)
    assert res.stdf_error and "stdf" not in res.files
    assert res.files["measurements"].exists() and res.files["packets"].exists()


def test_fake_can_never_claim_rig_origin(tmp_path):
    # even a CONDUCTED plan run through the double is SIMULATED
    res = run(tmp_path, {"setup": {"mode": "CONDUCTED", "attenuation_db": [30, 30], "orientation": "n/a",
                                   "obstacles": "n/a"}, "channels": [6], "tx_power_qdbm": [80]})
    assert res.meta["data_origin"] == "SIMULATED"
    pk = pq.read_table(res.files["packets"]).to_pandas()
    assert set(pk.data_origin) == {"SIMULATED"} and pk.attenuation_total_db.iloc[0] == 60


def test_cli_fake_run_and_validate(tmp_path, capsys):
    plans = sorted(str(p) for p in (Path(__file__).resolve().parents[2] / "plans").glob("*.yaml"))
    assert cli_main(["validate", *plans]) == 0
    out = tmp_path / "o"
    smoke = next(p for p in plans if p.endswith("smoke_wifi_1m.yaml"))
    rc = cli_main(["run", smoke, "--fake", "--out", str(out), "--unit", "DUT-99", "--distance", "3",
                   "--position", "tape-3m"])
    text = capsys.readouterr().out
    assert "SIMULATED" in text
    assert rc in (0, 3)  # 3 = STDF writer unavailable, data still written
    logs = list(out.glob("*.serial.log"))
    assert logs and "INFO" in logs[0].read_text()
    meta = json.loads(next(out.glob("*.json")).read_text())["meta"]
    assert meta["unit_serial"] == "DUT-99" and meta["position_label"] == "tape-3m"


HAVE_STDF = importlib.util.find_spec("radioyield.stdf._stdf") is not None


@pytest.mark.skipif(not HAVE_STDF, reason="radioyield.stdf C++ extension not built")
def test_real_stdf_round_trip(tmp_path):
    from radioyield.stdf import read

    res = run(tmp_path, {"channels": [1, 6], "tx_power_qdbm": [80, 44], "repeats": 2}, writer=None)
    assert "stdf" in res.files, res.stdf_error
    f = read(res.files["stdf"])
    t = f.measurements if hasattr(f, "measurements") else f
    df = t.to_pandas()
    m = pq.read_table(res.files["measurements"]).to_pandas()
    assert len(df) == len(m)
    assert set(df.data_origin) == {"SIMULATED"} and set(df.build) == {"EVT1"}
    merged = df.merge(m, on=["test_num", "part_ts"], suffixes=("_stdf", "_pq"))
    assert len(merged) == len(m)
    assert ((merged.result_stdf - merged.result_pq).abs() < 1e-3).all()   # STDF stores float32
    meta = f.meta
    assert meta.get("distance_m") == "2" and meta.get("mode") == "OTA" and meta.get("run_uid")
