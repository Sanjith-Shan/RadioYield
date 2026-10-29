# UWB baselines (recorded before any comparison)

Each published number below was read in the source. Anything we could not read in the primary
source is marked **unverified** and is not used for comparison.

## eWINE UWB LOS/NLOS Data Set: LOS/NLOS classification

Data set: github.com/ewine-project/UWB-LOS-NLOS-Data-Set (commit `9a7e755a`, CC-BY-4.0).
42,000 rows. The public files are shuffled and carry no environment label (see
`docs/results/uwb.md`).

### B1. Bregar & Mohorčič 2018: the paper that released the data (primary baseline)

K. Bregar and M. Mohorčič, "Improving Indoor Localization Using Convolutional Neural Networks
on Computationally Restricted Devices," *IEEE Access*, vol. 6, pp. 17429–17441, 2018.
**DOI 10.1109/ACCESS.2018.2817800.** The DOI often quoted for it, 10.1109/ACCESS.2018.2817572,
resolves to an unrelated paper.

Table 1, "NLoS classification performance" (p. 17435). The positive class is NLOS.

| Model | Accuracy | Precision | Sensitivity | F1 |
| --- | --- | --- | --- | --- |
| SVM (linear), 6 features | 82.5 % | 75.3 % | 97.1 % | 84.8 % |
| SVM (RBF), 6 features | 82.6 % | 75.2 % | 97.5 % | 84.9 % |
| MLP, 6 features | 82.9 % | 76.9 % | 94.1 % | 84.7 % |
| **CNN on 152 CIR bins from the first path** | **87.4 %** | 85.9 % | 89.4 % | 87.6 % |

- The paper reports no AUC.
- The 6 features are RSS, first-path RSS, mean excess delay, RMS delay spread, kurtosis and
  skewness.
- **Protocol: not stated.** The paper says only that evaluation "was performed using different
  subsets of data collected during the measurement campaign."
- The confusion matrices imply a balanced test set of exactly 12,000 rows (6,000 LOS and
  6,000 NLOS). That size matches 2 of the 7 environments held out, but also matches a
  class-stratified random subset. **Which one it is cannot be verified.**
- The authors' released code (github.com/ewine-project/UWB-localization,
  `tools/data_tools.py`) shuffles the data and splits it 60/40 at random, which gives about
  16.8k test rows. So the code is not exactly the Table 1 protocol either.
- **Our reading:** treat 87.4 % as a number under an unspecified, probably random,
  protocol. Do not treat it as a site-split number.

### B2. Zhang et al. 2023: random split, includes a hand-crafted-feature model

"Research on NLOS/LOS Identification Method Based on CNN-Channel Attention Module," *Sensors*
23(20):8552, 2023. DOI 10.3390/s23208552.

- **Protocol:** random 7:2:1 train/validation/test split of the eWINE data. No environment
  split.
- Table 5:
  - **Random forest on 12 hand-crafted features (100 trees): 87.43 %** accuracy, F1 87.85 %.
    This is the closest like-for-like comparison to our feature + gradient-boosting model.
  - CNN-CAM on the full 1016-sample CIR: 90.00 % accuracy, F1 90.22 %.
  - CNN-SVM: 86.12 %. CNN-LSTM: 84.94 %.

### B3. Baldini 2026: per-"environment" random cross-validation

"On the Application of Entropy-Based Metrics for UWB LOS/NLOS Classification with Ensemble
Instance Selection," *Entropy* 28(8):904, 2026. DOI 10.3390/e28080904.

- The paper reports per-environment results, but runs random 4-fold cross-validation
  **within** each environment. It never leaves an environment out.
- The paper does not say how it recovered environment labels. Its "approximately 3000 LOS
  and 3000 NLOS" per environment matches the per-file counts of the 7 shuffled CSV parts.
  So its "environments" are most likely the file chunks, not real sites. This is our
  inference.
- Best: 85.44 % (SVM after instance selection). Without instance selection: 79.99 %.

### Not used (unverified)

- Bregar, Hrovat & Mohorčič 2016 (IPSSC; the citation the README asks for): "90 %" from a
  one-page abstract. It predates the public data set and states no protocol.
- Pei et al. 2024, FCN-Attention, 88.24 %: abstract only. The full text and protocol could not
  be read.
- Jiang et al. 2020 CNN-LSTM, and several numbers quoted second-hand in Baldini 2026.

### Leave-one-environment-out on this data set

**No published paper reports it.** We searched the original repo (4 commits, already shuffled
in the first one), the authors' code repo, forks, Zenodo and the IEEE supplementary material.
None has an unshuffled, environment-labelled version. So **there is no published site-split
baseline to compare against.**

## IDLab Ghent TWR Correction Dataset: ranging error

Source: Hugging Face `iwine-ugent/uwb-positioning-datasets` (card sha `d813ce72`), with the
files on imec Nextcloud. The card asks users to cite D. Coppens, B. van Herbruggen, A. Shahid,
E. de Poorter, "Removing the Need for Ground Truth UWB Data Collection: Self-Supervised Ranging
Error Correction Using Deep Reinforcement Learning," *IEEE TMLCN* 2, 1615–1627, 2024.

- We did not read a per-session MAE/p95 number for this exact protocol in that paper.
  **No published number is used.**
- The baseline for our correction is the **uncorrected ADS-TWR range on the same held-out
  session** (spec section 6). As a second, stronger reference, we also report correction by a
  constant offset only (the training session's median error).

## DW1000 power formulas used as features

Decawave DW1000 User Manual:

- §4.7.1, first-path power: `10·log10((F1²+F2²+F3²)/N²) − A` dBm.
- §4.7.2, receive power: `10·log10(C·2¹⁷/N²) − A` dBm.
- In both, A = 121.74 for 64 MHz PRF. For 16 MHz PRF, A = 113.77 in v2.11; v2.05 says 115.72.
  All of our data uses 64 MHz PRF, so this discrepancy does not touch any number here.
- The formulas and the 121.74 constant were checked in v2.05 and v2.11. The code comment
  cites the section numbers.
- Decawave application note APS006 Part 3 gives the rule of thumb "RX − FP > 6 dB ⇒ likely
  NLOS", which we use as a training-free reference.
