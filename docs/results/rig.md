# Rig results (M2): NOT YET MEASURED

Every value below is a placeholder. Nothing here has been measured; the rig session
(`docs/RIG.md`) has not happened yet. When a number is filled in, add the command that
produced it, the run stem(s) in `out/rig/`, and a NUMBERS_LEDGER row. Label each one OTA
or CONDUCTED. Do not quote anything from `out/sim/` or the collector's test double here:
that data is SIMULATED.

The session has been rehearsed in simulation ([sim_rig.md](sim_rig.md)): every plan below is
known to produce fittable data *if* the hallway behaves like the model. The OTA PER waterfall
rows are expected to stay unfitted (a hallway is ~40 dB short of the Wi-Fi waterfall at the
lowest TX power), so the waterfall rows now depend on the conducted kit.

## Session facts

| Item | Value |
| --- | --- |
| Date | NOT YET MEASURED |
| Boards (model, antenna, count) | NOT YET RECORDED (open hardware question, RIG.md section 0) |
| DUT / REF MACs, firmware labels | NOT YET RECORDED |
| Environment (hallway, wall type, AP channels) | NOT YET RECORDED |
| Runs / steps / packets logged | NOT YET MEASURED |

## Numbers the rig will produce

| Claim | Radio / channel | Value | CI / spread | Compared against | Origin | Run stem(s) |
| --- | --- | --- | --- | --- | --- | --- |
| Path-loss exponent n | WiFi ch 1/6/11 | NOT YET MEASURED | | ITU-R P.1238 indoor values | OTA | |
| Shadowing sigma | WiFi | NOT YET MEASURED | | ITU-R P.1238 | OTA | |
| Path-loss exponent n | BLE (37/38/39) | NOT YET MEASURED | | ITU-R P.1238 | OTA | |
| RSSI at 1 m (intercept A) | WiFi / BLE | NOT YET MEASURED | | (setup-specific, no reference) | OTA | |
| Through-wall extra loss | WiFi / BLE | NOT YET MEASURED | | | OTA | |
| TX power step linearity: slope (dB/dB) | WiFi ch 6, 11 driver levels | NOT YET MEASURED | | commanded steps (ideal 1.0) | OTA | |
| TX power step linearity: max residual | WiFi | NOT YET MEASURED | | commanded steps | OTA | |
| TX power step linearity: slope | BLE, 13 levels | NOT YET MEASURED | | commanded steps | OTA | |
| RSSI level at 10% PER | WiFi ch 6 | NOT YET MEASURED | | none (OTA RSSI, not a sensitivity) | OTA | |
| RSSI level at 10% PER | BLE (all channels) | NOT YET MEASURED | | none (OTA) | OTA | |
| Input level at 10% PER | WiFi ch 6 | NOT YET MEASURED | | ESP32-C6 datasheet RX sensitivity, same rate only | CONDUCTED (optional, M3) | |
| Build-over-build: EVT2 vs EVT1 RSSI shift | WiFi ch 1/6/11 | NOT YET MEASURED | | the deliberate -2 dB firmware table change | OTA | |
| Build-over-build: ORIENT_B vs ORIENT_A | WiFi ch 1/6/11 | NOT YET MEASURED | | no detectable change vs a shift | OTA | |
| BLE REF scan behaviour (RX_FRAC per single channel) | BLE 37/38/39 | NOT YET MEASURED | | (characterises the REF, not the link) | OTA | |
| 802.15.4 PER / RSSI | ch 11-26 subset | NOT YET MEASURED (path not yet run on hardware) | | | OTA | |
| Drift: 1 m smoke start vs end | WiFi ch 6 | NOT YET MEASURED | | | OTA | |
| Unit-to-unit spread (if 3+ boards) | WiFi ch 6 | NOT YET MEASURED | | | OTA | |

## Not measured by this rig (by design)

EVM, spectral mask / ACLR, absolute TX power, frequency error, TRP / TIS, blocking /
desense. These need an RF tester, power meter or anechoic chamber.
