# UWB results: LOS/NLOS classification and ranging-error correction

**REAL PUBLIC DATA, Decawave/Qorvo DW1000. Not our hardware.** Every number here comes from two
public data sets recorded by other labs with DW1000 radios, in specific buildings. Nothing here
was measured on a RadioYield rig.

Machine: Apple M3 Pro (12 cores), macOS 26.5.1 arm64, Python 3.12.7, scikit-learn 1.9.1,
numpy 2.5.4, pandas 3.1.0. Run on 2026-11-01. Baselines and their protocols:
[`docs/baselines/uwb.md`](../baselines/uwb.md).

```bash
source .venv/bin/activate
python -m radioyield.uwb fetch      # ~318 MB into data/raw/uwb/, writes MANIFEST.json (URL, license, sha256)
python -m radioyield.uwb losnlos    # -> docs/results/uwb_losnlos.json   (~5 min)
python -m radioyield.uwb ranging    # -> docs/results/uwb_ranging.json   (~20 s)
pytest tests/uwb                    # 7 synthetic-fixture tests always run; 3 `data` tests need fetch
```

## Data sets

| Data set | Version | Size | License |
| --- | --- | --- | --- |
| eWINE UWB LOS/NLOS Data Set | git `9a7e755a5a2ed2e2656bea89d8d0d74d7093553d`, 7 CSVs | 301 MB | CC-BY-4.0 |
| IDLab Ghent TWR Correction Dataset, sessions 1 and 2 | HF card sha `d813ce72`, Nextcloud shares `iS3RkgeHAwitPTp` and `QtwFWDZG6PyrNXc` | 26 MB (`imu.csv` skipped) | Apache-2.0 (HF card) |

The sha256 of every file is in `data/raw/uwb/MANIFEST.json`. For the eWINE CSVs, the first 16
hex characters are: part1 `16ad94ca79b3f16e`, part2 `bbd4b8915131e78c`, part3 `70f70bf0e6016250`,
part4 `4b8234646a1c5183`, part5 `336bc71f962979c7`, part6 `593f1e611401bd30`,
part7 `f664b5a71394c506`.

---

## 1. LOS/NLOS classification (eWINE)

### Headline

The site split asked for in the spec cannot be measured on this data set, because the public
release does not record which site each sample came from. The strongest held-out split
available is **leave-one-acquisition-config-out**, and on that split:

**92.6 % mean accuracy (F1 0.925, AUC 0.979) over 2 held-out groups, 91.7 % to 93.5 %.**

On a stratified random 5-fold split the same model reaches **93.1 % ± 0.3 %**
(F1 0.931, AUC 0.982).

- The number to compare with published work is the random-split one.
- On a random split it is above the best verified feature-based number, Zhang et al. 2023: a
  random forest on 12 features, random 7:2:1 split, **87.43 %**.
- It is also above the CNN in the paper that released the data, Bregar & Mohorčič 2018:
  **87.4 %**, protocol not stated, balanced 12k test set.
- About half of that margin comes from the measured range, which in this data set is partly an
  artefact of how the campaign was set up (see the ablations).
- **Without range the model scores 88.4 % (random split), about the same as both baselines.**

### What we verified in the data

- **Sample counts.** 42,000 rows: 21,000 LOS and 21,000 NLOS. This matches the spec and the
  README.
- **Rows per file.** Each of the 7 files has 6,000 rows. The NLOS counts per file are 2968,
  2982, 2992, 3055, 2967, 2986 and 3050, not 3,000. So **the files are not the 7 sites.**
  - The README says: "Whole data set is randomized and later split into 7 smaller files."
  - The lag-1 autocorrelation of every column across row order is about 0 (|r| < 0.013).
  - So row order carries no site information either.
- **No environment column.** We also looked for an unshuffled release: the original repo (its
  first commit is already shuffled), the authors' code repo, the 33 forks and Zenodo. None
  has one.
- **Conclusion:** leave-one-site-out is **not measurable** on the public data.
- **One recoverable grouping.** FRAME_LEN splits the rows into two acquisition
  configurations, both roughly class-balanced:
  - FRAME_LEN = 27 (plus one row at 29): 18,194 rows, of which 8,740 are NLOS.
  - FRAME_LEN = 39: 23,806 rows, of which 12,260 are NLOS.
