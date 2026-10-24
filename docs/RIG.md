# Rig session runbook (M2)

Goal: about two hours with two ESP32-C6 boards in a hallway, producing real OTA data for
path loss, TX power linearity, BLE and WiFi PER vs received level, and two labelled
"builds", all through the collector into STDF + Parquet. Nothing below has been run on
hardware yet; every number the session produces goes into `docs/results/rig.md`.

The whole session has been rehearsed against a simulated hallway (`python -m radioyield.sim.rig`,
results in `docs/results/sim_rig.md`). The rehearsal changed four things in this runbook:
10 path-loss marks instead of 5, 10 build-compare repeats instead of 5, step 8 demoted to a
link-margin check, and 110 dB instead of 80 dB in the conducted chain. Each is marked
*(rehearsal)* below.

Files involved: firmware `firmware/` (protocol in `firmware/PROTOCOL.md`), plans `plans/`,
collector `radioyield/collector/` (README there has the file formats).

---

## 0. Answer this first (open hardware question)

Before anything else, write down:

1. **How many ESP32-C6 boards** you have. Two is the minimum (one DUT, one REF). Three or
   more allows a second DUT (unit-to-unit spread) or a second "station".
2. **Which model** each one is (e.g. Espressif ESP32-C6-DevKitC-1, SparkFun Thing Plus
   ESP32-C6, Seeed XIAO ESP32C6, a WROOM-1U module board). This decides:
   - the console port: native USB (USB-Serial-JTAG, the default build) vs a USB-UART
     bridge (build with `sdkconfig.defaults.uart`, run the collector with `--baud 921600`).
     The DevKitC-1 has both; use the port labelled **USB**.
   - the antenna: PCB trace, ceramic chip, or u.FL.
3. **Whether any board has a u.FL connector.** Only then is the conducted option (section
   9) possible. Note: some boards (e.g. the XIAO ESP32C6) have both an on-board antenna and
   a u.FL with an RF switch controlled by GPIOs. **The firmware does not drive any antenna
   switch**; check the board's docs before relying on its u.FL.

If you have exactly two boards and no u.FL: everything below except section 9 applies, and
every number is OTA, labelled OTA.

## 1. What this rig measures, and what it cannot

| | OTA (this session) | Conducted (optional, section 9) |
| --- | --- | --- |
| Path | antennas + hallway (multipath, people, walls) | coax + fixed attenuators |
| Level axis | REF's own RSSI estimate (integer dBm, uncalibrated) | TX power - attenuators - cable loss (computed) |
| PER vs level | yes, vs RSSI (biased at the bottom of the waterfall) | yes, vs computed input level |
| Compare with datasheet RX sensitivity | **no** | roughly, for the same rate, with the caveats in section 9 |
| Path-loss exponent + shadowing | yes (ITU-R P.1238 comparison) | n/a |
| TX power step linearity | yes, as relative RSSI change | yes, cleaner |

**Cannot measure, either way** (needs an RF tester, power meter or chamber):
EVM, spectral mask / ACLR, absolute TX power, frequency error, TRP / TIS (needs an
anechoic chamber and a positioner), blocking / desense (no controlled interferer).
RSSI is the receiver's estimate, not a calibrated power measurement; chip-to-chip
offsets of a few dB are normal. Say this in every report.

## 2. What to bring

- 2 (or more) ESP32-C6 boards, labelled with masking tape: `DUT-01`, `REF-01` (`DUT-02`...).
- 2 USB **data** cables long enough to reach both boards from the laptop; a USB extension
  or a 5 m active cable for the 16 and 20 m points (or run the laptop on a cart next to the DUT and
  only the REF far away: the REF must stay connected for logging, the DUT too, since the
  host drives it; plan cable routing before the day).
- Tape measure (>= 20 m, or a 5 m one used carefully), masking tape, a marker.
- Two identical stools/chairs/boxes so both boards sit at the same height (~0.75 m).
- A notebook (section 7).
- Optional, conducted: section 9 shopping list.

## 3. Before the day (at the desk, ~30 min)

