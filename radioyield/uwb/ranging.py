"""Ranging-error correction on the IDLab Ghent TWR Correction Dataset (REAL PUBLIC DATA, DW1000).

    python -m radioyield.uwb ranging            # writes docs/results/uwb_ranging.json

Data: two recordings ("dataset one" / "dataset two", captured about six months apart) of a
mobile robot carrying a Wi-PoS (Qorvo DW1000) tag through the imec-IDLab Industrial IoT lab,
ranging with ADS-TWR (asymmetric double-sided two-way ranging) to 22 fixed anchors. Ground
truth: Qualisys motion capture (gt.csv, 100 Hz) and surveyed anchor positions
(AnchorsIIoT-28march23.csv, mm).

Ground-truth distance per packet = || mocap position interpolated at Logtime - anchor ||.
Ranging error = raw TWR distance - ground-truth distance (positive = range too long).
The mocap marker-to-antenna offset is unknown and not corrected; any constant part of it is
inside the "before" error and can be learned by the model.

Evaluation is by held-out session: train on one recording, test on the other, both
directions. Rows from the same recording never appear in both train and test. CIs: moving
block bootstrap over 10-second time blocks of the test recording (consecutive packets are
correlated, so resampling single rows would understate the uncertainty), 2000 resamples,
seed 0.
"""
from __future__ import annotations

import json
import platform
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .features import cir_features
from .fetch import HF_CARD, HF_CARD_SHA, IDLAB_SHARES, RAW_DIR, REPO_ROOT

IDLAB_DIR = RAW_DIR / "idlab"
SESSIONS = ["twr_ds1", "twr_ds2"]
SEED = 0
N_BOOT = 2000
BLOCK_S = 10.0
NEEDED = ["processed_full.csv", "gt.csv"]


def have_data() -> bool:
    return all((IDLAB_DIR / s / f).exists() for s in SESSIONS for f in NEEDED) and \
        any((IDLAB_DIR / s / "AnchorsIIoT-28march23.csv").exists() for s in SESSIONS)


def decode_cir(hexstr: pd.Series) -> np.ndarray:
    """Hex dump of the DW1000 accumulator: little-endian int16 (real, imag) pairs -> complex (n, L)."""
    raw = np.stack([np.frombuffer(bytes.fromhex(h), dtype="<i2") for h in hexstr])
    return raw[:, 0::2].astype(float) + 1j * raw[:, 1::2].astype(float)


def _anchors() -> pd.DataFrame:
    for s in SESSIONS:
        f = IDLAB_DIR / s / "AnchorsIIoT-28march23.csv"
        if f.exists():
            return pd.read_csv(f).set_index("hex")[["x", "y", "z"]] / 1000.0  # mm -> m
    raise FileNotFoundError("AnchorsIIoT-28march23.csv missing")


def load_session(name: str) -> pd.DataFrame:
    d = IDLAB_DIR / name
    p = pd.read_csv(d / "processed_full.csv")
    g = pd.read_csv(d / "gt.csv").sort_values("Logtime")
    anc = _anchors()
    p = p[p["anchor"].isin(anc.index)].reset_index(drop=True)
    pos = np.column_stack([np.interp(p["Logtime"], g["Logtime"], g[c]) for c in "xyz"])
    inside = (p["Logtime"] >= g["Logtime"].iloc[0]) & (p["Logtime"] <= g["Logtime"].iloc[-1])
    true_d = np.linalg.norm(pos - anc.loc[p["anchor"]].to_numpy(), axis=1)
    rxpacc = p["rxpacc"].to_numpy(float)
    cir = np.abs(decode_cir(p["cir"])) / rxpacc[:, None]
    fp_pos = p["fp_index"].to_numpy(float) - p["cir_offset"].to_numpy(float)
    feats = cir_features(cir, fp_pos, noise_std=p["cir_noise"].to_numpy(float) / rxpacc)
    out = pd.DataFrame({
        "session": name, "t": p["Logtime"].to_numpy() - p["Logtime"].min(), "anchor": p["anchor"],
        "range_m": p["distance"].to_numpy(float) / 1000.0, "true_m": true_d,
        "fp_power_dbm": p["fp_power"], "rx_power_dbm": p["rx_power"],
        "fp_rx_diff_db": p["rx_power"] - p["fp_power"],
        "rxpacc": rxpacc, "fp_frac": p["fp_index"] % 1.0,
        "pp_minus_fp": p["ppindex"] - p["fp_index"],
        "fpampl1_n": p["fpampl1"] / rxpacc, "fpampl2_n": p["fpaml2"] / rxpacc, "fpampl3_n": p["fpampl3"] / rxpacc,
        "ppampl_n": p["ppampl"] / rxpacc, "cir_noise_n": p["cir_noise"] / rxpacc,
        "ldethr_n": p["LDE_threshold"].apply(lambda v: int(str(v), 16) if isinstance(v, str) else v).astype(float) / rxpacc,
        "inside_gt": inside.to_numpy(),
    })
    out = pd.concat([out, feats], axis=1)
    out["err_m"] = out["range_m"] - out["true_m"]
    return out[out["inside_gt"]].reset_index(drop=True)


FEATURES = ["range_m", "fp_power_dbm", "rx_power_dbm", "fp_rx_diff_db", "rxpacc", "fp_frac", "pp_minus_fp",
            "fpampl1_n", "fpampl2_n", "fpampl3_n", "ppampl_n", "cir_noise_n", "ldethr_n",
            "max_amp", "energy", "fp_amp_cir", "fp_to_peak_delay", "fp_to_peak_amp_ratio", "rise_time",
            "mean_excess_delay", "rms_delay_spread", "kurtosis", "e_frac_3ns", "e_frac_10ns",
            "peak_snr_db", "fp_snr_db"]


