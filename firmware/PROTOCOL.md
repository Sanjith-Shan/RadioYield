# RadioYield rig protocol (v1)

Two things are specified here: the **over-the-air payload** the DUT sends and the REF
decodes, and the **serial line protocol** the host (`radioyield.collector`) uses to drive
both boards. The C implementation is `components/ry_codec` (host-tested by
`host_tests/`); the Python mirror is `radioyield/collector/protocol.py`. Both are checked
against `host_tests/golden_rx_lines.txt`.

Target: ESP32-C6, ESP-IDF v5.5.x. One source tree, two images: **DUT** (transmits) and
**REF** (receives, prints one line per packet). The role is fixed at build time
(`sdkconfig.defaults.dut` / `.ref`) and reported by `INFO`.

## 1. Over-the-air payload (18 bytes, little-endian)

| off | len | field | notes |
| --- | --- | --- | --- |
| 0 | 2 | magic | ASCII `R` `Y` |
| 2 | 1 | version | `1` |
| 3 | 1 | radio | 1 = WiFi (ESP-NOW), 2 = BLE, 3 = 802.15.4 |
| 4 | 1 | channel | channel the DUT was commanded to. BLE: 37/38/39, 0 = all three |
| 5 | 1 | flags | bit 0 = padded; others 0 |
| 6 | 2 | run_id | host-chosen per sweep step; stale packets from an earlier step are discarded by run_id |
| 8 | 4 | seq | 0 .. n-1 within one run_id |
| 12 | 2 | pwr_qdbm | int16, TX power **commanded by the host**, 0.25 dB units |
| 14 | 2 | dut_tag | low 16 bits of the DUT's WiFi STA MAC |
| 16 | 2 | crc16 | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over bytes 0..15 |
| 18 | 0..232 | padding | byte *i* = `(seq + i) & 0xFF`; only for `len` > 18 (WiFi, 802.15.4) |

Carriers:

- **WiFi**: ESP-NOW broadcast to `FF:FF:FF:FF:FF:FF` from the STA interface. Broadcast
  ESP-NOW frames are not ACKed and not retried, so a frame the REF misses is a real loss.
  Payload length `len` (18..250). Rate: the ESP-NOW default (1 Mbps per Espressif docs; not
  changed by this firmware, not independently verified on air).
- **BLE**: legacy `ADV_NONCONN_IND`, advertising data = one manufacturer-specific AD
  structure `[0x15][0xFF][0xFF 0xFF][payload]` (22 bytes; company ID 0xFFFF is the one the
  Bluetooth SIG reserves for testing). One test packet = **one advertising event** on the
  commanded channel map (see section 4).
- **802.15.4**: data frame, FCF `0x8841` (data, PAN ID compression, 2003, short dst/src),
  DSN = seq & 0xFF, PAN = `CONFIG_RY_154_PAN_ID` (default 0x5259), dst 0xFFFF, src =
  dut_tag, no ACK request, no CCA. MAC payload = RY payload (`len` 18..116).

## 2. Serial transport

- USB-Serial-JTAG (C6 native USB) by default, any baud setting works. Boards that only
  have a USB-UART bridge: build with `sdkconfig.defaults.uart` (921600 baud).
- ASCII, one message per line, `\n` terminated (the device sends `\n`; the host may send
  `\r\n`). Max line 160 bytes.
- Host -> device: commands (section 3). One command at a time; wait for the `OK`/`ERR`.
- Device -> host: responses, asynchronous events, and RX data lines. **The host must
  ignore any line that does not start with `OK `, `ERR `, `RX,`, `TXDONE `, `BOOT `**
  (ESP-IDF boot/log output may appear; the firmware sets log level WARN).

### Command grammar

```
line   = verb [SP arg] *(SP key "=" value) [CR] LF
verb   = 1*11 ( %x41-5A )                    ; A-Z
arg    = 1*15 ( a-z / 0-9 / "_" )           ; only before the first key=value
key    = 1*15 ( a-z / 0-9 / "_" )
value  = 1*31 ( A-Z / a-z / 0-9 / "_" / "." / ":" / "+" / "-" )
```

Tabs or spaces separate tokens; duplicate keys, empty values, quotes, more than 12 pairs
or lines over 160 bytes are rejected with `ERR ? <CODE>` (`SYNTAX`, `TOOLONG`, `DUP`).
Integers are strict decimal (optional sign, no `0x`, no spaces).

### Responses

```
OK <VERB> [key=value ...]
ERR <VERB> <CODE> [free text]
```

`<VERB>` is `?` when the line could not be parsed. Codes: `SYNTAX`, `TOOLONG`, `DUP`,
`MISSING`, `RANGE`, `UNKNOWN`, `STATE`, `BUSY`, `ROLE`, `RADIO` (radio not compiled in),
`TIMEOUT`, `DRIVER` (an ESP-IDF call failed; the esp_err name follows).

## 3. Commands

| Command | Role | Reply |
| --- | --- | --- |
| `INFO` | both | `OK INFO role=DUT fw=0.1.0 proto=1 idf=v5.5.6 chip=esp32c6 mac=aa:bb:cc:dd:ee:ff tag=eeff radios=wifi,ble,154 pwr_offset_qdbm=0 label=EVT1_TXTABLE_NOMINAL ble_window_ms=30 ble_itvl_ms=100` |
| `PING` | both | `OK PING t_us=<device uptime us>` |
| `SET radio=<wifi\|ble\|154> ch=<n> [pwr_qdbm=<n>] [len=<n>] [scan_ms=<n>]` | both | `OK SET radio=wifi ch=6 pwr_qdbm=40 pwr_act_qdbm=40 len=32 scan_ms=1000` |
| `TX n=<count> interval_ms=<ms> run=<0..65535>` | DUT | `OK TX run=7 n=500 interval_ms=5`, later the `TXDONE` event |
| `STOP` | both | `OK STOP` (aborts a running TX; it then emits `TXDONE ... aborted=1`) |
| `RX start` | REF | `OK RX start radio=wifi ch=6`, then `RX,` lines |
| `RX stop` | REF | `OK RX stop ok=<n> bad=<n> other=<n> ovf=<n>` |

