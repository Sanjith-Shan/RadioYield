"""End-to-end: collector serial test double -> Parquet -> RF fits -> report. SIMULATED."""
import pytest

from radioyield.analysis.rig import load_rig_dir, rf_from_rig
from radioyield.cli import main


@pytest.mark.slow
def test_fake_pathloss_walk_recovers_the_doubles_exponent(tmp_path):
    from radioyield.collector.cli import main as collect
    for d in (1, 2, 4, 8, 16):
        rc = collect(["run", "plans/pathloss_wifi.yaml", "--fake", "--distance", str(d),
                      "--position", f"tape-{d}m", "--out", str(tmp_path)])
        assert rc in (0, None)
    steps, packets = load_rig_dir(tmp_path)
    rf, notes = rf_from_rig(steps, packets)
    # fake.py's toy channel uses exponent 2.2 (an arbitrary test value)
    assert rf["path_loss"]["fit"].exponent.contains(2.2)
    out = tmp_path / "r.html"
    main(["report", "--rig", str(tmp_path), "--out", str(out)])
    html = out.read_text()
    assert "SIMULATED RIG SESSION" in html and "Path loss" in html
