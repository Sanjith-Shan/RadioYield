"""LOS/NLOS classification on the eWINE UWB LOS/NLOS Data Set (REAL PUBLIC DATA, DW1000).

    python -m radioyield.uwb losnlos            # writes docs/results/uwb_losnlos.json

Site labels. The README lists 7 environments (3000 LOS + 3000 NLOS each) but says the whole
set was randomized before being split into 7 files, and there is no environment column. We
checked: each file has 6000 rows with 2967-3055 NLOS rows (not 3000), and lag-1
autocorrelation of every column across row order is ~0, so neither files nor row order
encode sites. Leave-one-site-out is therefore NOT measurable on the public release.

The most honest grouping we can recover is the acquisition configuration: FRAME_LEN (27 vs
39 bytes, plus one 29-byte row) splits the data into two roughly class-balanced campaigns
recorded with different firmware frame settings. We hold out one configuration group at a
time ("leave-one-config-out"). It is a real distribution shift, but it is NOT a site split
and may mix sites. A stratified random 5-fold split is reported as the secondary number.
Configuration columns (CH, FRAME_LEN, PREAM_LEN, BITRATE, PRFR) are never used as features:
PREAM_LEN=1536 rows are 98 % NLOS (1531 of 1557), a pure acquisition artefact that would leak the label.
"""
from __future__ import annotations

import glob
import json
import platform
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold

from .features import cir_features, diag_features
from .fetch import EWINE_COMMIT, RAW_DIR, REPO_ROOT

EWINE_DIR = RAW_DIR / "ewine" / "dataset"
CACHE = RAW_DIR / "ewine_features.parquet"
SEED = 0


def have_data() -> bool:
    return len(glob.glob(str(EWINE_DIR / "uwb_dataset_part*.csv"))) == 7


def load_raw() -> pd.DataFrame:
    files = sorted(glob.glob(str(EWINE_DIR / "uwb_dataset_part*.csv")))
    if len(files) != 7:
        raise FileNotFoundError(f"eWINE data not found under {EWINE_DIR}; run `python -m radioyield.uwb fetch`")
    return pd.concat([pd.read_csv(f).assign(part=i + 1) for i, f in enumerate(files)], ignore_index=True)


def build_features(raw: pd.DataFrame) -> pd.DataFrame:
    cir_cols = [c for c in raw.columns if c.startswith("CIR") and c[3:].isdigit()]
    rxpacc = raw["RXPACC"].to_numpy(float)
    # README: "CIR data still needs to be divided by number of acquired RX preamble samples".
    # (Its example code divides by item[2], which is FP_IDX; the correct column is RXPACC.)
    cir = raw[cir_cols].to_numpy(float) / rxpacc[:, None]
    # README: FP_IDX indexes the CIR accumulator; in the row it sits at first_path_index+15,
    # i.e. CIR array index == FP_IDX.
    fp = raw["FP_IDX"].to_numpy(float)
    feats = pd.concat([
        diag_features(raw.FP_AMP1, raw.FP_AMP2, raw.FP_AMP3, raw.CIR_PWR, rxpacc, raw.RANGE, raw.PRFR),
        cir_features(cir, fp, noise_std=raw["STDEV_NOISE"].to_numpy(float) / rxpacc),
    ], axis=1)
    feats["max_noise_norm"] = raw["MAX_NOISE"].to_numpy(float) / rxpacc
    feats["stdev_noise_norm"] = raw["STDEV_NOISE"].to_numpy(float) / rxpacc
    feats["NLOS"] = raw["NLOS"].astype(int).to_numpy()
    feats["config_group"] = np.where(raw["FRAME_LEN"] == 39, "FRAME_LEN=39", "FRAME_LEN=27/29")
    feats["part"] = raw["part"].to_numpy()
    for c in ["FRAME_LEN", "PREAM_LEN"]:
        feats[c] = raw[c].to_numpy()
    return feats


def load_features(rebuild: bool = False) -> pd.DataFrame:
    if CACHE.exists() and not rebuild:
        return pd.read_parquet(CACHE)
    f = build_features(load_raw())
    f.to_parquet(CACHE)
    return f


FEATURES = ["fp_power_dbm", "rx_power_dbm", "fp_rx_diff_db", "fp_to_total_ratio", "rxpacc", "range_m",
            "max_amp", "energy", "fp_amp_cir", "fp_to_peak_delay", "fp_to_peak_amp_ratio", "rise_time",
            "mean_excess_delay", "rms_delay_spread", "kurtosis", "e_frac_3ns", "e_frac_10ns",
            "peak_snr_db", "fp_snr_db", "max_noise_norm", "stdev_noise_norm"]


def make_model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
                                          early_stopping=False, random_state=SEED)


def _metrics(y, prob) -> dict:
    pred = (prob >= 0.5).astype(int)
    return {"n": int(len(y)), "n_nlos": int(y.sum()), "accuracy": float(accuracy_score(y, pred)),
            "f1": float(f1_score(y, pred)), "auc": float(roc_auc_score(y, prob)) if len(set(y)) > 1 else None}


def _summ(folds: list[dict]) -> dict:
    out = {}
    for k in ("accuracy", "f1", "auc"):
        v = np.array([f[k] for f in folds], float)
        out[k] = {"mean": float(v.mean()), "std": float(v.std(ddof=1)) if len(v) > 1 else 0.0,
                  "min": float(v.min()), "max": float(v.max())}
    return out


