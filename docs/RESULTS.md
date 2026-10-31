# Results

Every number RadioYield claims, each with the command that produced it. The detail lives in the per-component pages linked below. This page is the index and the label check.

**Labels.** Every row says what kind of number it is:
- **MEASURED**: our code, run on this machine.
- **PUBLIC DATA**: real measurements recorded by other labs.
- **SIMULATED**: known-answer scores on synthetic lots, never quotable as a measurement.
- **NOT YET MEASURED**: needs the rig (milestone M2).

**Machine** for everything measured so far: Apple M3 Pro, 12 cores, macOS 26.5.1, Python 3.12.7, 2026-11-01.

## Headline numbers

| Claim | Number | Label | Compared against | Source |
|---|---|---|---|---|
| STDF read throughput, largest real public file (11.7 MB, 162,655 records) | **34.3× faster than pystdf** (95% CI 33.8–35.5); **257× faster than Semi-ATE STDF** (251–278) | MEASURED | pystdf and Semi-ATE STDF, same files and machine, median of 7 runs | [results/stdf.md](results/stdf.md) |
| STDF read throughput, 65.6 MB generated file (1.2M PTRs) | 40.7× vs pystdf (29.5–42.6); 448× vs Semi-ATE (326–494); 184 MB/s, 3.4M records/s | MEASURED | same | [results/stdf.md](results/stdf.md) |
| STDF parser fuzzing | 30-minute libFuzzer + ASan + UBSan campaign: 2,088,824 executions, **0 crashes**. 2 bugs found outside the campaign (a 30 s smoke run and a mutation test), both fixed with regression tests. 10-minute verification run after the fixes: 0 crashes | MEASURED | none; it is a robustness fact | [results/stdf.md](results/stdf.md) |
| STDF correctness | Round trip in both byte orders equals pystdf's decoding; 10 public sample files match pystdf record for record | MEASURED | pystdf, Semi-ATE | [results/stdf.md](results/stdf.md) |
| Detector recall, 6 fault scenarios × 50 lots | Station offset, station drift, build shift and bimodal antenna lot: **50/50 each** (Wilson 95% CI 92.9–100%). PAT outlier units: **98.4%** of injected units | SIMULATED | known answers | [results/sim.md](results/sim.md) |
| False alarms on 50 clean lots | Drift **0/50**; PAT **0/50** (0 of 100,250 units); bimodality 0/50; golden-unit correlation 1/50; build comparison 3/50 (the family-wise 5% that Holm is set to) | SIMULATED | known answers | [results/sim.md](results/sim.md) |
| Drift time to detect, 0.1 dB/h | Median **1.89 h** after onset, p90 2.28 h | SIMULATED | known onset | [results/sim.md](results/sim.md) |
| UWB LOS/NLOS accuracy, strongest held-out split the data allows (leave-one-acquisition-config-out; true leave-one-site-out is impossible because site labels are not in the release) | **92.6%** (F1 0.925, AUC 0.979) | PUBLIC DATA | not reported by any paper on this protocol | [results/uwb.md](results/uwb.md) |
| UWB LOS/NLOS accuracy, random 5-fold | **93.1% ± 0.3**; 88.4% without the range feature | PUBLIC DATA | Bregar & Mohorčič 2018 CNN 87.4% (protocol not stated); Zhang et al. 2023 CNN-CAM 90.0% (random 7:2:1) | [results/uwb.md](results/uwb.md), [baselines/uwb.md](baselines/uwb.md) |
| UWB ranging error, held-out session, before → after correction | Session 1 → 2: MAE **0.196 → 0.144 m**, p95 0.584 → 0.472 m. Session 2 → 1: MAE 0.191 → 0.144 m, p95 0.540 → 0.424 m. Improvement CIs exclude 0 | PUBLIC DATA | uncorrected ranging | [results/uwb.md](results/uwb.md) |
| Virtual rig: the full M2 session (37 runs, ~126k packets) through the real collector and fits, against a simulated hallway with known truth | Found 4 defects in the plans as first written, all fixed: OTA waterfalls unreachable (0/30 fitted), 80 dB conducted chain too weak for Wi-Fi (0/30), build compare unable to flag a deliberate −2 dB change (3/30), path-loss CI 2.8 wide. Revised plans: −2 dB change flagged **30/30**, null change 0/30; path-loss exponent unbiased, CI width 1.44; conducted waterfall fitted 30/30, but chain error (median +0.5 dB) exceeds the statistical CI | SIMULATED | known answers (P.1238-13 corridor LoS, ESP32-C6 datasheet sensitivities, stated assumptions) | [results/sim_rig.md](results/sim_rig.md) |
| Path-loss exponent and shadowing σ | — | NOT YET MEASURED | ITU-R P.1238 | [results/rig.md](results/rig.md) |
| TX power step linearity | — | NOT YET MEASURED | commanded steps | [results/rig.md](results/rig.md) |
| Level at 10% PER, Wi-Fi and BLE | — | NOT YET MEASURED | datasheet sensitivity, conducted only | [results/rig.md](results/rig.md) |
| Packets logged, tests run | — | NOT YET MEASURED | — | [results/rig.md](results/rig.md) |

## Test suite

`make test` builds the C++ STDF extension and the firmware codec host tests, then runs everything that needs no boards. Last run 2026-11-01 from a clean build:

| Suite | Result |
|---|---|
| STDF ctest | 3/3 (2,028 C++ checks) |
| Firmware codec ctest | 1/1 (4,999 checks) |
| pytest | **207 passed** (includes the UWB public-data tests, the slow tests and the virtual-rig session tests) |

## Reproduce

```bash
make venv && make test          # build + every hardware-free test
make score                      # known-answer scoring, writes results/sim_score.json (~1 min, 8 workers)
make rig-sim                    # virtual rig rehearsal of the M2 session, writes results/sim_rig*.json (~3 min)
make reports                    # one HTML report per simulated scenario into examples/
python -m radioyield.uwb fetch && make uwb       # public UWB data (~318 MB) and both UWB results
python stdf/bench/bench.py ...  # see results/stdf.md for the exact benchmark invocation
```
