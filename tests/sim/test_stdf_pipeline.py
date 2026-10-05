"""Simulated lot -> STDF files (C++ writer) -> C++ reader -> same detector verdict."""
import numpy as np
import pandas as pd

from radioyield import stdf
from radioyield.analysis import station
from radioyield.sim.lots import LotConfig, generate_lot


def test_lot_survives_stdf_and_detector_agrees(tmp_path):
    lot = generate_lot(LotConfig(scenario="station_offset", seed=3, n_units=600))
    parts = []
    for (st, b), g in lot.frame.groupby(["station", "build"]):
        p = tmp_path / f"{st}_{b}.stdf"
        stdf.write(str(p), g.reset_index(drop=True), {**lot.meta, "station": st, "build": b})
        parts.append(stdf.read(str(p)).measurements.to_pandas())
    back = pd.concat(parts, ignore_index=True)
    key = ["part_ts", "part_id", "test_num"]
    a = lot.frame.sort_values(key).reset_index(drop=True)
    b = back.sort_values(key).reset_index(drop=True)
    assert len(a) == len(b)
    assert (a["part_id"] == b["part_id"]).all() and (a["station"] == b["station"]).all()
    # STDF stores RESULT as float32
    assert np.allclose(a["result"], b["result"], rtol=1e-6, atol=1e-5)
    assert set(b["data_origin"]) == {"SIMULATED"}
    g = station.golden_correlation(back, n_boot=300)
    assert set(g[g["flag"]]["station"]) == {"ST3"}