def make_model() -> HistGradientBoostingRegressor:
    # absolute_error loss because the headline metric is MAE; small trees for ~1.7k rows/session
    return HistGradientBoostingRegressor(loss="absolute_error", max_iter=300, learning_rate=0.05,
                                         max_leaf_nodes=15, min_samples_leaf=20, random_state=SEED)


def _p95(x):
    return float(np.percentile(x, 95))


def block_bootstrap(t: np.ndarray, before: np.ndarray, after: np.ndarray, seed: int = SEED) -> dict:
    """Block bootstrap CIs for MAE / p95 of |error|, before, after and the paired difference."""
    rng = np.random.default_rng(seed)
    blk = np.floor(t / BLOCK_S).astype(int)
    ids = np.unique(blk)
    members = [np.nonzero(blk == b)[0] for b in ids]
    stats = {k: [] for k in ("mae_before", "mae_after", "p95_before", "p95_after", "mae_delta", "p95_delta")}
    ab, aa = np.abs(before), np.abs(after)
    for _ in range(N_BOOT):
        idx = np.concatenate([members[i] for i in rng.integers(0, len(ids), len(ids))])
        mb, ma = ab[idx].mean(), aa[idx].mean()
        pb, pa = _p95(ab[idx]), _p95(aa[idx])
        for k, v in zip(stats, (mb, ma, pb, pa, ma - mb, pa - pb)):
            stats[k].append(v)
    return {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in stats.items()} | \
        {"n_blocks": int(len(ids)), "block_s": BLOCK_S, "n_boot": N_BOOT, "seed": seed}


def evaluate(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    m = make_model().fit(train[FEATURES].to_numpy(), train["err_m"].to_numpy())
    pred = m.predict(test[FEATURES].to_numpy())
    before = test["err_m"].to_numpy()
    after = before - pred
    # reference: subtract only the training-set median error (constant offset calibration)
    const = before - np.median(train["err_m"])
    return {
        "n_train": int(len(train)), "n_test": int(len(test)),
        "mae_before": float(np.mean(np.abs(before))), "p95_before": _p95(np.abs(before)),
        "mae_after": float(np.mean(np.abs(after))), "p95_after": _p95(np.abs(after)),
        "mean_err_before": float(before.mean()), "mean_err_after": float(after.mean()),
        "mae_const_offset_only": float(np.mean(np.abs(const))), "p95_const_offset_only": _p95(np.abs(const)),
        "ci95": block_bootstrap(test["t"].to_numpy(), before, after),
    }


def run(out_path: Path | None = None) -> dict:
    t0 = time.time()
    if not have_data():
        raise FileNotFoundError(f"IDLab data missing under {IDLAB_DIR}; run `python -m radioyield.uwb fetch`")
    S = {s: load_session(s) for s in SESSIONS}
    res: dict = {
        "label": "REAL PUBLIC DATA (IDLab Ghent TWR Correction Dataset, Qorvo DW1000 / Wi-PoS, ADS-TWR, "
                 "Qualisys mocap ground truth), not our hardware",
        "dataset": {"index": HF_CARD, "hf_card_sha": HF_CARD_SHA,
                    "shares": {k: f"https://cloud.ilabt.imec.be/index.php/s/{v[0]}" for k, v in IDLAB_SHARES.items()},
                    "sessions": {s: {"n": int(len(d)), "duration_s": float(d.t.max()), "anchors": int(d.anchor.nunique())}
                                 for s, d in S.items()}},
        "model": "sklearn HistGradientBoostingRegressor(loss=absolute_error, max_iter=300, lr=0.05, "
                 "max_leaf_nodes=15, min_samples_leaf=20, seed=0); target = raw range - mocap distance",
        "features": FEATURES,
        "protocol": "held-out session: train on one recording, test on the other (both directions)",
    }
    res["train_ds1_test_ds2"] = evaluate(S["twr_ds1"], S["twr_ds2"])
    res["train_ds2_test_ds1"] = evaluate(S["twr_ds2"], S["twr_ds1"])
    # error statistics by a training-free NLOS proxy (Decawave APS006: RX - FP > 6 dB)
    allp = pd.concat(S.values(), ignore_index=True)
    proxy = {}
    for name, mask in (("likely_LOS (rx-fp<=6dB)", allp.fp_rx_diff_db <= 6), ("likely_NLOS (rx-fp>6dB)", allp.fp_rx_diff_db > 6)):
        e = allp.loc[mask, "err_m"]
        proxy[name] = {"n": int(mask.sum()), "mean_err_m": float(e.mean()), "median_err_m": float(e.median()),
                       "mae_m": float(e.abs().mean()), "p95_abs_m": _p95(e.abs())}
    res["error_by_nlos_proxy"] = proxy
    res["error_by_session"] = {s: {"mean_err_m": float(d.err_m.mean()), "median_err_m": float(d.err_m.median()),
                                   "std_err_m": float(d.err_m.std())} for s, d in S.items()}
    res["machine"] = f"{platform.platform()} / {platform.processor() or platform.machine()} / python {platform.python_version()}"
    res["runtime_s"] = round(time.time() - t0, 1)
    out_path = out_path or REPO_ROOT / "docs" / "results" / "uwb_ranging.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(res, indent=2))
    return res
