# STDF reader/writer: results

Every number below was measured on this machine and comes with the command that produced it.
Machine (all runs unless noted): Apple M3 Pro (Mac15,7), 12 cores, 18 GiB, macOS 26.5.1 arm64,
CPython 3.12.7 (project venv), Apple clang 17.0.0, Release build. The competitors and how they
were invoked are described in `docs/baselines/stdf.md`.

## 1. Tests

| what | command | result |
| --- | --- | --- |
| C++ unit + fuzz regressions + public samples (ctest) | `cmake -S stdf -B build/stdf -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build/stdf && ctest --test-dir build/stdf --output-on-failure` | 3/3 tests passed; `core_unit` runs 2028 checks, 0 failures |
| pytest (round trip vs pystdf + Semi-ATE, public samples vs pystdf, robustness) | `stdf/fetch_samples.sh && python -m pytest tests/stdf -q` | 57 passed |
| Same on Linux/gcc (portability check for CI) | Docker `ubuntu:24.04`, g++ 13.3.0, venv with pybind11/numpy/pyarrow/pandas/pystdf/Semi-ATE-STDF from PyPI; same cmake/ctest/pytest commands on a copy of the repo | builds clean (after initializing three variables gcc flagged with -Wmaybe-uninitialized); ctest 3/3, pytest 57 passed |

What the 57 pytest cases cover:
- Round trip, both byte orders, compact and full PTR encoding. A 40-part x 9-test table with
  null limits, null results, null soft bins, a null part_ts, retests and 4 sites is written
  by our writer. Our reader returns exactly the input table, with RESULT and limits rounded
  to float32 because STDF stores them as R4. pystdf decodes the same bytes, and a
  pure-Python version of the record-to-measurement mapping applied to pystdf's records
  gives the identical table. Record counts also equal pystdf's.
- Semi-ATE STDF reads our files in both byte orders: same PTR count, identical RESULTs.
- 10 public sample files (section 4): record counts per type equal pystdf's on every file;
  raw PTR fields match pystdf (every PTR in small files, about 2000 evenly spaced PTRs in
  large ones); the measurement table equals the pystdf-based reference mapping on every
  file. Real data (lot2, a595, 8ALX) written back with our writer in both byte orders
  reads back identical.
- Robustness: every truncation point of a written file in both byte orders; 3000 random
  byte mutations of written and public files; 200 garbage tails after a FAR; all fuzz
  regression inputs.

## 2. Fuzzing

Apple clang 17 ships no libFuzzer, and Homebrew llvm is not installed on this Mac. Its ASan
runtime also hangs at process start on macOS 26.5.1. A two-line test program built with
`clang -fsanitize=address` never reaches `main`; it sits in
`__asan::InitializeShadowMemory -> get_dyld_hdr -> dyld_shared_cache_iterate_text_swift`,
seen with `sample`. So the fuzzer ran **libFuzzer + ASan + UBSan (`-fno-sanitize-recover=all`)
in a Linux container** (Docker Desktop's arm64 Linux VM on this same Mac; Ubuntu 24.04,
clang 18.1.3), built from the same sources: `stdf/fuzz/Dockerfile`, `stdf/fuzz/run_libfuzzer.sh`.

Fuzz target (`stdf/fuzz/fuzz_target.cpp`): `parse_buffer` on the input, then structural
invariants on every produced table (column lengths, offsets monotone and in bounds, validity
bitmap sizes, list/child consistency, valid UTF-8). The measurement table is then
re-encoded with our writer and parsed again, and the run fails if the row count changes or
the re-read raises warnings. Any sanitizer report, abort or invariant failure counts as a
crash. Seeds (`python stdf/fuzz/make_seeds.py`): 8 writer files (both byte orders), the
public samples up to 64 kB, 4 kB and 16 kB prefixes of the larger ones, and the regression
inputs. `-max_len=65536`, fork mode with `-ignore_crashes=1`, so a crash does not end
the campaign.

| run | command | duration | executions | crashes | log |
| --- | --- | --- | --- | --- | --- |
| smoke run before the campaign | `stdf/fuzz/run_libfuzzer.sh 30` (non-fork at the time) | 30 s | 21,020 | **1** (finding 1, stopped the run) | `build/fuzz/libfuzzer_smoke_log.txt` |
| **campaign** | `stdf/fuzz/run_libfuzzer.sh 1800` (2026-11-01, started 07:13 UTC) | **1844 s** (libFuzzer clock) | **2,088,824** (about 1,130/s) | **0** | `docs/results/stdf_fuzz_log.txt` (progress lines); final coverage cov 7581 / ft 10165, corpus 1104 |
| post-fix verification | `stdf/fuzz/run_libfuzzer.sh 600 verify` (harness with UTF-8 checks, after fix 2, resumed from the campaign corpus) | 601 s | 652,067 | **0** | `build/fuzz/verify/log.txt` |

