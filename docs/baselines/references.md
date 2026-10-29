# Reference values used by the analysis and the simulator

Every value below was read from the primary source PDF on 2026-10-31. Where a source does not state a value, it says **not stated**: we do not fill those gaps from memory.

## ESP32-C6 Series Datasheet v1.5 (revision history dated 2026-03-31)

Source: <https://www.espressif.com/sites/default/files/documentation/esp32-c6_datasheet_en.pdf>, Section 6 "RF Characteristics".

Measurement conditions: "The RF data is measured at the antenna port, where RF cable is connected, including the front-end loss… 3.3 V (±5%) supply at 25 ºC ambient temperature." In other words, these are **conducted** figures. They cannot be compared with over-the-air numbers.

### Wi-Fi

Operating channels cover 2412 to 2484 MHz (Table 6-1). TX power is from Table 6-2 ("TX Power with Spectral Mask and EVM Meeting 802.11 Standards"); RX sensitivity is from Table 6-4.

| Rate | TX power, typ (dBm) | RX sensitivity, typ (dBm) |
|---|---|---|
| 11b 1 Mbps | 21.0 | −99.2 |
| 11b 11 Mbps | 21.0 | −90.0 |
| 11g 6 Mbps | 20.5 | −94.0 |
| 11g 54 Mbps | 19.5 | −77.6 |
| 11n HT20 MCS0 | 19.5 | −93.6 |
| 11n HT20 MCS7 | 18.5 | −75.6 |
| 11ax HE20 MCS0 | 19.5 | −93.8 |
| 11ax HE20 MCS9 | 16.5 | −69.0 |

- **Min and max values:** "—" for every rate. The datasheet gives typical values only.
- **TX power tolerance:** not stated.
- **PER criterion for sensitivity:** 8% for 802.11b and 10% for g/n/ax.
- **EVM (Table 6-3):** HT20 MCS7 is typ −30.0 dB against a limit of −27.0 dB.

### Bluetooth LE

- **Operating channels:** 2402 to 2480 MHz.
- **TX power range:** −15.0 to 20.0 dBm (Table 6-7).
- **Power step:** not stated.
- **Sensitivity @30.8% PER:**
  - 1M PHY: −98.5 dBm (Table 6-12)
  - 2M PHY: −95.5 dBm (Table 6-13)
  - Coded 125k: −106.0 dBm (Table 6-14)
  - Coded 500k: −102.0 dBm (Table 6-15)

### IEEE 802.15.4

- **Channels:** 2405 to 2480 MHz, channels 11 to 26.
- **TX power:** −15.0 to 20.0 dBm (Table 6-17).
- **Sensitivity @1% PER:** −104.0 dBm (Table 6-18).

### RSSI

Accuracy and resolution are **not stated**. The datasheet says only "RSSI and LQI supported" (Section 4.3.4.1).

## ITU-R P.1238, indoor propagation

The revision in force is **P.1238-13 (09/2025)**. It has **no** 2.4 GHz power-loss coefficient N. The classic N table was last published in **P.1238-10 (08/2019)**. `radioyield.analysis.rf` compares a measured exponent against both revisions and says which revision each value comes from.

### P.1238-10 (2019), Table 2: power loss coefficient N = 10·n

| Frequency | Residential | Office | Commercial |
|---|---|---|---|
| 1.9 GHz | 28 | 30 | 22 |
| 2.4 GHz | 28 | 30 | – |

- **Shadow-fading σ (Table 4, 1.8 to 2 GHz):** residential 8 dB, office 10 dB, commercial 10 dB. **No 2.4 GHz row exists.**
- **Text:** line-of-sight paths have "a distance power loss coefficient of around 20", and through-wall paths can reach "about 40".

### P.1238-13 (2025), §3.1 site-general model

The model is `L = 10·α·log10(d) + β + 10·γ·log10(f[GHz])`, plus N(0, σ). α plays the role of n.

| Environment | α | σ (dB) |
|---|---|---|
| Office LoS | 1.47 | 3.68 |
| Office NLoS | 2.39 | 5.01 |
| Corridor LoS | 1.57 | 3.77 |
| Corridor NLoS | 2.78 | 7.58 |
| Industrial LoS | 2.27 | 2.62 |
| Industrial NLoS | 2.80 | 5.70 |
| Conference LoS | 1.56 | 2.92 |
| Conference NLoS | 1.40 | 3.33 |

Free space is n = 2 (Friis).

## AEC-Q001 Rev D (December 9, 2011), Guidelines for Part Average Testing

Source: <http://www.aecouncil.com/Documents/AEC_Q001_Rev_D.pdf>. The AEC documents page lists Rev D as current.

**§2.4 robust statistics**
- "Robust Mean = Q2" (the median).
- "Robust Sigma = (Q3 - Q1) / 1.35".
- Note 2: "The 1.35 number is inexact for sample sizes less than 20."

**§3.1.1 static PAT**
- Limits = Robust Mean ± 6 Robust Sigma.
- The data come from at least six lots, with at least 30 parts per lot.
- For non-normal data, "the supplier shall use other suitable means".

**§3.1.2 dynamic PAT**
- Limits = Robust Mean ± 6 Robust Sigma, computed from the current lot's parts that passed the static limits.

**§3.1 relation to spec limits**
- "PAT test limits shall not exceed the device specification limits."
- No explicit clamping formula is given. Clamping each PAT limit to the spec limit is **our reading** of that sentence, and the code says so.

## Standards cited for limits but not re-verified in this repo

- **IEEE 802.11 OFDM transmit centre-frequency tolerance, ±20 ppm.** Used as the simulator's Wi-Fi frequency-error limit.
- **Bluetooth LE initial carrier frequency tolerance, ±150 kHz.** Used as the simulator's BLE frequency-offset limit.

Both affect simulated data only. Each is flagged in `radioyield/sim/params.py`.