**a. Get the firmware images.** Either:
- From CI (no ESP-IDF needed): download the `radioyield-fw` artifact of the latest green
  run of the firmware build job. It has `radioyield_{dut,ref}_{evt1,evt2}.bin` (merged images
  flashed at offset 0).
- Or build locally with ESP-IDF v5.5.x exported: `firmware/build_all.sh evt1` and
  `firmware/build_all.sh evt2` (add `uart` as a second argument for UART-bridge boards).

**b. Tools in the project venv** (small, no ESP-IDF):
```
source .venv/bin/activate
pip install pyserial esptool
```

**c. Flash.** Plug in one board, find its port (`ls /dev/cu.usbmodem*`), then:
```
python -m esptool --chip esp32c6 -p /dev/cu.usbmodemXXXX -b 460800 write_flash 0x0 radioyield_dut_evt1.bin
python -m esptool --chip esp32c6 -p /dev/cu.usbmodemYYYY -b 460800 write_flash 0x0 radioyield_ref_evt1.bin
```
(with a local build: `idf.py -B firmware/build/dut_evt1 -p PORT flash`, or flash `firmware/build/dist/*.bin` as above). If esptool cannot
connect, hold BOOT, tap RESET, release BOOT, retry.

**d. Identify the boards.**
```
python -m radioyield.collector ports
```
Expected: one line `-> DUT fw=0.1.0 mac=... label=EVT1_TXTABLE_NOMINAL` and one `-> REF ...`.
Write each MAC next to its tape label. Ports can renumber when replugged; re-run `ports`
whenever you replug.

**e. Desk smoke test** (boards 1 m apart on the desk):
```
export DUT=/dev/cu.usbmodemXXXX REF=/dev/cu.usbmodemYYYY
python -m radioyield.collector run plans/smoke_wifi_1m.yaml --dut $DUT --ref $REF --out out/desk/
python -m radioyield.collector run plans/smoke_ble_1m.yaml  --dut $DUT --ref $REF --out out/desk/
```
Pass criteria (functional, not RF claims): every step prints `tx 200 rx ~200`, PER near 0,
no `FLAGS`. If BLE `ch ALL` PER is high at 1 m, see troubleshooting. Write down what the
BLE single-channel steps show for `RX_FRAC` (~33% each = REF scan rotates; one channel
~100% = it parks): that tells you how the REF scanner behaves.

**f. Know what the output looks like without boards:** `python -m radioyield.collector run
plans/smoke_wifi_1m.yaml --fake --out out/sim/` (SIMULATED, numbers meaningless).

## 4. Hallway setup (~15 min)

- REF fixed at one end, on its stool. Put tape marks at **1, 1.5, 2, 3, 4, 6, 8, 11, 16, 20 m**
  from the REF along the hallway centreline *(rehearsal: with 5 marks the exponent's 95% CI was
  ~2.8 wide, too wide to tell any two references apart; 10 marks halve it. A shorter hallway:
  use as many marks as fit, spaced evenly in log distance)*, and pick one **through-wall** point (DUT in a room, door
  closed, one interior wall between DUT and REF; measure the straight-line distance).
- Both boards at the same height (~0.75 m), same orientation for every distance: antennas
  vertical, PCB faces toward each other. Photograph the setup once.
- Nobody standing between the boards during a run; note people walking past.
- Note the WiFi environment: the channels of nearby access points (macOS: hold Option and
  click the WiFi icon, or `system_profiler SPAirPortDataType`). ESP-NOW shares channels 1/6/11
  with them; busy channels show up as loss that is not path loss.

## 5. Session order and timing

Radio time per plan is from `python -m radioyield.collector validate plans/*.yaml`; the rest
is moving boards and writing notes. Use `--out out/rig/` for everything.