Findings, all fixed, each with a regression test:

1. **Out-of-bounds vector access (UBSan: reference binding to null pointer), found by the
   libFuzzer smoke run.** The per-test-number default cache used `0xFFFFFFFF` as its "no
   previous test" sentinel. A file whose first PTR had `TEST_NUM = 0xFFFFFFFF` hit the
   cache and indexed an empty `std::vector<TestState>`. Fix: an explicit
   `have_last_test_` flag (`stdf/src/reader.cpp`). Regressions: the crashing input
   `stdf/fuzz/regressions/crash-01-ptr-test-num-0xffffffff-first.stdf` (replayed by ctest
   `fuzz_regressions`, the sanitized `fuzz_regressions_sanitized` and pytest) and
   `test_regression_test_num_max` in `stdf/tests/test_core.cpp`.
2. **Python-level crash: UnicodeDecodeError, found by the pytest mutation test
   (`test_random_mutations_never_crash`), not by libFuzzer.** Raw bytes of a `ry.<key>=`
   DTR key went into `meta` (and into a warning message) without Latin-1-to-UTF-8
   mapping. pybind11 then failed to build a Python `str`. The C++ harness could not see
   this because it never converts to Python. Fix: every string that becomes a Python
   `str` (meta keys and values, MIR strings) goes through the same Latin-1-to-UTF-8
   mapping as the columns, and the warning message no longer echoes raw bytes. The fuzz
   harness now also checks meta and warning text for valid UTF-8. Regression:
   `stdf/fuzz/regressions/finding-02-non-utf8-ry-dtr-key.stdf`. The 30-minute campaign ran
   on the build with fix 1 but before fix 2 and before the harness UTF-8 checks; the
   verification run covers the final code.

Also present but not used for the numbers above: `stdf/fuzz/mutate_driver.cpp`
(`-DRY_STDF_FUZZ=ON`), an in-house coverage-guided mutation driver (trace-pc-guard edges,
byte and record-aware mutations, ASan/UBSan death callback that saves the input) for clang
toolchains without libFuzzer. A 60 s smoke run in the Linux container did 89,700
executions with 0 crashes.

## 3. Read throughput vs pystdf and Semi-ATE STDF

Command: `python stdf/bench/bench.py --gen-large && python stdf/bench/bench.py --runs 7 --out docs/results/stdf_bench.json`,
table rendered by `python stdf/bench/report.py docs/results/stdf_bench.json`. Raw times:
`docs/results/stdf_bench.json`. Medians of 7 runs per (file, tool), runs interleaved
across tools. The 95% CIs are percentile bootstrap (10,000 resamples, seed 12345) of the
median, and of the ratio of medians for "x slower". Timed regions:
- **ours** = `radioyield.stdf.read(path)`: decode every record in C++, then build the
  measurement table plus one pyarrow table of raw fields per record type.
- **records=False** = the same, without raw tables for the bulky record types.
- **pystdf** = Parser + sink appending every `(record type, fields)` to a list.
- **Semi-ATE** = `list(records_from_file(path))`.
The competitors build no table at all. Every tool returned the same record count as ours on
every file, and no tool failed on any file.

Machine: Mac15,7 / Apple M3 Pro, 12 cores, 18 GiB, macOS-26.5.1-arm64-arm-64bit; Python 3.12.7, numpy 2.5.4, pyarrow 26.0.0, pystdf 1.4.0, Semi-ATE-STDF 0.1.28; C++ Apple clang version 17.0.0 (clang-1700.6.3.2) (Release).
Runs per (file, tool): 7; bootstrap: 10,000 resamples, seed 12345; started 2026-11-01T00:44:27-0700, finished 2026-11-01T01:08:56-0700.

