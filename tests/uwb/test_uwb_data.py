"""Tests that need the downloaded public data sets (python -m radioyield.uwb fetch)."""
import numpy as np
import pytest

from radioyield.uwb import losnlos, ranging

pytestmark = pytest.mark.data


@pytest.mark.skipif(not losnlos.have_data(), reason="eWINE data not downloaded")
def test_ewine_counts_and_physics():
    f = losnlos.load_features()
    assert len(f) == 42000
    assert (f.NLOS == 1).sum() == 21000 and (f.NLOS == 0).sum() == 21000
    med = f.groupby("NLOS").median(numeric_only=True)
    # NLOS: weaker first path relative to total, later peak, larger delay spread
    assert med.loc[1, "fp_rx_diff_db"] > med.loc[0, "fp_rx_diff_db"]
    assert med.loc[1, "mean_excess_delay"] > med.loc[0, "mean_excess_delay"]
    assert med.loc[1, "range_m"] > med.loc[0, "range_m"]


@pytest.mark.skipif(not losnlos.have_data(), reason="eWINE data not downloaded")
@pytest.mark.slow
def test_ewine_group_split_runs():
    f = losnlos.load_features().sample(6000, random_state=0)
    r = losnlos.eval_group(f, losnlos.FEATURES, "config_group")
    assert len(r["folds"]) == 2
    assert r["summary"]["accuracy"]["mean"] > 0.8


@pytest.mark.skipif(not ranging.have_data(), reason="IDLab TWR data not downloaded")
def test_idlab_ground_truth_alignment():
    d = ranging.load_session("twr_ds1")
    assert len(d) > 1000
    # mocap-referenced error should be centred near zero with a sub-metre spread for most rows
    assert abs(np.median(d.err_m)) < 0.3
    assert np.percentile(np.abs(d.err_m), 50) < 0.5
