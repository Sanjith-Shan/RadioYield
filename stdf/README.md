# stdf/: STDF V4 reader and writer (C++17, pybind11)

STDF (Standard Test Data Format, V4) is the semiconductor test industry's tester output
format. This directory holds a C++17 reader and writer, a pybind11 module
(`radioyield.stdf._stdf`) and the Python API in `radioyield/stdf/_api.py`. The reader turns
a file into Arrow tables. The writer is what the simulator and the collector use to emit
STDF.

Results (fuzzing, throughput against pystdf and Semi-ATE STDF) are in
`docs/results/stdf.md`. The competitors and how they were run are in `docs/baselines/stdf.md`.

## Layout

| Path | What |
| --- | --- |
| `include/ry_stdf/stdf.hpp` | public C++ API: `parse_buffer`, `parse_file`, `write_buffer`, `write_file`, `Column`, `Table`, `Stats` |
| `src/records.cpp` | field layouts for every supported record, Arrow-layout `Column` builder |
| `src/reader.cpp` | parser, record to column decoding, measurement-table join |
| `src/writer.cpp` | measurement table to STDF |
| `src/bytes.hpp` | bounds-checked field cursor, byte swapping |
| `python/module.cpp` | pybind11 bindings (zero-copy numpy views of the C++ buffers) |
| `tools/stdf_cat.cpp` | `ry_stdf_cat`: prints record counts, rows, warnings and parse time for files or directories |
| `tests/test_core.cpp` | C++ unit tests (ctest `core_unit`) |
| `fuzz/` | libFuzzer target, Docker campaign script, in-house mutation driver, seed builder, `regressions/` |
| `bench/bench.py` | throughput benchmark against pystdf and Semi-ATE STDF |
| `fetch_samples.sh` | downloads the public sample files (pinned commits, sha256-checked) |

## Supported records

All V4 records that carry data are decoded field by field into a raw table, with names as
in the STDF V4 spec:

FAR, ATR, MIR, MRR, PCR, HBR, SBR, PMR, PGR, PLR, RDR, SDR, WIR, WRR, WCR, PIR, PRR, TSR,
PTR, MPR, FTR, BPS, EPS, GDR, DTR.

Arrow types: U1/B1/N1 become uint8, U2 uint16, U4 uint32, I1/I2/I4 int8/16/32, R4 float32,
R8 float64, C1/Cn string, Bn/Dn binary (payload bytes only), kxU1/kxN1 list<uint8>,
kxU2 list<uint16>, kxR4 list<float32>, kxCn list<string>. GDR `GEN_DATA` becomes
list<string> of `TYPE:value` items (`U1:42`, `R4:0.5`, `Cn:text`, `Bn:hex`).

- **Unknown record types** are skipped by REC_LEN and counted in
  `stats["unknown"]`/`stats["unknown_types"]`. They are never fatal.
- **Missing trailing fields.** A record may end before its last field. Every field after
  the end is null in the raw table, and the measurement mapping falls back to the STDF
  defaults described below.
- **Byte order** comes from `FAR.CPU_TYPE`: 1 means big endian, 2 means little endian. Any
  other value gives a warning, and the order is then inferred from how FAR.REC_LEN is
  laid out (`00 02` means big, `02 00` means little). IEEE floats are assumed in every
  case, so VAX/PDP-11 floats (CPU_TYPE 0) are not converted.
- **Strings** are ASCII per the spec. Stray bytes at or above 0x80 are treated as Latin-1
  and stored as UTF-8, so every string column is valid UTF-8.

## Measurement table (radioyield/schema.py contract)

`read(path).measurements` conforms exactly to `MEASUREMENT_SCHEMA`. It has one row per PTR
that lies between a PIR and its PRR for the same (HEAD_NUM, SITE_NUM). Rows are in file
order. Each row carries its part's fields.

