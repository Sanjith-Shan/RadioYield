# Analysis engine: known-answer scores (SIMULATED)

> **SIMULATED.** These figures score the detectors against faults we injected into synthetic lots. They show the engine finds what it was built to find, and how often it cries wolf. They are **not** measurements of any radio, line or product.

| | |
|---|---|
| **Command** | `python -m radioyield.sim.score --seeds 50 --seed-offset 1000 --sensitivity-seeds 20 --calibration-seeds 40` (also `make score`) |
| **Raw output** | `docs/results/sim_score.json`: every lot's outcome plus the summary |
| **Machine** | Apple M3 Pro, macOS 26.5.1, Python 3.12.7; 52 s on 8 worker processes |
| **Date** | 2026-11-01 |

## Setup

- **Lot.** 2,000 units, 4 stations, an 8-hour shift and 13 tests: Wi-Fi/BLE/802.15.4 TX power, frequency error, EVM, RSSI error, PER near sensitivity, and an OTA coupler check.
  - Failing units are retested once.
  - Five golden units run 3× per station at the start and the end of the shift.
- **Parameters.** Nominal values come from the ESP32-C6 datasheet where it gives one. Spreads and most limits are assumptions. Every source is in `radioyield/sim/params.py`.
- **Faults.** One injected fault per scenario, exactly as the spec sets them.
- **Seeds.**
  - Scored seeds: 1000–1049.
  - Drift calibration seeds: 0–39 (disjoint).
  - Sensitivity sweep seeds: 6000–6019.
- **Intervals.** Every rate below carries a Wilson 95% interval: 50 lots cannot distinguish 100% from 93%.

## Headline: detection and false alarms, 50 lots per scenario

| Scenario (injected fault) | Detector | Detected | False flags in faulted lots | Clean lots with any false alarm |
|---|---|---|---|---|
| Station +1.5 dB on all TX power tests | golden-unit correlation | **50/50** (95% CI 92.9–100%) | 1 lot, 1 flag | **1/50** (2%; CI 0.4–10.5%) |
| Same, without golden units | production cross-check | 50/50 | 0 lots | 0/50 |
| Station drifting +0.1 dB/h from hour 3 | CUSUM, h = 18 | **50/50** | 0 lots | **0/50** (CI 0–7.1%) |
| Build EVT2 +0.8 dB on Wi-Fi TX power | Mann-Whitney + Holm + bootstrap CI | **50/50**, all 3 shifted tests every time | 4 lots, 5 flags | 3/50 (6%; CI 2.1–16.2%) |
| 30% of units −3 dB on the OTA antenna test | GMM BIC + Ashman D | **50/50** | 0 lots | **0/50** |
| 0.5% units 6.5–9σ out but inside spec | dynamic PAT (AEC-Q001) | **98.4% of injected units**; all 10 in 42/50 lots | 0 lots | **0/50**, 0 of 100,250 units |

### Station drift: time to detect

| Statistic | Hours after drift onset |
|---|---|
| Median | 1.89 |
| p90 | 2.28 |
| Max | 2.60 |

At 0.1 dB/h, 1.9 h is a 0.19 dB shift. That is about 0.7 σ of a TX power reading.

### Build shift: estimate quality

- **Estimated shift:** within 0.013 dB of the injected 0.8 dB on average.
- **Coverage:** the 95% interval covered the truth in 94% of cases (141 of 150 test-lots).

### PAT misses

All misses are units injected near 6.5σ of the model's sigma, where the lot's own robust sigma came out a little larger. They fall just inside the 6σ PAT limit. That is the limit working as written, and the injection range was not moved to hide it.

## How the false-alarm rates were controlled (and what it cost)

### Build comparison

It fires on 6% of clean lots, which is the family-wise 5% that Holm across 13 tests is designed to give (CI 2–16%).

These are false positives by construction, because nothing was injected. Pass `practical={test: threshold}` to `compare_builds` to require a minimum shift size before calling a shift. The default is off, so the reported rate is the raw statistical one.

### Drift

The textbook CUSUM (k = 0.5, h = 5) has an in-control ARL of about 465 readings. A shift charts about 44 streams × 400 readings, so h = 5 alarmed on **every** clean lot (calibration curve below).

**Step 1: budget-derived h.** We derived h from a budget of 0.05 expected false alarms per lot, using Siegmund's ARL approximation. That gave h ≈ 11.6. Clean lots still alarmed about 40% of the time. The cause is that the in-control mean and sigma are *estimated* from 150 readings (the Phase I estimation effect), and the analytic formula assumes they are known.

**Step 2: calibration.** We calibrated h on disjoint seeds (0–39) as the smallest h with a clean-lot false-alarm rate ≤ 5%, and scored on seeds 1000+.

| h | Clean lots alarming (calibration seeds) | Median time to detect 0.1 dB/h (h) |
|---|---|---|
| 6 | 100% | 0.77 |
| 8 | 97.5% | 1.17 |
| 10 | 55% | 1.32 |
| 12 | 25% | 1.41 |
| 14 | 10% | 1.57 |
| 16 | 7.5% | 1.71 |
| **18 (chosen)** | **0%** | **1.79** |
| 20 | 0% | 1.88 |
| 24 | 0% | 2.09 |

Moving from the textbook h = 5 to h = 18 costs about 1 hour of detection delay and removes nearly all false alarms. On a real line, h should be recalibrated on that line's own clean history.

### Golden-unit slope

With five golden units near nominal, a slope from 5 points is noise. 52 charts at 5% each would flag a healthy line most of the time. So a slope is assessed only when the golden units span ≥ 10× the station's repeatability, and it is Holm-corrected across charts.

### Non-normal tests

The PER test (binomial, skewed) and the RSSI error (integer-quantized) are **not** charted for drift and **not** PAT-screened. The reports say why. Without that gate they were the main source of false PAT flags.

## Sensitivity: detection rate vs fault size (20 lots per point)

### Build shift on Wi-Fi TX power (all 3 tests must be flagged)

Unit spread is σ ≈ 0.34 dB, with 1,000 units per build.

| Shift (dB) | 0.02 | 0.05 | 0.1 | 0.2 | 0.4 | 0.8 |
|---|---|---|---|---|---|---|
| Detected | 0% | 75% | 100% | 100% | 100% | 100% |

### Station offset (golden correlation, tolerance 0.6 dB = 10% of the ±3 dB window)

| Offset (dB) | 0.2 | 0.4 | 0.6 | 0.8 | 1.0 | 1.5 |
|---|---|---|---|---|---|---|
| Detected | 0% | 0% | 90% | 100% | 100% | 100% |

The station detector is designed to ignore offsets inside the correlation tolerance. The 0% below 0.6 dB is the tolerance doing its job, not a miss.

The production cross-check (Holm across station × test) is the tool for smaller offsets when golden data are absent.

## Example reports

`examples/sim_<scenario>.html` holds one self-contained report per scenario, from seed 7. They were produced by `make reports` (`radioyield demo --out examples`). Each shows the SIMULATED banner and the "what was not measured" box.

One behaviour worth showing: in the station-offset lot, the TX power distributions are *bimodal* because one station in four reads +1.5 dB. The report attributes the minor mode to "station ST3: 100% of its units are in the minor mode". In the antenna lot, it correctly finds no single station or build behind the minor mode.
