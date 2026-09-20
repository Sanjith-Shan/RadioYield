"""Malformed input: truncation, garbage, mutations, fuzz regressions. Never a crash."""
from __future__ import annotations

import glob
import os
import random
import warnings

import pytest

from _stdf_reference import synth_table
from radioyield.schema import MEASUREMENT_SCHEMA
from radioyield.stdf._api import StdfError, read, read_bytes, write_bytes

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REGRESSIONS = sorted(glob.glob(os.path.join(ROOT, "stdf", "fuzz", "regressions", "*")))
META = {"lot_id": "LOT-A", "station": "ST1", "build": "EVT", "data_origin": "SIMULATED"}


def _parse(data: bytes):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            f = read_bytes(data)
        except StdfError:
            return None
    assert f.measurements.schema.equals(MEASUREMENT_SCHEMA)
    for t in f.records.values():
        t.validate(full=True)
    f.measurements.validate(full=True)
    assert f.stats["bytes_parsed"] <= f.stats["bytes"] == len(data)
    return f


@pytest.mark.parametrize("byte_order", ["<", ">"])
def test_every_truncation_point(byte_order):
    data = write_bytes(synth_table(parts=4, tests=3, seed=9), META, byte_order=byte_order)
    full = _parse(data)
    rows = []
    for cut in range(len(data) + 1):
        f = _parse(data[:cut])
        if cut < 6:
            assert f is None
            continue
        rows.append(f.measurements.num_rows)
        if cut < len(data):
            # a prefix never has more rows than the whole file
            assert f.measurements.num_rows <= full.measurements.num_rows
    assert rows[-1] == full.measurements.num_rows
    assert rows == sorted(rows)  # rows only appear as more of the file is available


def test_truncated_flag_and_warning():
    data = write_bytes(synth_table(parts=2, tests=2), META)
    with pytest.warns(Warning, match="truncated"):
        f = read_bytes(data[: len(data) - 3])
    assert f.stats["truncated"] and f.stats["events"]["truncated_record"] == 1


def test_random_mutations_never_crash():
    rng = random.Random(1234)
    base = [write_bytes(synth_table(parts=3, tests=3, seed=s), META, byte_order=bo)
            for s, bo in [(1, "<"), (2, ">")]]
    for p in glob.glob(os.path.join(ROOT, "data", "raw", "stdf_samples", "*.std*"))[:4]:
        with open(p, "rb") as fh:
            base.append(fh.read(8192))
    for _ in range(3000):
        d = bytearray(rng.choice(base))
        for _ in range(rng.randint(1, 8)):
            op = rng.randrange(4)
            i = rng.randrange(len(d))
            if op == 0:
                d[i] = rng.randrange(256)
            elif op == 1:
                d[i:i + 2] = rng.choice([b"\xff\xff", b"\x00\x00", b"\x01\x00", b"\x00\x01"])
            elif op == 2:
                del d[i:i + rng.randint(1, 16)]
            else:
                d[i:i] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 16)))
        _parse(bytes(d))


def test_garbage_after_far_is_partial_not_fatal():
    rng = random.Random(7)
    for k in range(200):
        head = b"\x02\x00\x00\x0a\x02\x04" if k % 2 else b"\x00\x02\x00\x0a\x01\x04"
        f = _parse(head + bytes(rng.randrange(256) for _ in range(rng.randrange(1, 3000))))
        assert f is not None


@pytest.mark.parametrize("path", REGRESSIONS, ids=[os.path.basename(p) for p in REGRESSIONS])
def test_fuzz_regressions(path):
    with open(path, "rb") as fh:
        _parse(fh.read())


def test_records_false_same_measurements():
    data = write_bytes(synth_table(parts=5, tests=4), META)
    a, b = read_bytes(data), read_bytes(data, records=False)
    assert a.measurements.equals(b.measurements)
    assert "PTR" in a.records and "PTR" not in b.records and "MIR" in b.records


def test_read_accepts_pathlike(tmp_path):
    p = tmp_path / "x.stdf"
    p.write_bytes(write_bytes(synth_table(parts=2, tests=2), META))
    assert read(p).measurements.num_rows == 4
