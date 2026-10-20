# radioyield.collector

Drives an ESP32-C6 **DUT** (transmitter) and **REF** (receiver) over USB serial through a
YAML sweep plan, computes PER and RSSI per step, and writes one STDF V4 file per run plus
the raw per-packet log as Parquet. Firmware and the line protocol: `firmware/PROTOCOL.md`.
The session runbook: `docs/RIG.md`.

```
python -m radioyield.collector ports                          # which port is DUT / REF
python -m radioyield.collector validate plans/*.yaml          # check plans, time estimate
python -m radioyield.collector run plans/smoke_wifi_1m.yaml \
    --dut /dev/cu.usbmodem1101 --ref /dev/cu.usbmodem1201 --out out/rig/
python -m radioyield.collector run plans/pathloss_wifi.yaml --dut ... --ref ... \
    --distance 8 --position tape-8m --out out/rig/            # per-position overrides
python -m radioyield.collector run plans/smoke_wifi_1m.yaml --fake --out out/sim/   # no boards
python -m radioyield.collector stdf out/rig/<stem>.measurements.parquet            # re-export STDF
```

Exit codes: 0 ok, 1 board/serial error (partial data is still written), 2 plan error,
3 run complete but the STDF writer failed (data safe in Parquet; use `stdf` to re-export),
130 interrupted (partial data written).

Requires `pyserial` (`pip install -e '.[rig]'`) for real boards; `--fake` needs nothing extra.

## Modules

| module | role |
| --- | --- |
| `protocol.py` | RX line / reply / event parsing and command formatting (mirror of `firmware/components/ry_codec`) |
| `plan.py` | YAML plan schema, validation (all errors at once, with paths), step expansion |
| `transport.py` | `Board`: serial port + reader thread sorting replies, events and RX lines; transcript |
| `stats.py` | per-step PER from sequence numbers, RSSI statistics |
| `runner.py` | the sweep: SET / RX start / TX / TXDONE / RX stop per step; writes files |
| `outputs.py` | Parquet schemas, STDF test naming/numbering, MEASUREMENT_SCHEMA rows |
| `fake.py` | **serial test double** (fake DUT + REF + toy channel). Output is always `SIMULATED` |

## Plan schema

```yaml
plan: txlin_wifi                 # name (used in file names and STDF JOB_NAM)
description: free text           # optional
build: EVT1                      # build label (EVT1, EVT2, ORIENT_A ...). Overridable: --build
build_note: what the build is    # optional, e.g. "firmware TX table -2 dB, same board"
station: HALL-A                  # test station id (STDF MIR.NODE_NAM)
lot_id: RIG-2026-11-02           # optional; default RIG-<UTC date>
unit_serial: DUT-01              # DUT serial (STDF PRR.PART_ID). Overridable: --unit
ref_serial: REF-01               # optional, recorded
radio: wifi                      # wifi | ble | "154"
channels: [1, 6, 11]             # wifi 1..13, ble 37/38/39/all, 154 11..26
tx_power_qdbm: [80, 44]          # 0.25 dB units, or tx_power_dbm: [20, 11]
packets_per_step: 300
interval_ms: 5                   # BLE: >= ble_window_ms + 5 (35 by default)
payload_len: 32                  # wifi 18..250, 154 18..116, ble fixed 18
repeats: 1                       # whole sweep repeated; each repeat is one STDF part
scan_ms: 1000                    # REF BLE scan interval = window
settle_ms: 300                   # pause between TXDONE and RX stop
setup:
  mode: OTA                      # OTA | CONDUCTED -> data_origin RIG_OTA | RIG_CONDUCTED
  distance_m: 2.0                # required for OTA. Overridable: --distance
  orientation: "..."             # required; write it the way you would want to read it later
  obstacles: none                # required
  height_m: 0.75                 # optional
  attenuation_db: [30, 30, 30, 20]   # required for CONDUCTED (list of fixed attenuators)
  cable_loss_db: 1.0             # optional
  environment: indoor hallway    # optional
  position_label: tape-4m        # optional. Overridable: --position
  notes: free text               # optional
limits:                          # optional, per metric: lo and/or hi
  PER: {hi: 10}
  RSSI_MEDIAN: {lo: -80}
```

Steps are expanded as `for repeat: for channel: for power`. Validation also warns when a
WiFi power will be quantized by the driver (`esp_wifi_set_max_tx_power` has 11 real levels)
or a BLE power snaps to the nearest C6 level.

## Per-step metrics and STDF mapping