- **What leaving one configuration out means.** It is a real shift in firmware settings and
  recording session, and the model never sees the held-out configuration. It is **not** a
  site split. Sites may appear in both groups, so it is probably easier than a true
  leave-one-site-out.
- **Configuration columns are never used as features.** These are CH, FRAME_LEN, PREAM_LEN,
  BITRATE and PRFR. PREAM_LEN = 1536 rows are 98 % NLOS (1,531 of 1,557), a recording artefact
  that would leak the label.
- **CIR normalization.** The CIR is divided by RXPACC, as the README says.
  - The README's example code divides by `item[2]`, which is FP_IDX, not RXPACC. That is a bug
    in the example; we divide by the RXPACC column.
  - FP_IDX indexes the CIR array directly. The README notes it sits at "first_path_index+15"
    within the row.

### Why a random split leaks

The 6,000 samples from one room share the room's geometry: the same walls, the same multipath
delays and the same anchor-to-tag distances. With a random split, near-copies of each test
sample's room are in the training set. The model can then learn "this delay profile belongs
to room X, and room X's NLOS spot", which does not carry over to a new building. Holding a
whole group out removes those near-copies. Here the honest group is the acquisition
configuration; with real site labels it would be the room.

### Results

Model: `HistGradientBoostingClassifier(max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
early_stopping=False, random_state=0)` on 21 features. The positive class is NLOS.

| Protocol | Held-out group or fold | n | Accuracy | F1 | AUC |
| --- | --- | --- | --- | --- | --- |
| **Leave-one-config-out** | FRAME_LEN = 27/29 | 18,194 | 0.9346 | 0.9316 | 0.9824 |
| | FRAME_LEN = 39 | 23,806 | 0.9174 | 0.9178 | 0.9747 |
| | **mean ± sd (2 groups)** | | **0.9260 ± 0.0122** | 0.9247 ± 0.0098 | 0.9786 ± 0.0054 |
| Random stratified 5-fold (seed 0) | folds 0–4 | 8,400 each | 0.9329, 0.9340, 0.9277, 0.9286, 0.9337 | | |
| | **mean ± sd** | | **0.9314 ± 0.0030** | 0.9306 ± 0.0030 | 0.9817 ± 0.0006 |

The gap between the two protocols is only 0.5 points. Moving to a new acquisition
configuration costs little, but this says **nothing** about a new building.

### Ablations: what the model is actually using

| Feature set | Leave-one-config-out acc | Random 5-fold acc | Random 5-fold AUC |
| --- | --- | --- | --- |
| All 21 features | 0.926 ± 0.012 | 0.931 ± 0.003 | 0.982 |
| All except `range_m` | 0.880 ± 0.012 | 0.884 ± 0.004 | 0.953 |
| CIR shape only (10 features: no range, no absolute power, no RXPACC, no noise) | 0.723 ± 0.006 | 0.731 ± 0.005 | 0.809 |
| Training-free rule, RX − FP > 6 dB (Decawave APS006) | 0.672 (no training) | | AUC of RX − FP: 0.727 |

- The two strongest features by permutation importance (in-sample) are total CIR energy and
  measured range.
- **Range.** The median range is 2.43 m for LOS and 4.89 m for NLOS. NLOS links do read long
  (see section 3), but most of this 2.5 m gap must be geometry: the NLOS spots were placed
  farther away, and positive NLOS bias is usually tens of cm. **A deployed classifier would
  not see this distance prior, so the 93 % is optimistic for a new building.** 88 % without
  range is the more portable number.
- **Shape-only (72 %) versus the full model.** Absolute power and the energy and noise levels
  carry most of the signal. They are physical (obstacles attenuate), but they also depend on
  distance and on each room.

### Features and why NLOS changes them

The table shows LOS and NLOS medians from `docs/results/uwb_losnlos.json`. The CIR is
normalized by RXPACC. 1 sample ≈ 1 ns.