| column | source |
| --- | --- |
| lot_id | MIR.LOT_ID (null if no MIR) |
| station | MIR.NODE_NAM |
| build | DTR `ry.build=<label>` before the first PIR (null if absent) |
| data_origin | DTR `ry.data_origin=<origin>` before the first PIR, else `"UNKNOWN"` |
| site_num | PTR.SITE_NUM (the part's PIR/PRR use the same site) |
| part_id | PRR.PART_ID; an empty string becomes null |
| part_ts | DTR `ry.part_ts=<unix s>` between PIR and PRR (applies to the most recently opened part); else MIR.START_T. `ry.part_ts=` with an empty value means explicitly null |
| retest | PRR.PART_FLG bit 0 or bit 1 |
| test_num | PTR.TEST_NUM |
| test_name | PTR.TEST_TXT, or the test's default if empty or missing |
| units | PTR.UNITS, or the test's default if empty or missing |
| lo_limit / hi_limit | see the PTR default rules below; null when there is no limit |
| result | PTR.RESULT as float64; null when TEST_FLG bit 1 ("result not valid") is set or RESULT is missing |
| test_pass | `TEST_FLG & 0xC0 == 0` (bit 7 = fail, bit 6 = no pass/fail indication) |
| part_pass | PRR.PART_FLG bit 3 == 0 |
| hard_bin / soft_bin | PRR.HARD_BIN / PRR.SOFT_BIN; 65535 (the STDF "invalid" value) becomes null |

**PTR default semantics.** The first PTR with a given TEST_NUM sets that test's defaults
for the low and high limits. A later PTR may leave out OPT_FLAG and everything after it,
or set OPT_FLAG bit 4 or bit 5 ("LO_LIMIT/HI_LIMIT invalid, use the default"). In either
case it inherits the default. OPT_FLAG bit 6 or bit 7 ("no low/high limit") gives null
for that row. An explicit, valid limit on a later PTR applies only to that row; defaults
are never updated after the first PTR. The default test_name and units are the first
non-empty TEST_TXT and UNITS seen for the test number.

**Parts.** A PRR closes the open part on its (head, site). If a PIR arrives while a part
is still open on the same (head, site), the old part is left unclosed and a warning is
raised. Rows of a part that never gets a PRR have null part_id, retest, part_pass and bins.

**Not in the measurement table.** PTRs outside any PIR/PRR pair. The spec allows these as
"defaults only" records: they still set the test defaults and are counted in
`stats["events"]["ptr_outside_part"]`. MPR and FTR records appear only in `records`.
RES_SCAL, LLM_SCAL and HLM_SCAL are not applied: values are reported as stored, as pystdf
and Semi-ATE also report them.

### `meta`

- every MIR field present in the file, under its STDF name (`LOT_ID`, `NODE_NAM`,
  `START_T`, ...): U fields as int, C fields as str;
- every `ry.<key>=<value>` DTR before the first PIR, as `key: "value"` (str). Later
  duplicates win;
- `lot_id` and `station` as aliases of LOT_ID and NODE_NAM, and `data_origin`
  (`"UNKNOWN"` if the file has none).

### ry.* DTR conventions

| DTR text | where | meaning |
| --- | --- | --- |
| `ry.build=<label>` | before the first PIR | build label (EVT, DVT, firmware table A, ...) |
| `ry.data_origin=<origin>` | before the first PIR | one of `SIMULATED, RIG_OTA, RIG_CONDUCTED, PUBLIC_DATASET, UNKNOWN` |
| `ry.<key>=<value>` | before the first PIR | any setup fact (distance_m, attenuation_db, ...) and lands in `meta` |
| `ry.part_ts=<unix seconds>` | between PIR and PRR | part timestamp as a float64 written with `%.17g`, so it round-trips exactly |

The keys `lot_id` and `station` are reserved, because they come from the MIR. ry DTRs
after the first PIR (other than part_ts) do not go into `meta`. Non-ry DTRs are untouched
and appear in `records["DTR"]`.

## Writer

```
FAR(CPU_TYPE 2 for "<", 1 for ">"; STDF_VER 4)
MIR(LOT_ID=lot_id, NODE_NAM=station, START_T/SETUP_T = floor(min part_ts) unless given, other MIR keys from meta)
DTR ry.build=..., DTR ry.data_origin=..., DTR ry.<key>=... for every other meta key
per part (first-appearance order):
    PIR(head 1, site) / DTR ry.part_ts=<%.17g or empty> / PTR... / PRR
HBR, SBR (head 255: one per bin, count, P/F), PCR (head 255), TSR (head 255, one per test:
exec/fail counts, min/max/sum/sum of squares of results), MRR(FINISH_T = floor(max part_ts))
```

- A **part** is the set of rows sharing (part_id, site_num, part_ts, retest, hard_bin,
  soft_bin, part_pass). Parts come out in first-appearance order and rows keep their order
  within a part. A table already grouped by part therefore round-trips row for row.
- **PTRs.** The first PTR of each TEST_NUM is written in full: TEST_TXT, OPT_FLAG
  (0x0E plus 0x40/0x80 for a null limit), LO_LIMIT, HI_LIMIT and UNITS. With
  `compact_ptr=True` (the default), a later PTR whose name, units and limits match the
  test's defaults ends after ALARM_ID, as the spec allows. `compact_ptr=False` writes
  every PTR in full.
- **Nulls.** A null result is written as RESULT 0 with TEST_FLG bit 1. A null test_pass
  sets TEST_FLG bit 6. A null part_pass sets PRR.PART_FLG bit 4. A null bin is written as
  65535. A null part_id is written as an empty string. A null site_num is written as 1.
  `retest=True` sets PART_FLG bit 0.
- **Meta keys.** Upper-case MIR field names (PART_TYP, JOB_NAM, START_T, TST_TEMP, ...)
  go into the MIR. All other keys become `ry.<key>=<str(value)>` DTRs; floats use
  `repr`. lot_id, station, build and data_origin are required. Any of them missing from
  `meta` is taken from the table when the table holds a single value. If `meta` and the
  table disagree, the writer raises.
- One file holds one (lot_id, station, build, data_origin). `write()` raises if the table
  holds more than one, and `write_many(dir, table, meta)` splits the table and writes one
  file per group.
- If the write fails partway, the partial output file is removed.

## Python API

```python
from radioyield.stdf import read, write
from radioyield.stdf._api import read_bytes, write_bytes, write_many, StdfError, StdfWarning

f = read("lot.stdf")              # StdfFile
f.measurements                    # pa.Table, MEASUREMENT_SCHEMA
f.records["PTR"]                  # pa.Table of raw PTR fields (also MIR, PRR, HBR, TSR, ...)
f.meta                            # dict
f.stats                           # counts, unknown, warnings, events, truncated, byte_order, ...
read("lot.stdf", records=False)   # skip raw tables for PTR/MPR/FTR/PIR/PRR/DTR/GDR (roughly half the time; docs/results/stdf.md)

write("out.stdf", table_or_dataframe,
      {"lot_id": "L1", "station": "OTA-1", "build": "EVT", "data_origin": "SIMULATED",
       "distance_m": 3.0, "PART_TYP": "ESP32-C6"},
      byte_order="<")             # or ">"
```

**Errors.** `StdfError` (a `ValueError`) is raised only when the input cannot be STDF at
all: an empty file, a first record that is not a FAR, or fewer than 6 bytes. Damage inside
a file never raises. The result is partial, with `stats["warnings"]`, `stats["events"]`
counters, the first 50 messages in `stats["warning_messages"]`, and
`stats["truncated"]=True` when a record runs past EOF. `read` also emits one
`StdfWarning` per file that had warnings; pass `warn=False` to silence it.

**Data handoff.** C++ builds each column as Arrow buffers: an LSB validity bitmap, data,
and int32 offsets. The pybind layer moves those `std::vector`s into numpy arrays that own
them, and `pa.Array.from_buffers` wraps the numpy arrays without copying. The GIL is
released while parsing and writing.

## Build

```bash
source .venv/bin/activate                       # needs pybind11 importable by this python
cmake -S stdf -B build/stdf -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build/stdf                        # -> radioyield/stdf/_stdf.cpython-*.so
ctest --test-dir build/stdf --output-on-failure # core_unit, fuzz_regressions, public_samples
stdf/fetch_samples.sh                           # public samples -> data/raw/stdf_samples/
python -m pytest tests/stdf                     # needs pystdf + Semi-ATE-STDF (bench extra)
```

CMake options: `RY_STDF_PYTHON` (ON), `RY_STDF_FUZZ` (sanitized mutation driver and
sanitized regression replay), `RY_STDF_LIBFUZZER` (needs a clang that ships libFuzzer). The
default build uses only portable C++17 and builds with gcc and clang. The `public_samples`
ctest passes as a skip when the samples have not been fetched.

Fuzzing: `python stdf/fuzz/make_seeds.py && stdf/fuzz/run_libfuzzer.sh 1800`. This runs
libFuzzer with ASan and UBSan in a Linux container, because Apple clang has no libFuzzer
and its ASan runtime hangs at startup on macOS 26. Every crash input found is kept in
`fuzz/regressions/` and replayed by ctest and pytest.

Benchmark: `python stdf/bench/bench.py --gen-large && python stdf/bench/bench.py --runs 7`.

## Limits

- Input files are fully memory-mapped (POSIX) or read into memory. There is no streaming
  and no compressed input (.gz/.bz2/.xz).
- Each string, binary or list column holds at most 2 GiB of data (int32 offsets). The
  reader raises `StdfError` beyond that.
- Writer: every Cn field (test_name, units, part_id, meta values) is at most 255 bytes, or
  the writer raises. RESULT and the limits are stored as 4-byte floats (STDF R4), so they
  round to float32. NUM_TEST is capped at 65535. HEAD_NUM is always 1. X/Y coordinates,
  spec limits and MPR/FTR are not written.
- Round-trip losses that STDF itself forces: an empty string reads back as null for
  part_id, test_name and units. A null test_name or units on a later row inherits the
  test's default. A null test_pass reads back as False, by the TEST_FLG bit 6 rule of the
  contract. A null retest is written as False.
- `ry.part_ts` is parsed with `strtod` (C locale assumed).
- MPR and FTR results are only in `records`; the measurement contract covers PTR only.
