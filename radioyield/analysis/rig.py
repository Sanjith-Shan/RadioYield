"""Turn the collector's per-step / per-packet Parquet into the RF fits.

Which runs feed which fit is decided by the plan name (the first ``__`` field of
the run stem): ``waterfall_*`` -> PER waterfall, ``pathloss_*`` -> path loss,
``txlin_*`` -> TX power linearity. ``conducted_*`` runs also feed the waterfall,
with the level computed from the applied TX power minus the attenuator chain
instead of RSSI.

Level used for the OTA waterfall: the REF's median RSSI of the packets that got
through. Near sensitivity that is biased upward (the packets that survive are
the strong ones), which is stated in the fit's notes.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import rf


def load_rig_dir(path) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = Path(path)
    steps = [pd.read_parquet(f) for f in sorted(p.rglob("*.steps.parquet"))]
    pk = [pd.read_parquet(f) for f in sorted(p.rglob("*.packets.parquet"))]
    s = pd.concat(steps, ignore_index=True) if steps else pd.DataFrame()
    k = pd.concat(pk, ignore_index=True) if pk else pd.DataFrame()
    for df in (s, k):
        if len(df):
            df["plan"] = df["run_uid"].str.split("__").str[0]
    return s, k


def _valid(s: pd.DataFrame) -> pd.DataFrame:
    m = pd.Series(True, index=s.index)
    for col in ("per_valid", "per_meaningful"):
        if col in s:
            m &= s[col].fillna(False).astype(bool)
    return s[m]


def rf_from_rig(steps: pd.DataFrame, packets: pd.DataFrame) -> tuple[dict, list[str]]:
    out: dict = {}
    notes: list[str] = []
    if steps.empty:
        return out, ["no rig steps found"]

    # ---- PER waterfall, per radio (and per mode)
    wf = {}
    for (radio, mode), g in _valid(steps[steps["plan"].str.startswith(("waterfall", "conducted"))]).groupby(["radio", "mode"]):
        if mode == "CONDUCTED":
            level = g["pwr_act_qdbm"] / 4 - g["attenuation_total_db"].fillna(0) - g["cable_loss_db"].fillna(0)
            what = "conducted input level (applied TX power - attenuators - cable)"
        else:
            level = g["rssi_median"]
            what = "REF median RSSI of received packets (OTA; biased high near sensitivity)"
        d = pd.DataFrame({"level": level, "n": g["n_onair"], "lost": g["n_lost"]}).dropna()
        d = d[d["n"] > 0].groupby("level", as_index=False).sum()
        if len(d) < 3:
            notes.append(f"waterfall {radio} {mode}: fewer than 3 usable levels")
            continue
        try:
            fit = rf.per_waterfall(d["level"], d["n"], d["lost"], n_boot=500)
        except ValueError as e:
            notes.append(f"PER waterfall {radio} {mode}: not fitted: {e}")
            continue
        fit.notes.insert(0, f"level = {what}")
        if mode == "CONDUCTED":
            fit.notes.append("the CI covers packet statistics only; the attenuator, cable and TX power errors add a "
                             "systematic error the CI does not include (about +/-1.5 dB in the virtual-rig rehearsal, "
                             "docs/results/sim_rig.md). Calibrate the chain before quoting against the datasheet")
        wf[f"{radio} {mode}"] = {"level": d["level"].to_numpy(), "per": (d["lost"] / d["n"]).to_numpy(), "fit": fit}
    if wf:
        out["waterfall"] = wf

    # ---- path loss across positions (per-packet RSSI), clear-path positions only
    if len(packets):
        pl = packets[packets["plan"].str.startswith("pathloss") & packets["rssi_dbm"].notna()
                     & ~packets["dup"].astype(bool) & packets["distance_m"].notna()]
        clear = pl["obstacles"].fillna("none").str.strip().str.lower().str.match(r"^(none|$)")
        excluded = sorted(pl.loc[~clear, "position_label"].dropna().unique().tolist())
        pl = pl[clear]
        if pl["distance_m"].nunique() >= 3:
            fit = rf.path_loss_fit(pl["distance_m"], pl["rssi_dbm"])
            if excluded:
                fit.notes.append(f"excluded from the fit (obstructed): {', '.join(excluded)}")
            pos = np.sort(pl["distance_m"].unique())
            meds = np.array([pl.loc[pl["distance_m"] == p, "rssi_dbm"].median() for p in pos])
            out["path_loss"] = {"fit": fit, "distance": pl["distance_m"].to_numpy(), "positions": pos, "medians": meds}
        elif len(pl):
            notes.append(f"path loss: only {pl['distance_m'].nunique()} clear-path distance(s); need 3")

    # ---- TX power linearity per radio: median over channels at each applied power
    tx = {}
    for radio, g in _valid(steps[steps["plan"].str.startswith("txlin")]).groupby("radio"):
        d = g.assign(p=g["pwr_act_qdbm"] / 4).groupby("p")["rssi_median"].median().dropna()
        if len(d) >= 3:
            fit = rf.tx_linearity(d.index.to_numpy(), d.to_numpy())
            tx[str(radio)] = {"fit": fit, "commanded": d.index.to_numpy(), "measured": d.to_numpy()}
    if tx:
        out["tx_linearity"] = tx
    return out, notes
