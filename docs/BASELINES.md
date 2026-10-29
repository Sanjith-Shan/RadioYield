# Baselines

Every headline number is compared with something outside this repo: a datasheet, a standard, a paper, a competing tool, or a known answer. The baseline was written down here before the comparison was made.

| Area | Baseline | Detail |
|---|---|---|
| STDF parsing speed and correctness | pystdf; Semi-ATE STDF. Versions, how each was invoked, and what each does inside the timed region | [baselines/stdf.md](baselines/stdf.md) |
| UWB LOS/NLOS classification | Bregar & Mohorčič, IEEE Access 6:17429–17441 (2018), DOI 10.1109/ACCESS.2018.2817800: CNN 87.4%, protocol not stated. Zhang et al., Sensors 23:8552 (2023): 90.0%, random 7:2:1. No paper reports a site- or config-held-out protocol | [baselines/uwb.md](baselines/uwb.md) |
| UWB ranging error | Uncorrected two-way ranging on the same held-out session | [baselines/uwb.md](baselines/uwb.md) |
| Datasheet RF values (simulator nominals; future sensitivity comparison) | ESP32-C6 datasheet v1.5, Section 6 tables | [baselines/references.md](baselines/references.md) |
| Path-loss exponent | ITU-R P.1238-10 (2019) N at 2.4 GHz; P.1238-13 (2025) site-general α | [baselines/references.md](baselines/references.md) |
| PAT limits | AEC-Q001 Rev D §2.4, §3.1.1, §3.1.2 | [baselines/references.md](baselines/references.md) |
| Analysis detectors | Known answers: faults injected into simulated lots | [results/sim.md](results/sim.md) |
