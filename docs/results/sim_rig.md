# Virtual rig: the M2 session rehearsed in simulation

**Label: SIMULATED.** Nothing on this page is a measurement of a hallway or an ESP32-C6. It
answers a different question: *if the hallway and boards behave like this model, does the
session in `docs/RIG.md` produce data the analysis can use, and how close does each fit land
to the known answer?* The rig numbers themselves stay NOT YET MEASURED in [rig.md](rig.md).

```bash
python -m radioyield.sim.rig session --seed 7 --out out/sim_rig/seed7    # one session, ~10 s
radioyield report --rig out/sim_rig/seed7 --out examples/sim_rig_session.html
python -m radioyield.sim.rig score --seeds 30 --out docs/results/sim_rig.json   # ~80 s, 8 workers
python -m radioyield.sim.rig score --seeds 30 --as-written --out docs/results/sim_rig_as_written.json
```

Raw output: [sim_rig.json](sim_rig.json) (plans as revised), [sim_rig_as_written.json](sim_rig_as_written.json)
(plans as first written). Seeds 2000-2029. Example report: [`examples/sim_rig_session.html`](../../examples/sim_rig_session.html).

## What runs

Every step of RIG.md section 5 in order: 37 runs, 563 steps, about 126,000 packets per
session. The real code does the work: plan loader, collector runner, line protocol,
Parquet and STDF writers, `analysis.rig` fits and `compare_builds`. Only the boards are
fake: the serial test double from `radioyield/collector/fake.py`, given a channel model.
The conducted M3 plans (Wi-Fi and BLE) run at the end of each session.

## The simulated truth

| Parameter | Value | Where it comes from |
|---|---|---|
| Path-loss exponent | 1.57 | ITU-R P.1238-13 (2025) site-general, corridor LoS α |
| Large-scale shadowing σ (per position) | 3.77 dB | same table |
| Loss at 1 m | 40.2 dB | Friis at 2.44 GHz, 0 dBi antennas (the 0 dBi is an assumption) |
| Wi-Fi sensitivity | −99.2 dBm at 8% PER | ESP32-C6 datasheet v1.5 Table 6-4, 11b 1 Mbps. *Assumption:* ESP-NOW broadcasts at the driver default, 1 Mbps |
| BLE sensitivity | −98.5 dBm at 30.8% PER | datasheet Table 6-12, 1M PHY |
| 802.15.4 sensitivity | −104 dBm at 1% PER | datasheet Table 6-18 |
| Waterfall logistic scale | 1.0 dB | assumption |
| Frequency-selective term per (position, channel) | σ 2 dB | assumption |
| Per-step and per-packet fading | σ 0.5 dB, 1 dB | assumption |
| One interior wall | 6 dB at 6 m | assumption |
| REF RSSI bias | N(0, 2 dB) per session | assumption; the datasheet states no RSSI accuracy |
| DUT TX error | N(0, 0.5 dB) per unit | assumption; the datasheet states no TX tolerance |
| Attenuator tolerance; cable | ±0.5 dB each; 1.3 dB true vs 1.0 dB written in the plan | assumption |
| EVT2 firmware table | −8 qdBm, then the driver's power quantisation | the firmware's actual EVT2 variant |
| ORIENT_B; drift | 0 dB; 0 dB | chosen to test the nulls |

Not modelled: other 2.4 GHz traffic, radiated leakage around the conducted chain, board
resets, USB drop-outs, temperature. Each would make the real session harder than this one.

## What the rehearsal found in the plans as first written

Thirty sessions, plans exactly as first written:

| Finding | Evidence (30 sessions) | Change made |
|---|---|---|
| **The OTA PER waterfalls cannot work in a hallway.** At the weakest point (6 m, through one wall) the link has ~58 dB of loss; reaching the Wi-Fi waterfall at the lowest power (2 dBm) needs ~100 dB. | Wi-Fi OTA and BLE OTA: PER 0% on every step, **0/30 fitted** | RIG step 8 is now a link-margin check. The waterfall comes from the conducted setup |
| **The conducted chain as specified (80 dB) is too weak for Wi-Fi.** | Wi-Fi conducted: PER 0% on every step, **0/30 fitted** (BLE fitted, because its power goes down to −15 dBm) | `conducted_*.yaml` and RIG §9 now say 110 dB (30+30+30+20), plus a note on shielding |
| **The build comparison could not flag anything.** With 5 repeats per build, the smallest possible Mann-Whitney p is 0.008. Holm across 9 tests multiplies it past 0.05. | Deliberate −2 dB EVT2 change: flagged in **3/30** sessions, though the median shift estimate was exactly −2.0 dB | `build_compare_wifi.yaml` now runs 10 repeats |
| **Five path-loss positions give an uninformative exponent.** | Exponent 95% CI width: median **2.80** (p95 4.57). Every reference (1.47–3.0) sits inside almost every CI | RIG.md now walks 10 marks: 1, 1.5, 2, 3, 4, 6, 8, 11, 16, 20 m |

## Scores with the revised plans

| Quantity | Result over 30 sessions | Reading |
|---|---|---|
| Path-loss exponent, error vs 1.57 | median −0.01, 90% of sessions within −0.41 to +0.46 | unbiased |
| Path-loss exponent, 95% CI | width median **1.44** (was 2.80); covers the truth 30/30 | conservative (few degrees of freedom), and still wide: shadowing of ~4 dB is the limit. It separates LoS (~1.5) from the office value (3.0), but not from free space (2.0) |
| Shadowing σ estimate | median 4.14 dB, against a 3.9 dB truth (3.77 large-scale plus the channel-averaged frequency term) | about right |
| TX linearity slope, Wi-Fi | error median −0.0001, 90% within ±0.03; CI covers 1.0 in 28/30 | |
| TX linearity slope, BLE | error median 0.002, 90% within −0.015 to +0.012; CI covers 29/30 | |
| Conducted level at 10% PER, Wi-Fi | fitted **30/30**; error median **+0.54 dB** (90%: −0.44 to +1.73); CI covers truth **4/30** | the CI is packet statistics only. The error is the chain: TX error, attenuator tolerance, a cable 0.3 dB lossier than written. The report now says so under the fit |
| Conducted level at 10% PER, BLE | fitted 30/30; error median +0.57 dB (90%: −0.82 to +2.32); CI covers 6/30 | same |
| EVT2 − EVT1 (deliberate −2 dB table change) | flagged in **30/30** sessions, 9/9 tests each; median-shift error 0.0 dB (90%: −0.5 to 0) | RSSI is an integer, so shifts come out in whole dB |
| ORIENT_B − ORIENT_A (truth: no change) | flagged in **0/30** sessions | |
| DUT-02 − DUT-01 TX offset | error median −0.04 dB, 90% within −0.76 to +0.99 dB | an integer RSSI limits unit-to-unit resolution to about 1 dB |
| 1 m smoke, start vs end (truth: no drift) | error median 0, 90% within −0.5 to +1.0 dB | |
| OTA waterfalls | 0/30 fitted (expected, see above) | link-margin check only |

## What this does and does not support

- It supports: the runbook as revised produces a path-loss fit, two TX linearity fits, two
  conducted waterfalls (given the M3 kit), and two build comparisons that each give the
  known answer. Each runs through the real collector and analysis code.
- It does not support any claim about a real hallway. If the hallway's shadowing is closer
  to P.1238-10's 8-10 dB than to P.1238-13's 3.8 dB, the path-loss CI will be roughly twice
  as wide again. If channels 1/6/11 carry access-point traffic, PER will not be 0 at 1 m.
- Conducted-sensitivity numbers against the datasheet need the chain calibrated: the
  rehearsal's chain error (about ±1.5 dB at the 90% level) is larger than the PER
  statistics' CI (about ±0.3 dB).