def eval_group(df: pd.DataFrame, feats: list[str], group_col: str) -> dict:
    X, y, g = df[feats].to_numpy(), df["NLOS"].to_numpy(), df[group_col].to_numpy()
    folds = []
    for grp in sorted(set(g)):
        te = g == grp
        m = make_model().fit(X[~te], y[~te])
        folds.append({"held_out": str(grp), **_metrics(y[te], m.predict_proba(X[te])[:, 1])})
    return {"folds": folds, "summary": _summ(folds)}


def eval_random(df: pd.DataFrame, feats: list[str], k: int = 5) -> dict:
    X, y = df[feats].to_numpy(), df["NLOS"].to_numpy()
    folds = []
    for i, (tr, te) in enumerate(StratifiedKFold(k, shuffle=True, random_state=SEED).split(X, y)):
        m = make_model().fit(X[tr], y[tr])
        folds.append({"fold": i, **_metrics(y[te], m.predict_proba(X[te])[:, 1])})
    return {"folds": folds, "summary": _summ(folds)}


def heuristic_baseline(df: pd.DataFrame) -> dict:
    """Training-free rule from Decawave APS006 part 3: NLOS likely if RX - FP > 6 dB."""
    y = df["NLOS"].to_numpy()
    d = df["fp_rx_diff_db"].to_numpy()
    pred = (d > 6.0).astype(int)
    return {"rule": "NLOS if rx_power_dbm - fp_power_dbm > 6 dB (Decawave APS006 Part 3)",
            "accuracy": float(accuracy_score(y, pred)), "f1": float(f1_score(y, pred)),
            "auc_of_diff": float(roc_auc_score(y, d))}


def run(out_path: Path | None = None) -> dict:
    t0 = time.time()
    df = load_features()
    res: dict = {
        "label": "REAL PUBLIC DATA (eWINE UWB LOS/NLOS Data Set, Decawave DWM1000), not our hardware",
        "dataset": {"source": "https://github.com/ewine-project/UWB-LOS-NLOS-Data-Set", "commit": EWINE_COMMIT,
                    "n": int(len(df)), "n_los": int((df.NLOS == 0).sum()), "n_nlos": int((df.NLOS == 1).sum()),
                    "per_file_nlos": df.groupby("part").NLOS.sum().astype(int).to_dict(),
                    "config_groups": {k: {"n": int(len(v)), "n_nlos": int(v.NLOS.sum())}
                                      for k, v in df.groupby("config_group")}},
        "site_labels": "not recoverable from the public release (shuffled, no environment column); "
                       "leave-one-site-out not measurable. Headline uses leave-one-config-out (FRAME_LEN).",
        "model": "sklearn HistGradientBoostingClassifier(max_iter=400, lr=0.05, max_leaf_nodes=31, seed=0)",
        "features": FEATURES,
    }
    res["leave_one_config_out"] = eval_group(df, FEATURES, "config_group")
    res["random_5fold"] = eval_random(df, FEATURES)
    res["heuristic_fp_rx_6db"] = heuristic_baseline(df)
    # ablation: CIR-shape features only (no range, no RXPACC, no absolute power): checks the
    # model is not just reading acquisition/geometry cues
    cir_only = ["fp_to_peak_delay", "fp_to_peak_amp_ratio", "rise_time", "mean_excess_delay",
                "rms_delay_spread", "kurtosis", "e_frac_3ns", "e_frac_10ns", "fp_rx_diff_db", "fp_to_total_ratio"]
    res["ablation_shape_only"] = {"features": cir_only,
                                  "leave_one_config_out": eval_group(df, cir_only, "config_group")["summary"],
                                  "random_5fold": eval_random(df, cir_only)["summary"]}
    # ablation: everything except measured range (range is partly a property of how the
    # campaign placed nodes: NLOS links were measured at longer distances)
    no_range = [f for f in FEATURES if f != "range_m"]
    res["ablation_no_range"] = {"features": no_range,
                                "leave_one_config_out": eval_group(df, no_range, "config_group")["summary"],
                                "random_5fold": eval_random(df, no_range)["summary"]}
    m = make_model().fit(df[FEATURES].to_numpy(), df["NLOS"].to_numpy())
    from sklearn.inspection import permutation_importance
    sub = df.sample(5000, random_state=SEED)
    pi = permutation_importance(m, sub[FEATURES].to_numpy(), sub["NLOS"].to_numpy(), n_repeats=3,
                                random_state=SEED, scoring="accuracy")
    res["permutation_importance_in_sample"] = dict(sorted(
        {f: float(v) for f, v in zip(FEATURES, pi.importances_mean)}.items(), key=lambda kv: -kv[1]))
    res["los_vs_nlos_feature_medians"] = df.groupby("NLOS")[FEATURES].median().T.rename(
        columns={0: "LOS", 1: "NLOS"}).to_dict(orient="index")
    res["machine"] = f"{platform.platform()} / {platform.processor() or platform.machine()} / python {platform.python_version()}"
    res["runtime_s"] = round(time.time() - t0, 1)
    out_path = out_path or REPO_ROOT / "docs" / "results" / "uwb_losnlos.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(res, indent=2))
    return res