| metric | units | when |
| --- | --- | --- |
| `PER` | % | step valid, and not a single-channel BLE step |
| `RX_FRAC` | % | single-channel BLE steps (received fraction is limited by the REF's scan schedule, so it is not PER; see PROTOCOL.md section 4) |
| `RSSI_MEAN`, `RSSI_MEDIAN` | dBm | at least one packet received |
| `RSSI_STDEV` | dB | at least two packets received |
| `N_RX`, `N_TX` | count | always (`N_TX` = packets on air = sent - driver failures) |

- Test name: `<RADIO>_CH<ch>_P<pwr_qdbm>_<METRIC>`, e.g. `WIFI_CH6_P40_RSSI_MEDIAN`,
  `BLE_CHALL_PN12_PER` (negative power = `PN`), `IEEE154_CH15_P0_PER`. The power in the name
  is the **commanded** power; the applied one is in the steps Parquet.
- Test number: `radio*1e6 + channel*1e4 + (pwr_qdbm+200)*10 + metric_index` (wifi 1, ble 2,
  154 3; BLE "all" = channel 0), so the same test has the same number in every run and build.
- Limits come from the plan's `limits`; no limit = always pass. One STDF part per repeat,
  `part_id` = unit serial, site 1, hard/soft bin 1 = all tests pass, 2 = any fail.
- A step whose PER is invalid (REF queue overflow, corrupted serial lines, aborted TX) gets
  **no** PER/RX_FRAC test in STDF; the steps Parquet keeps the numbers and the reason.
- STDF `meta` (each becomes an `ry.<key>=<value>` DTR): `lot_id`, `station`, `build`,
  `data_origin` and `run_uid, plan, plan_file, plan_sha256, unit_serial, ref_serial, radio,
  mode, distance_m, height_m, orientation, obstacles, attenuation_db, attenuation_total_db,
  cable_loss_db, environment, position_label, notes, build_note, packets_per_step,
  interval_ms, payload_len, repeats, dut_fw, dut_idf, dut_mac, dut_label,
  dut_pwr_offset_qdbm, ref_fw, ref_idf, ref_mac, ref_label, collector_version, host,
  rssi_note, not_measured, start_utc, end_utc, complete, n_steps_done, n_packets_logged`
  (and `ble_rx_channel`, `ble_window_ms` for BLE, `simulated_note` for the double). MIR gets
  `JOB_NAM` = plan, `EXEC_TYP/EXEC_VER` = collector, `TSTR_TYP`, `PART_TYP`, `SERL_NUM`.
- **data_origin**: `RIG_OTA` or `RIG_CONDUCTED` from `setup.mode` for real boards;
  **always `SIMULATED`** when either board is the test double (enforced in `runner.py`,
  independent of the plan).

## Files per run

`<stem>` = `<plan>__<build>__<unit>__<UTC yyyymmddThhmmssZ>`.

| file | content |
| --- | --- |
| `<stem>.stdf` | STDF V4 (via `radioyield.stdf.write`) |
| `<stem>.measurements.parquet` | the same table in `radioyield.schema.MEASUREMENT_SCHEMA` |
| `<stem>.packets.parquet` | raw per-packet log, schema below |
| `<stem>.steps.parquet` | per-step summary, schema below |
| `<stem>.json` | meta, the plan as loaded, warnings, raw INFO replies |
| `<stem>.serial.log` | every serial line, both boards, both directions, with host timestamps |

Every Parquet file carries the run `meta` as Arrow schema metadata as well.

### Raw packet log (`PACKET_SCHEMA`), one row per accepted RX line

Rows are the REF's lines for this step's `run_id` and the DUT's tag, in arrival order,
**including duplicates** (flagged `dup`). Lost packets have no row: PER comes from the
steps file (or from `seq` gaps against `n_onair`).

| column | type | meaning |
| --- | --- | --- |
| run_uid | string | = stem; joins with the steps file |
| data_origin | string | RIG_OTA / RIG_CONDUCTED / SIMULATED |
| lot_id, station, build, unit_serial | string | as in meta |
| mode | string | OTA / CONDUCTED |
| distance_m | float64 | null if not given |
| attenuation_total_db, cable_loss_db | float64 | null for OTA |
| orientation, obstacles, position_label | string | setup facts |
| step_idx | int32 | step index in the run |
| repeat | int16 | repeat index |
| radio | string | wifi / ble / 154 |
| channel | int16 | commanded channel; BLE 0 = all three |
| channel_label | string | "6", "37", "ALL" |
| pwr_cmd_qdbm | int16 | host-commanded TX power (0.25 dB) |
| pwr_act_qdbm | int16 | DUT driver read-back incl. build offset |
| run_id | uint16 | over-the-air run id of the step |
| seq | uint32 | packet sequence number |
| rssi_dbm | int16 | REF RSSI, null if the stack had none (BLE 127) |
| rx_channel | int16 | REF-reported channel; **null for BLE** (not reported) |
| lqi | int16 | 802.15.4 LQI, null otherwise |
| ref_t_us | uint64 | REF `esp_timer_get_time()` at reception |
| host_t | float64 | host unix time the line was read |
| dup | bool | a repeat of a seq already logged in this step |
| dut_tag | uint16 | DUT MAC low 16 bits |

### Steps summary (`STEP_SCHEMA`), one row per step

Setup columns and step key columns as above, plus: `run_id, payload_len, interval_ms,
n_cmd, n_sent, n_tx_fail, n_onair, n_rx_unique, n_lost, n_dup, n_reorder, n_out_of_range,
per_pct, per_meaningful, per_valid, rssi_mean, rssi_median, rssi_stdev, rssi_min,
rssi_max, ref_ok, ref_bad, ref_other, ref_ovf, host_bad_lines, flags, t_start, t_end`.

For the RF fits in `radioyield.analysis.rf`:
- `per_waterfall(level_dbm=rssi_median, n_sent=n_onair, n_lost=n_lost)` on valid,
  meaningful steps (OTA: the level is the receiver's RSSI of packets that got through, so
  it is biased at the bottom of the waterfall; CONDUCTED: use
  `pwr_act_qdbm/4 - attenuation_total_db - cable_loss_db` instead).
- `path_loss_fit(distance_m, rssi_median)` across the path-loss runs (one per position).
- `tx_linearity(pwr_act_qdbm/4 or pwr_cmd_qdbm/4, rssi_median)` from a TX sweep at one position.

## The test double (`fake.py`)

`FakeAir` connects fake DUT and REF "serial ports" that implement the firmware's command
set. Packets go through a toy channel: log-distance path loss (exponent 2.2, 40 dB at 1 m),
2 dB shadowing per step, 1 dB per packet, a logistic PER waterfall around made-up
sensitivities. **These parameters are arbitrary test values, not datasheet or measured
numbers.** Optional faults: duplicated, reordered, corrupted and noise lines, driver TX
failures, REF queue overflow. It exists to test the protocol handling and the PER logic
in CI; its numbers mean nothing about any radio.