`SET` ranges:

| radio | ch | pwr_qdbm | len |
| --- | --- | --- | --- |
| wifi | 1..13 | 8..84 (2..21 dBm; default 80) | 18..250 (default 18) |
| ble | 37, 38, 39, `all` | -60..80 (nearest of -15..+20 dBm in 3 dB steps, top step +20) | 18 only |
| 154 | 11..26 | -60..80 (driver takes whole dBm: pwr_qdbm/4) | 18..116 |

- `pwr_qdbm` is what the host commands and what goes into every packet. The DUT adds its
  build-time `CONFIG_RY_TXPWR_OFFSET_QDBM` (the EVT2 "TX table" variant), clamps, applies,
  and reports what the driver read back as `pwr_act_qdbm`. WiFi quantizes: per
  `esp_wifi.h`, set values map to {8, 20, 28, 34, 44, 52, 56, 60, 66, 72, 80} quarter-dB.
- `SET` switches radios: the previous radio is fully stopped (WiFi stopped, BLE
  advertising/scanning stopped, 802.15.4 disabled) so only one radio uses the shared front
  end during a step. `SET` always leaves the REF's RX stopped. `SET` on a busy DUT is `BUSY`.
- `scan_ms` (REF, BLE only): scan interval = scan window (continuous scan), 3..10240 ms.
- `TX` (DUT): seq runs 0..n-1, paced with `vTaskDelayUntil` at `interval_ms` (1 ms tick).
  Each packet waits for the driver's send-complete callback, so `interval_ms` is a minimum
  period. BLE requires `interval_ms >= ble_window_ms + 5`.

### Events

```
BOOT role=REF fw=0.1.0 proto=1
TXDONE run=7 n=500 sent=500 ok=500 fail=0 aborted=0 dur_ms=2512 radio=wifi ch=6 pwr_qdbm=40 pwr_act_qdbm=40 len=32 err=none
```

`ok` = packets the driver reported as sent; `fail` = packets the driver refused or reported
failed (these never reached the air, or might not have). The collector uses `n - fail`
as the on-air count and flags any step with `fail > 0`.

### RX data line (REF)

```
RX,<radio>,<rx_ch>,<tx_ch>,<run_id>,<seq>,<rssi>,<pwr_qdbm>,<t_us>,<lqi>,<tag>*<hh>
RX,wifi,6,6,7,123,-45,40,1234567,-1,beef*1C
```

| field | meaning |
| --- | --- |
| radio | `wifi`, `ble`, `154` |
| rx_ch | channel the REF's stack reported for this reception. WiFi: `rx_ctrl.channel`; 802.15.4: frame_info.channel; **BLE: always 0 = not reported by the stack** |
| tx_ch | channel from the payload (BLE: 37/38/39 or 0 = all) |
| run_id, seq, pwr_qdbm | from the payload |
| rssi | dBm as reported by the REF's stack (int8; BLE 127 = unavailable) |
| t_us | REF `esp_timer_get_time()` when the packet reached the firmware callback |
| lqi | 802.15.4 LQI, -1 for WiFi/BLE |
| tag | DUT tag, 4 lowercase hex digits |
| hh | 2 hex digits, XOR of every byte before `*` (NMEA style); lines that fail are dropped and counted by the host |

The REF prints every valid RadioYield packet of the selected radio, whatever its run_id or
tag; the host filters. Counters in `OK RX stop`: `ok` lines printed, `bad` frames that
looked like ours but failed CRC/version checks, `other` foreign frames, `ovf` packets
dropped because the REF's 128-entry output queue was full. **`ovf > 0` means REF-side loss;
the collector marks that step's PER invalid.**

## 4. What the numbers mean (and do not)

- **PER** is computed on the host from sequence numbers: on-air count = `n - fail`,
  received = unique seq in [0, n) for the step's run_id and DUT tag; duplicates and
  reorders are counted, not double-counted. A lost packet is counted, never guessed.
- **RSSI** is the receiver's own estimate (integer dBm, uncalibrated, chip-to-chip spread of
  a few dB). It is a relative measure unless the conducted setup calibrates it.
- **BLE channels**: NimBLE's advertising report does not carry the channel the PDU arrived
  on, and standard HCI has no "scan only channel 38" control. So:
  - `ch=all`: each event is sent on 37, 38 and 39 back to back; the REF, parked on one
    channel at a time, catches at most one copy. PER is meaningful (event received or not);
    the receive channel is unknown.
  - `ch=37|38|39`: every received packet is known to have been on that channel (RSSI per
    channel is honest), but the REF only hears it while its scan happens to be on that
    channel, so the received fraction measures the scan schedule, not the link. The
    collector reports `RX_FRAC` instead of `PER` for these steps.
- **802.15.4**: behind `CONFIG_RY_ENABLE_IEEE802154` (default on). It uses the raw driver
  with callbacks registered via `esp_ieee802154_event_callback_list_register`; it has not
  been run on hardware yet.