| # | Where | Command (add `--dut $DUT --ref $REF --out out/rig/`) | Radio | Wall clock |
| --- | --- | --- | --- | --- |
| 1 | 1 m | `run plans/smoke_wifi_1m.yaml` then `run plans/smoke_ble_1m.yaml` | ~1 min | 5 min |
| 2 | each of the 10 marks | `run plans/pathloss_wifi.yaml --distance D --position tape-Dm` then `run plans/pathloss_ble.yaml --distance D --position tape-Dm` | ~0.5 min/pos | 45 min |
| 3 | through-wall | same two plans, `--distance <measured> --position wall` | ~0.5 min | 5 min |
| 4 | 2 m | `run plans/txlin_wifi.yaml` then `run plans/txlin_ble.yaml` | ~6 min | 10 min |
| 5 | 4 m | `run plans/build_compare_wifi.yaml --build EVT1` | ~4 min | 5 min |
| 6 | 4 m | reflash DUT with `radioyield_dut_evt2.bin` (do not move it), `ports` to confirm `label=EVT2_TXTABLE_M2DB`, then `run plans/build_compare_wifi.yaml --build EVT2` | ~4 min | 10 min |
| 7 | 4 m | reflash DUT back to EVT1. Orientation "build": `run plans/build_compare_wifi.yaml --build ORIENT_A`, rotate the DUT 90 degrees about its vertical axis, `run plans/build_compare_wifi.yaml --build ORIENT_B --orientation "DUT rotated 90 deg (edge toward REF)"` | ~8 min | 14 min |
| 8 | weakest point (wall or 20 m) | link-margin check, expected PER 0 *(rehearsal)*: `run plans/waterfall_wifi.yaml --distance D --position wall` then `run plans/waterfall_ble.yaml ...` | ~4 min | 6 min |
| 9 | 2 m (optional) | `run plans/ieee154_smoke.yaml` | ~1 min | 3 min |
| 10 | 1 m | `run plans/smoke_wifi_1m.yaml` again (did anything drift?) | ~0.5 min | 3 min |
| 11 | (3+ boards) 2 m | swap in DUT-02: `run plans/txlin_wifi.yaml --unit DUT-02` | ~2 min | 8 min |

About 1 h 55 min of work plus setup *(rehearsal: up from 1 h 30)*. If time is short, drop step 11, then step 8, then the BLE half of step 2 at 1.5, 3 and 11 m.

Notes on the steps:
- **Step 2/3** feed the path-loss fit: one RSSI median per position per channel.
- **Step 4** feeds TX linearity. WiFi has only 11 real power levels; the plan uses exactly
  those. Low powers may lose packets at 2 m only if the hallway is bad; that is data.
- **Steps 5-7 are the two "builds"**. They are a firmware TX-table variant (EVT2 = every
  power 2 dB lower, a deliberate firmware change, reported by the DUT in `INFO` and in the
  files) and an antenna-orientation variant. They are **not** hardware builds; label them
  that way in any report.
- **Step 8** *(rehearsal)*: a hallway cannot reach the PER waterfall. ESP-NOW goes out at
  802.11b 1 Mbps (sensitivity -99.2 dBm at 8% PER), and the lowest WiFi power is 2 dBm, so the
  link needs about 100 dB of loss. Through one wall at 6 m the simulated hallway gives about
  58 dB: roughly 40 dB short for WiFi and 24 dB short for BLE (lowest power -15 dBm). Even
  ITU-R P.1238's office exponent (N = 30) needs over 100 m. In all 30 rehearsal sessions
  PER stayed 0% on every OTA waterfall step. Run the two plans anyway as a **link-margin
  check** (PER 0 at the lowest power means margin > ~40 dB). The waterfall itself comes from
  the conducted setup (section 9), or from the DUT inside a closed metal tin, which is
  uncontrolled and must be labelled that way.
- **Steps 5-7** *(rehearsal)*: with 5 repeats per build, the smallest possible Mann-Whitney p
  (0.008) times 9 tests under Holm is above 0.05, so even the deliberate -2 dB EVT2 change
  was flagged in only 3 of 30 sessions. With 10 repeats it is flagged in 30 of 30, and the
  null orientation change in 0 of 30.

## 6. During each run

The collector prints one line per step:
```
step 3/9 wifi ch6 pwr 34 qdBm (act 34): tx 500 rx 431 PER 13.80% RSSI med -88.0 dBm
```
Watch for `FLAGS`:
- `ref_overflow=N`: the REF could not print fast enough; that step's PER is invalid (not
  written to STDF). Re-run with a larger `interval_ms`.
