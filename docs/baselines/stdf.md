# STDF baselines

The STDF reader in `stdf/` is compared against the two open-source Python STDF libraries
that are installable from PyPI. Both were installed into the project venv
(`.venv`, CPython 3.12.7, macOS arm64) and imported in the same Python process as our
reader. The numbers are in `docs/results/stdf.md`.

## pystdf 1.4.0

- What it is: "The Pythonic STDF Parser", by Casey Marshall; the long-standing reference
  Python STDF V4 parser (https://github.com/cmars/pystdf, GPL-2.0, PyPI `pystdf`).
  Pure Python, decodes each field with `struct.unpack`, and pushes each record to its
  sinks as `(record_type, [field values])`.
- How it was invoked (benchmark, `stdf/bench/bench.py::run_pystdf`): open the file,
  run `pystdf.IO.Parser(inp=fh)` with one sink whose `after_send` appends each
  `(record_type, fields)` tuple to a Python list, then call `parse()`. stderr is redirected
  to an in-memory buffer during the call, because pystdf prints a line for every record
  with unprocessed bytes. This decodes every record into Python objects. No table is
  built, which would only add time to pystdf.
- How it was invoked (correctness, `tests/stdf/_stdf_reference.py::pystdf_records`): the
  same parser, with each record turned into `{field name: value}`. Our record counts, our
  raw PTR fields, and a pure-Python re-implementation of the measurement mapping applied
  to pystdf's records are all compared with our reader's output.
- Behaviour to know: byte order comes from FAR.CPU_TYPE (2 means little endian; any other
  value is treated as big endian). Missing trailing fields come back as `None`. Cn fields
  are decoded as ASCII, so a non-ASCII byte raises `UnicodeDecodeError`.

## Semi-ATE STDF 0.1.28

- What it is: the Semi-ATE project's STDF library (https://github.com/Semi-ATE/STDF,
  MIT, PyPI `Semi-ATE-STDF`, import `Semi_ATE.STDF`). Pure Python. Each record is an
  object holding a field dictionary, with ATDF/JSON conversion support.
- How it was invoked (benchmark, `stdf/bench/bench.py::run_semiate`):
  `list(Semi_ATE.STDF.records_from_file(path))`. The generator reads each record and
  builds the matching record object, whose constructor unpacks every field. No further
  field access or table building is timed.
- How it was invoked (correctness, `tests/stdf/test_stdf_roundtrip.py::test_semiate_reads_our_files`):
  `records_from_file` plus `rec.to_dict()` on files written by our writer in both byte
  orders. The PTR count and every RESULT value must match our reader's.
- Behaviour to know: byte order comes from FAR.CPU_TYPE (1 means big, 2 means little;
  anything else is unsupported). A record shorter than its REC_LEN ends iteration
  silently.

## Fairness notes

- All three readers run in the same process on the same files. Every file is read once
  before timing, so it is in the OS page cache. Each tool is warmed once on the smallest
  file so that imports and first-call costs fall outside the timed region. Runs are
  interleaved per iteration (ours, ours_meas, pystdf, semiate, then repeat), and
  `gc.collect()` runs before every timed call.
- Our timed region does strictly more work than the competitors': it builds pyarrow
  tables (the measurement table plus one table per record type), while the competitors
  only produce Python objects per record.
- Record-count parity is checked per file and run. Any tool that returns a different
  record count from ours, or raises, has that fact recorded in the JSON and the results
  table rather than being dropped.