| Feature | Definition | LOS | NLOS | Why NLOS changes it |
| --- | --- | --- | --- | --- |
| `fp_power_dbm` | DW1000 UM §4.7.1: 10·log10((F1²+F2²+F3²)/N²) − 121.74 | −85.3 | −100.6 | The blocked direct path is attenuated |
| `rx_power_dbm` | UM §4.7.2: 10·log10(C·2¹⁷/N²) − 121.74 | −80.5 | −91.4 | Less total energy, but reflections still carry some |
| `fp_rx_diff_db` | RX − FP (dB) | 3.9 | 8.1 | The first path loses more than the total. APS006 uses > 6 dB as an NLOS rule |
| `fp_to_total_ratio` | (F1²+F2²+F3²)/(C·2¹⁷), the same quantity in linear form | 0.41 | 0.15 | Same as above |
| `fp_to_peak_delay` | Samples from the first path to the CIR maximum | 2 | 5 | Without LOS, the strongest path is a later reflection |
| `rise_time` | Samples from 10 % to 90 % of the peak on the leading edge | 3 | 5 | Energy builds up gradually instead of in one sharp pulse |
| `mean_excess_delay` | Power-weighted mean delay after the first path (ns) | 5.9 | 10.0 | Energy is dominated by longer, multi-bounce paths |
| `rms_delay_spread` | Power-weighted std of delay after the first path (ns) | 7.0 | 9.0 | The profile is more spread out |
| `kurtosis` | Kurtosis of CIR amplitude over [FP−5, FP+60] | 8.5 | 6.3 | A single dominant pulse is peaky; diffuse multipath is flatter |
| `e_frac_3ns` | Fraction of energy within 3 ns of the first path | 0.46 | 0.19 | Less energy at the leading edge |
| `max_amp`, `energy` | Peak and total CIR window energy | 54.6, 13,919 | 13.9, 1,147 | Obstacle loss |
| `peak_snr_db`, `fp_snr_db` | Peak or first-path amplitude over noise std | 49.4, 42.6 | 44.8, 34.1 | Weaker signal over a similar noise floor |
| `rxpacc` | Preamble symbols accumulated | 332 | 1024 | The receiver accumulates longer on weak signals before SFD detection; partly a campaign setting |
| `range_m` | Reported TWR range | 2.43 | 4.89 | Positive NLOS bias plus campaign geometry (see above) |

The two noise features, `stdev_noise_norm` and `max_noise_norm`, are also used.

### Comparison with published work (like with like)

| Work | Protocol | Accuracy |
| --- | --- | --- |
| Zhang et al. 2023, random forest on 12 hand-crafted features | Random 7:2:1 | 87.43 % |
| Zhang et al. 2023, CNN-CAM on the full CIR | Random 7:2:1 | 90.00 % |
| Bregar & Mohorčič 2018, CNN on 152 CIR bins | Not stated; balanced 12k test set | 87.4 % |
| **This work, gradient boosting on 21 features** | **Random 5-fold** | **93.1 % ± 0.3** |
| This work, without range | Random 5-fold | 88.4 % ± 0.4 |
| **This work, gradient boosting on 21 features** | **Leave-one-config-out (2 groups)** | **92.6 % ± 1.2** |

- No paper reports a site split, or any group split, on this data set, so our held-out-group
  number has no published comparison.
- **The fair claim:** on a random split, a physics-feature model matches the released CNN
  baseline without using range, and beats it by about 6 points with range. Range is a feature
  those baselines did not use, and in this campaign it is partly geometry.

### Not done

- **The optional 1D CNN on the CIR.** torch is not installed in the venv, and the
  feature-model work, including the ablations that explain the margin, came first.
- **True leave-one-site-out.** It is impossible without site labels, as explained above.

---

## 2. Ranging-error correction (IDLab Ghent TWR Correction Dataset)

### Data

- A mobile robot carries a Wi-PoS (Qorvo DW1000) tag at 0.1 m/s through the imec-IDLab
  Industrial IoT lab, a 240 m² warehouse with metal racks.
- It ranges with **ADS-TWR** to 22 ceiling and rack anchors on channel 5 (64 MHz PRF,
  110 kbps, 1024-symbol preamble).
- Ground truth is Qualisys motion capture (`gt.csv`, 100 Hz) plus the surveyed anchor
  positions.
- Two recordings, captured about six months apart. The second has more clutter, goods in the
  racks and slightly disturbed anchors.
  - Session 1: 1,757 ranges, 549 s, 22 anchors.
  - Session 2: 1,622 ranges, 551 s, 20 anchors.
- The data is small (3,379 ranges in total), because the curated `processed_full.csv` keeps
  only valid TWR exchanges.