- `host_bad_lines=N`: corrupted serial lines (cable?). PER invalid; re-run.
- `tx_fail=N`: the DUT driver refused packets; they are excluded from the denominator.
- `aborted`: TX was interrupted.
A run that dies midway still writes what it collected (`complete=false` in meta).
Ctrl-C stops the DUT and keeps partial data.

## 7. What to write down (logbook)

Once: date, board models and antenna types, which board is which (tape label <-> MAC),
firmware labels from `ports`, hallway description (width, floor, wall material for the
through-wall point), nearby AP channels, room temperature if you can.

Per run (the collector already records plan, distance, position, orientation, obstacles,
timestamps, firmware, MACs; write only what it cannot know):

| time | plan | position | anything unusual (people, door state, cable moved, retries) |
| --- | --- | --- | --- |

Then copy `out/rig/` somewhere safe (it is git-ignored).

## 8. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `ports` shows nothing | charge-only cable; try another cable/port. DevKitC-1: use the USB port, not UART (or build the UART variant) |
| `no RadioYield INFO reply` | not flashed, or a UART-bridge board on the default build: rebuild with `uart`, use `--baud 921600` |
| `role mismatch ... swap the ports` | `--dut` and `--ref` swapped |
| `ERR SET RADIO` for 154 | firmware built without 802.15.4 or the driver refused; skip step 9 and note it |
| BLE `ch ALL` PER high at 1 m | the per-packet advertising window is shorter than this controller's start-up delay: rebuild with a larger `CONFIG_RY_BLE_ADV_WINDOW_MS` (menuconfig, "RadioYield rig"), keep `interval_ms >= window + 5` |
| WiFi PER > 0 at 1 m on one channel only | busy channel (AP traffic); note it, compare channels |
| `no TXDONE within ...` | DUT reset or hung: re-run `ports`, re-run the plan |
| board resets when the collector opens the port | harmless; the handshake waits for it to boot |

## 9. Optional: conducted setup (M3)

Only with a u.FL connector on both boards, or at least on the DUT with the REF in a
shielded box far away. Shopping list (about $20-40):

- 2 x u.FL (IPEX MHF1) to SMA female pigtail, ~10-15 cm.
- SMA fixed attenuators, 2 W, DC-3 GHz or better: 30 + 30 + 30 + 20 dB (110 dB), plus a 10 dB
  to fine-tune *(rehearsal: 80 dB never reaches the WiFi waterfall)*. Rated attenuation at 2.4 GHz is within about +-1 dB; write down the part numbers.
- SMA male-male / female-female adapters as needed.
- Optional: two small metal tins as shields.

Wiring: `DUT u.FL -> pigtail -> attenuators in series -> pigtail -> REF u.FL`.

Choosing the attenuation: the received level is
`P_tx - sum(attenuators) - cable/adapter loss - leakage paths`. To walk the WiFi waterfall
the REF needs to see levels down into its sensitivity region (look the number up in the
ESP32-C6 datasheet for the rate actually used; do not assume). With the DUT's power range
(2..20 dBm) and 1 Mbps ESP-NOW, the rehearsal says 110 dB: the WiFi sweep then spans about
-109 to -91 dBm around the ~-99 dBm waterfall, and the BLE sweep (-15..20 dBm) spans -126 to
-91. With 80 dB no WiFi step lost a packet in any of 30 rehearsal sessions. 110 dB of chain
also means the boards must be isolated from each other by more than ~110 dB everywhere else,
which takes metal shielding, not distance alone; the leakage check below is not optional.

Leakage check (do it, or the result is not conducted): with the full chain connected, run
`smoke_wifi_1m.yaml` (edit it to CONDUCTED); then **disconnect the cable at the REF** and run
again. If the REF still receives packets, radiation from the boards bypasses the
attenuators: shield the boards (tins), separate them by several metres, and retry. Write
down the result of this check; it goes in the report.

Then: `run plans/conducted_wifi.yaml` after editing `attenuation_db` and `cable_loss_db` to
the parts used. data_origin will be `RIG_CONDUCTED`. A comparison with the datasheet RX
sensitivity is only meaningful for the same PHY rate and packet length and a stated PER
criterion; write those next to any number.
