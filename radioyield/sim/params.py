"""Test definitions for the synthetic production lot, each with its source.

SIMULATED. These parameters describe a plausible conducted factory test of an
ESP32-C6-class 2.4 GHz module. Nominal values come from the ESP32-C6 datasheet
v1.5 (Section 6, RF Characteristics) where it gives one. Spreads and most
limits are **assumptions** (the datasheet states typical values only: no
min/max, no TX power tolerance), marked ``assumed`` below. Once the rig has run,
unit-to-unit spreads should be re-seeded from its measurements and the source
column updated.

Spreads are split into three components, because the detectors care which:
  unit_sd   unit-to-unit (product) variation, part of what Cpk sees
  meas_sd   repeatability of one reading on one station (what GR&R sees)
  pa_load   loading on a shared per-unit "PA strength" factor (sd 0.15 dB) so
            TX power tests are correlated within a unit, as they are on real parts
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestDef:
    num: int
    name: str
    units: str
    nominal: float
    unit_sd: float
    meas_sd: float
    lo: float | None
    hi: float | None
    soft_bin: int
    kind: str            # "txp" (TX power, dBm), "freq", "evm", "rssi_err", "per", "ota"
    source: str
    pa_load: float = 0.0
    quantum: float | None = None   # reading resolution, e.g. 1 dB integer RSSI


DS = "ESP32-C6 datasheet v1.5"

TESTS: tuple[TestDef, ...] = (
    TestDef(1001, "WIFI_TXP_HT20MCS0_CH1", "dBm", 19.5, 0.20, 0.10, 16.5, 22.5, 10, "txp",
            f"nominal: {DS} Table 6-2 (HT20 MCS0 typ 19.5 dBm); limits +/-3 dB assumed", pa_load=1.0),
    TestDef(1002, "WIFI_TXP_HT20MCS0_CH6", "dBm", 19.5, 0.20, 0.10, 16.5, 22.5, 10, "txp",
            f"nominal: {DS} Table 6-2; limits assumed", pa_load=1.0),
    TestDef(1003, "WIFI_TXP_HT20MCS0_CH11", "dBm", 19.3, 0.20, 0.10, 16.5, 22.5, 10, "txp",
            f"nominal: {DS} Table 6-2, -0.2 dB band-edge rolloff assumed; limits assumed", pa_load=1.0),
    TestDef(1010, "WIFI_FREQ_ERR_CH6", "ppm", 0.0, 3.0, 0.3, -20.0, 20.0, 11, "freq",
            "limits: IEEE 802.11 OFDM transmit centre-frequency tolerance +/-20 ppm (not re-verified here); spread assumed"),
    TestDef(1020, "WIFI_EVM_HT20MCS7_CH6", "dB", -30.0, 0.6, 0.3, None, -27.0, 12, "evm",
            f"typ -30.0 and limit -27.0 dB: {DS} Table 6-3; spread assumed"),
    TestDef(1030, "WIFI_RSSI_ERR_CH6", "dB", 0.0, 0.6, 0.5, -3.0, 3.0, 13, "rssi_err",
            "RSSI readback minus tester level; integer-dBm RSSI, so quantized to 1 dB; all values assumed",
            quantum=1.0),
    TestDef(1040, "WIFI_PER_HT20MCS7_M74P5", "%", 0.0, 0.0, 0.0, None, 10.0, 14, "per",
            f"PER over 1000 packets at -74.5 dBm; 10% criterion and -75.6 dBm typ sensitivity: {DS} Table 6-4; "
            "unit sensitivity sd 0.6 dB and logistic slope 0.5 dB assumed"),
    TestDef(2001, "BLE_TXP_CH37", "dBm", 9.0, 0.20, 0.10, 6.0, 12.0, 20, "txp",
            f"commanded 9 dBm (inside {DS} Table 6-7 range -15..20 dBm); limits assumed", pa_load=1.0),
    TestDef(2002, "BLE_TXP_CH38", "dBm", 9.0, 0.20, 0.10, 6.0, 12.0, 20, "txp", "as CH37", pa_load=1.0),
    TestDef(2003, "BLE_TXP_CH39", "dBm", 8.9, 0.20, 0.10, 6.0, 12.0, 20, "txp", "as CH37", pa_load=1.0),
    TestDef(2010, "BLE_FREQ_OFFSET_CH38", "kHz", 0.0, 15.0, 2.0, -150.0, 150.0, 21, "freq",
            "limits: Bluetooth LE initial carrier frequency tolerance +/-150 kHz (not re-verified here); spread assumed"),
    TestDef(3001, "OTA_COUPLER_RSSI", "dBm", -40.0, 0.50, 0.30, -46.0, -34.0, 30, "ota",
            "radiated antenna check in a shielded coupler; all values assumed"),
    TestDef(4001, "IEEE154_TXP_CH15", "dBm", 10.0, 0.20, 0.10, 7.0, 13.0, 40, "txp",
            f"commanded 10 dBm (inside {DS} Table 6-17 range -15..20 dBm); limits assumed", pa_load=1.0),
)

PA_SD = 0.15
STATION_SD = {"dBm": 0.04, "dB": 0.04, "ppm": 0.1, "kHz": 0.5, "%": 0.0}   # clean-line station spread, assumed
PER_LEVEL_DBM = -74.5
PER_SENS50_DBM = -75.6 - 0.5 * 2.197   # level of 50% PER if typ sensitivity (-75.6) is the 10% point, slope 0.5 dB
PER_SLOPE_DB = 0.5
PER_SENS_UNIT_SD = 0.6
PER_PACKETS = 1000

TXP_TESTS = tuple(t.name for t in TESTS if t.kind == "txp")
WIFI_TXP_TESTS = tuple(t.name for t in TESTS if t.kind == "txp" and t.name.startswith("WIFI"))
BY_NAME = {t.name: t for t in TESTS}


def total_sd(t: TestDef) -> float:
    return float((t.unit_sd ** 2 + t.meas_sd ** 2 + (t.pa_load * PA_SD) ** 2) ** 0.5)