| file | MB | records | ours median [95% CI] | ours MB/s | ours rec/s | records=False median | pystdf median | pystdf x slower than ours [95% CI] | Semi-ATE median | Semi-ATE x slower [95% CI] |
| --- | ---: | ---: | --- | ---: | ---: | --- | --- | --- | --- | --- |
| itest_GX36_FT.stdf | 0.18 | 831 | 3.9 ms [3.9 ms, 4.0 ms] | 47 | 0.21 M | 0.5 ms | 39.3 ms | **10.0x** [9.9, 10.2] | 139.5 ms | **35.6x** [35.1, 36.0] |
| itest_LX7730.stdf | 0.86 | 11,194 | 2.8 ms [2.8 ms, 3.0 ms] | 302 | 3.93 M | 2.0 ms | 103.8 ms | **36.5x** [35.1, 37.6] | 840.3 ms | **295.1x** [282.9, 299.1] |
| itest_bad_x17y8.stdf | 0.05 | 503 | 0.9 ms [0.8 ms, 0.9 ms] | 55 | 0.59 M | 0.5 ms | 7.5 ms | **8.8x** [8.6, 9.1] | 40.1 ms | **46.8x** [46.2, 48.0] |
| itest_main_lot_8ALX.stdf | 11.69 | 162,655 | 52.4 ms [51.2 ms, 52.9 ms] | 223 | 3.11 M | 23.1 ms | 1.80 s | **34.3x** [33.8, 35.5] | 13.46 s | **257.1x** [251.0, 278.3] |
| metis_test.std | 0.05 | 918 | 0.9 ms [0.8 ms, 1.1 ms] | 55 | 1.07 M | 0.6 ms | 8.1 ms | **9.5x** [7.3, 10.2] | 64.5 ms | **75.3x** [58.0, 80.0] |
| pystdf_lot2.stdf | 4.42 | 58,020 | 17.5 ms [17.1 ms, 17.6 ms] | 252 | 3.31 M | 8.4 ms | 658.7 ms | **37.6x** [37.3, 39.2] | 4.89 s | **279.2x** [276.4, 287.7] |
| pystdf_lot3.stdf | 4.56 | 59,890 | 17.8 ms [17.8 ms, 18.3 ms] | 256 | 3.36 M | 8.7 ms | 677.3 ms | **38.0x** [37.0, 38.2] | 5.02 s | **281.4x** [274.3, 286.5] |
| stdfreader_a595.stdf | 2.10 | 40,651 | 11.9 ms [11.9 ms, 12.2 ms] | 176 | 3.41 M | 5.0 ms | 455.5 ms | **38.2x** [37.4, 38.9] | 3.25 s | **272.8x** [266.3, 287.6] |
| large_3000x400_full.stdf | 65.61 | 1,209,410 | 357.4 ms [342.2 ms, 488.0 ms] | 184 | 3.38 M | 169.5 ms | 14.54 s | **40.7x** [29.5, 42.6] | 160.25 s | **448.4x** [326.3, 494.4] |

`large_3000x400_full.stdf` was generated by our writer (`bench.py --gen-large`):
3000 parts x 400 tests on 4 sites, every PTR written in full (`compact_ptr=False`), 65.6 MB,
1.2 M PTRs. The other files are the public samples of section 4 that are at least 40 kB.

**Headline:** on the largest real public file (itest_main_lot_8ALX, 11.7 MB, 162,655
records) our full read is **34.3x faster than pystdf (95% CI 33.8-35.5)** and **257x
faster than Semi-ATE STDF (251-278)**. On the generated 65.6 MB file it is **40.7x faster
than pystdf (29.5-42.6)** and **448x faster than Semi-ATE (326-494)**, at 184 MB/s and
3.4 M records/s. Measurement-table-only reads (`records=False`) take roughly half that time.
On small files (under 200 kB) the fixed cost of building about 15 pyarrow tables, around
0.5 ms, dominates, and the advantage over pystdf drops to 9-10x.

## 4. Public sample corpus

`stdf/fetch_samples.sh` downloads these files to `data/raw/stdf_samples/` (gitignored)
from pinned commits and checks each sha256. URLs and hashes are in the script and in
`data/raw/stdf_samples/MANIFEST.tsv`. Parsed with `build/stdf/ry_stdf_cat data/raw/stdf_samples`:

| file | source repo | bytes | byte order | records | measurement rows | reader warnings |
| --- | --- | ---: | --- | ---: | ---: | --- |
| pystdf_lot2.stdf | cmars/pystdf | 4,418,001 | big | 58,020 | 52,403 | 0 |
| pystdf_lot3.stdf | cmars/pystdf | 4,558,921 | big | 59,890 | 54,123 | 0 |
| stdfreader_a595.stdf | showjim/STDFReader | 2,101,549 | big | 40,651 | 31,605 | 0 |
| metis_test.std | Semi-ATE/Metis | 47,400 | little | 918 | 572 | 0 |
| stdf4j_test.stdf | liquidkarma/stdf4j | 166 | little | 9 | 0 | 0 |
| itest_main_lot_8ALX.stdf | itestinc/stdf2xls | 11,688,502 | little | 162,655 | 134,532 | 0 |
| itest_LX7730.stdf | itestinc/stdf2xls | 858,858 | little | 11,194 | 3,591 | 0 |
| itest_GX36_FT.stdf | itestinc/stdf2xls | 182,265 | big | 831 | 0 (FTR/MPR only) | 0 |
| itest_bad_x17y8.stdf | itestinc/stdf2xls (`bad_stdf/`) | 46,977 | little | 503 | 2 | 0 |
| itest_bad_spreadsheet14.stdf | itestinc/stdf2xls (`bad_stdf/`) | 2,280 | little | 29 | 0 | 4 (PRR without PIR; 8 PTRs outside any part) |

pystdf parsed all 10 files, with record counts identical to ours (section 1).