Why this data set: it is the one the spec names. The Hugging Face repo holds only the README;
the files are on the Nextcloud links it gives. It has what we need: raw TWR range, DW1000
diagnostics, the complex CIR (300 taps from accumulator index 650), mocap ground truth and
anchor positions.

**Ground truth and error definition.**

- True distance = ‖mocap position (interpolated at `Logtime`) − anchor position‖.
- Error = raw TWR range − true distance.
- The mocap-marker-to-antenna offset is unknown and not corrected. It sits inside the
  "before" error.
- The same March 2023 anchor survey is used for both sessions. If anchors moved in session 2,
  that adds error the model cannot know about.
- As a sanity check, session 1's median error is −2.6 cm. Per-anchor medians are all within
  ±0.18 m.

### Protocol

- **Held-out session:** train on one recording, test on the other, both directions. No rows
  from the test recording are used in training.
- **Model:** `HistGradientBoostingRegressor(loss="absolute_error", max_iter=300,
  learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=20, random_state=0)`. It is trained
  on 26 features: CIR shape, DW1000 power and amplitude registers, the fractional part of
  FP_INDEX, and the raw range.
  - Anchor ID is **not** a feature, so the model cannot just memorize each anchor's bias.
- **Correction:** corrected range = raw − predicted error.
- **Confidence intervals:** 95 % CIs from a block bootstrap. The test recording is cut into
  10-second blocks (about 55 blocks), and blocks are resampled 2,000 times with seed 0.
  Consecutive ranges are correlated, so a row-level bootstrap would make the CIs too narrow.

### Results (absolute ranging error, metres)

| Train → test | n test | MAE before | MAE after | p95 before | p95 after |
| --- | --- | --- | --- | --- | --- |
| **Session 1 → session 2** | 1,622 | **0.196** [0.180, 0.210] | **0.144** [0.131, 0.157] | **0.584** [0.515, 0.675] | **0.472** [0.400, 0.558] |
| **Session 2 → session 1** | 1,757 | **0.191** [0.174, 0.210] | **0.144** [0.130, 0.160] | **0.540** [0.481, 0.600] | **0.424** [0.382, 0.476] |

The paired change (after − before), with block-bootstrap 95 % CI:

| Train → test | ΔMAE | Δp95 |
| --- | --- | --- |
| Session 1 → session 2 | −0.051 m [−0.058, −0.045] | −0.112 m [−0.170, −0.071] |
| Session 2 → session 1 | −0.047 m [−0.054, −0.041] | −0.116 m [−0.157, −0.073] |

- **MAE falls by about 25 %** in both directions, from 0.19–0.20 m to 0.14 m, on a session
  recorded six months apart from the training one.
- **p95 falls by about 20 %**, from 0.54–0.58 m to 0.42–0.47 m.
- Neither CI on the change includes zero.
- **Reference: subtracting a constant offset only** (the training session's median error)
  barely helps. MAE becomes 0.193 m and 0.191 m; p95 becomes 0.607 m and 0.558 m. So the gain
  comes from the per-packet CIR and diagnostic features, not from removing a fixed bias.
- The mean signed error after correction is +4 to +5 cm, versus +3 cm before. The
  absolute-error loss targets the median, so this skew is expected.

### Limitations

- One building, one tag, one robot route style, DW1000 on channel 5 only.
- Only two sessions, so "held-out session" means two folds.
- The anchor survey is shared between the two sessions.
- The marker-to-antenna offset is not modelled.
- The CIs cover resampling within the test session, not variation between sessions.

---

## 3. LOS versus NLOS ranging error on real data

The TWR data set has no per-link LOS/NLOS label, so we split it with the training-free
Decawave proxy: RX − FP > 6 dB means "likely NLOS". Both sessions are pooled.

| Group | n | Mean error | Median error | MAE | p95 \|error\| |
| --- | --- | --- | --- | --- | --- |
| Likely LOS (RX − FP ≤ 6 dB) | 2,102 | +0.010 m | −0.046 m | 0.176 m | 0.486 m |
| Likely NLOS (RX − FP > 6 dB) | 1,277 | +0.063 m | −0.004 m | 0.222 m | 0.732 m |

The likely-NLOS group has a higher mean error (+5 cm) and a longer positive tail: its p95 is
0.73 m against 0.49 m for likely LOS. This is the positive NLOS bias, but it is modest here,
and the proxy label is noisy.
