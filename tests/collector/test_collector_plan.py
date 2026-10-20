import copy
from pathlib import Path

import pytest

from radioyield.collector import plan as PL

PLANS = sorted((Path(__file__).resolve().parents[2] / "plans").glob("*.yaml"))

BASE = {
    "plan": "t", "build": "EVT1", "station": "S1", "unit_serial": "DUT-01", "radio": "wifi",
    "channels": [1, 6, 11], "tx_power_qdbm": [80, 44], "packets_per_step": 100,
    "setup": {"mode": "OTA", "distance_m": 2, "orientation": "upright", "obstacles": "none"},
}


def mk(**over):
    d = copy.deepcopy(BASE)
    for k, v in over.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


@pytest.mark.parametrize("path", PLANS, ids=lambda p: p.name)
def test_example_plans_validate(path):
    p = PL.load(path)
    assert p.steps()
    assert p.data_origin in ("RIG_OTA", "RIG_CONDUCTED")
    assert p.sha256


def test_example_plans_exist():
    names = {p.stem for p in PLANS}
    assert {"smoke_wifi_1m", "pathloss_wifi", "txlin_wifi", "waterfall_wifi", "build_compare_wifi"} <= names


def test_steps_expand_repeats_channels_powers():
    p = PL.from_dict(mk(repeats=2))
    s = p.steps()
    assert len(s) == 2 * 3 * 2
    assert [x.index for x in s] == list(range(12))
    assert (s[0].repeat, s[0].channel, s[0].pwr_qdbm) == (0, 1, 80)
    assert (s[-1].repeat, s[-1].channel, s[-1].pwr_qdbm) == (1, 11, 44)


def test_dbm_and_ble_all():
    p = PL.from_dict(mk(radio="ble", channels=["all", 37], tx_power_qdbm=None, tx_power_dbm=[-15, 9],
                        interval_ms=40))
    assert p.channels == [0, 37]
    assert p.tx_power_qdbm == [-60, 36]
    st = p.steps()
    assert st[0].channel_label == "ALL" and st[0].per_meaningful
    assert st[2].channel_label == "37" and not st[2].per_meaningful


def test_conducted_requires_attenuation_and_origin():
    with pytest.raises(PL.PlanError, match="attenuation_db is required"):
        PL.from_dict(mk(setup={"mode": "CONDUCTED", "orientation": "n/a", "obstacles": "n/a"}))
    p = PL.from_dict(mk(setup={"mode": "conducted", "orientation": "n/a", "obstacles": "n/a",
                               "attenuation_db": [30, 30, 20], "cable_loss_db": 1}))
    assert p.data_origin == "RIG_CONDUCTED" and p.attenuation_total_db == 80


def test_all_errors_reported_at_once():
    bad = mk(radio="wifi", channels=[1, 14, "x"], tx_power_qdbm=[90, 4.1], packets_per_step=0,
             setup={"mode": "OTA", "orientation": ""}, limits={"PER": {"hi": 10}, "EVM": {"hi": 3}}, colour="red")
    with pytest.raises(PL.PlanError) as ei:
        PL.from_dict(bad, source="bad.yaml")
    msg = str(ei.value)
    for frag in ["unknown key 'colour'", "channels[1]=14", "channels[2]='x'", "tx_power_qdbm[0]=90",
                 "tx_power_qdbm[1]=4.1", "packets_per_step", "setup.distance_m is required",
                 "setup.orientation is required", "setup.obstacles is required", "unknown metric 'EVM'"]:
        assert frag in msg, frag
    assert msg.startswith("bad.yaml:")
    assert len(ei.value.errors) >= 10


@pytest.mark.parametrize("over,frag", [
    (dict(radio="lte"), "'radio' must be one of"),
    (dict(radio="ble", channels=[36], tx_power_qdbm=[0], interval_ms=40), "channels[0]=36"),
    (dict(radio="ble", channels=["all"], tx_power_qdbm=[0], interval_ms=10), "interval_ms' for BLE"),
    (dict(radio="154", channels=[10], tx_power_qdbm=[0]), "channels[0]=10"),
    (dict(radio="154", channels=[11], tx_power_qdbm=[2]), "whole dBm"),
    (dict(channels=[6, 6]), "duplicates"),
    (dict(channels=[]), "non-empty list"),
    (dict(tx_power_qdbm=None), "is required"),
    (dict(tx_power_dbm=[10]), "not both"),
    (dict(build=None), "'build' is required"),
    (dict(build="a=b"), "must not contain"),
    (dict(payload_len=300), "payload_len"),
    (dict(repeats=True), "repeats"),
    (dict(limits={"PER": {"lo": 5, "hi": 1}}), "lo 5 > hi 1"),
    (dict(limits={"PER": {"max": 5}}), "'lo' and/or 'hi'"),
    (dict(setup={"mode": "AIR", "distance_m": 1, "orientation": "x", "obstacles": "y"}), "OTA or CONDUCTED"),
    (dict(setup={"mode": "OTA", "distance_m": -1, "orientation": "x", "obstacles": "y"}), "positive number"),
    (dict(setup={"mode": "OTA", "distance_m": 1, "orientation": "x", "obstacles": "y", "wind": 3}), "unknown key 'wind'"),
])
def test_specific_errors(over, frag):
    with pytest.raises(PL.PlanError) as ei:
        PL.from_dict(mk(**over))
    assert frag in str(ei.value)


def test_warnings_for_quantized_powers_and_low_counts():
    p = PL.from_dict(mk(tx_power_qdbm=[40, 41], packets_per_step=50))
    w = " ".join(p.warnings)
    assert "applied as 34" in w and "identical" in w and "resolution" in w


def test_load_reports_yaml_and_io_errors(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("plan: [unclosed\n")
    with pytest.raises(PL.PlanError, match="invalid YAML"):
        PL.load(f)
    with pytest.raises(PL.PlanError, match="cannot read"):
        PL.load(tmp_path / "missing.yaml")
    f.write_text("- just\n- a list\n")
    with pytest.raises(PL.PlanError, match="mapping"):
        PL.load(f)


def test_estimated_time_positive():
    p = PL.from_dict(mk())
    assert 0 < p.estimated_seconds() < 60
