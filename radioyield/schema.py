"""The one data contract every component shares.

A *measurement table* is a flat Arrow/pandas table, one row per (part, test)
parametric result. The STDF reader produces it, the STDF writer consumes it, the
simulator and the collector emit it, and every analysis function takes it.

STDF mapping (see stdf/README.md for the full record-level description):

    lot_id       MIR.LOT_ID
    station      MIR.NODE_NAM               (tester node = test station)
    build        DTR "ry.build=<label>"     (file-level, before first PIR)
    data_origin  DTR "ry.data_origin=..."   one of DATA_ORIGINS
    site_num     PIR/PRR.SITE_NUM
    part_id      PRR.PART_ID                (unit serial)
    part_ts      DTR "ry.part_ts=<unix s>"  (between PIR and PRR; else MIR.START_T)
    retest       PRR.PART_FLG bit 0 or 1    (this part supersedes an earlier one)
    test_num     PTR.TEST_NUM
    test_name    PTR.TEST_TXT (first occurrence carries it; later PTRs may omit)
    units        PTR.UNITS
    lo_limit     PTR.LO_LIMIT (null when OPT_FLAG says invalid / absent)
    hi_limit     PTR.HI_LIMIT
    result       PTR.RESULT
    test_pass    PTR.TEST_FLG bit 7 == 0 (and bit 6 == 0)
    part_pass    PRR.PART_FLG bit 3 == 0
    hard_bin     PRR.HARD_BIN
    soft_bin     PRR.SOFT_BIN

File-level metadata (``meta`` dict) holds every ``ry.<key>=<value>`` DTR seen
before the first PIR, plus MIR fields, so setup facts (distance, OTA vs
conducted, attenuators, SIMULATED) travel with the data.
"""
from __future__ import annotations

import pyarrow as pa

DATA_ORIGINS = ("SIMULATED", "RIG_OTA", "RIG_CONDUCTED", "PUBLIC_DATASET", "UNKNOWN")

MEASUREMENT_SCHEMA = pa.schema(
    [
        pa.field("lot_id", pa.string()),
        pa.field("station", pa.string()),
        pa.field("build", pa.string()),
        pa.field("data_origin", pa.string()),
        pa.field("site_num", pa.uint8()),
        pa.field("part_id", pa.string()),
        pa.field("part_ts", pa.float64()),
        pa.field("retest", pa.bool_()),
        pa.field("test_num", pa.uint32()),
        pa.field("test_name", pa.string()),
        pa.field("units", pa.string()),
        pa.field("lo_limit", pa.float64()),  # nullable
        pa.field("hi_limit", pa.float64()),  # nullable
        pa.field("result", pa.float64()),
        pa.field("test_pass", pa.bool_()),
        pa.field("part_pass", pa.bool_()),
        pa.field("hard_bin", pa.uint16()),
        pa.field("soft_bin", pa.uint16()),
    ]
)

COLUMNS = MEASUREMENT_SCHEMA.names


def to_table(df) -> pa.Table:
    """pandas DataFrame -> Arrow table conforming exactly to MEASUREMENT_SCHEMA."""
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"measurement frame missing columns: {missing}")
    return pa.Table.from_pandas(df[COLUMNS], schema=MEASUREMENT_SCHEMA, preserve_index=False)


def validate(table: pa.Table) -> None:
    if not table.schema.equals(MEASUREMENT_SCHEMA, check_metadata=False):
        raise ValueError(f"schema mismatch:\n{table.schema}\nexpected:\n{MEASUREMENT_SCHEMA}")
