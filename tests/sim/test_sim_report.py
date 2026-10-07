import json

import pandas as pd
import pytest

from radioyield import schema
from radioyield.analysis.lot import analyze_lot
from radioyield.report.html import render_lot
from radioyield.sim.lots import SCENARIOS, LotConfig, generate_lot
from radioyield.sim import score


@pytest.mark.parametrize("sc", SCENARIOS)
def test_lot_conforms_to_schema_and_is_labelled(sc):
    lot = generate_lot(LotConfig(scenario=sc, seed=1, n_units=400))
    t = lot.table()
    schema.validate(t)
    assert set(lot.frame["data_origin"]) == {"SIMULATED"} and lot.meta["data_origin"] == "SIMULATED"


def test_lot_is_deterministic():
    a = generate_lot(LotConfig(scenario="clean", seed=5, n_units=300)).frame
    b = generate_lot(LotConfig(scenario="clean", seed=5, n_units=300)).frame
    pd.testing.assert_frame_equal(a, b)


def test_pat_injections_pass_spec():
    lot = generate_lot(LotConfig(scenario="pat_marginal", seed=9))
    f = lot.frame
    rows = f[f["part_id"].isin(lot.truth["units"]) & ~f["retest"]]
    assert rows["part_pass"].all()


@pytest.mark.parametrize("sc,runner", [("station_offset", score.run_station_offset),
                                        ("build_shift", score.run_build_shift),
                                        ("bimodal_antenna", score.run_bimodal),
                                        ("pat_marginal", score.run_pat)])
def test_scenario_detected(sc, runner, lots):
    assert runner(lots(sc))["detected"]


def test_report_renders_with_banner_and_not_measured(lots):
    a = analyze_lot(lots("station_offset").frame, n_boot=200, drift_h=18)
    html = render_lot(a)
    assert "SIMULATED DATA" in html and "What was not measured" in html
    assert "station ST3" in html          # bimodality attributed to the offset station
    assert html.count("<svg") >= 4


@pytest.mark.slow
def test_score_cli_small(tmp_path):
    out = tmp_path / "s.json"
    score.main(["--seeds", "2", "--sensitivity-seeds", "0", "--calibration-seeds", "0", "--workers", "2", "--out", str(out)])
    d = json.loads(out.read_text())
    assert d["label"].startswith("SIMULATED") and set(d["summary"]) == set(SCENARIOS)


def test_cli_sim_then_report(tmp_path):
    from radioyield.cli import main
    p = tmp_path / "lot.parquet"
    main(["sim", "--scenario", "clean", "--seed", "2", "--units", "300", "--out", str(p)])
    out = tmp_path / "r.html"
    main(["report", str(p), "--out", str(out)])
    assert "SIMULATED DATA" in out.read_text()
