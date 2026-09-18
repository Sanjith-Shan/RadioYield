"""Build the fuzz seed corpus in build/fuzz/seeds (gitignored).

Seeds: small files from our writer (both byte orders, with nulls / retests /
multi-site), prefixes of the public samples (data/raw/stdf_samples, run
stdf/fetch_samples.sh first), whole public samples under --max-len, and the
checked-in regression inputs.

usage: python stdf/fuzz/make_seeds.py [--out build/fuzz/seeds] [--max-len 65536]
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil

import numpy as np
import pyarrow as pa

from radioyield.schema import MEASUREMENT_SCHEMA
from radioyield.stdf._api import write_bytes

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def synth(parts: int, tests: int, seed: int) -> pa.Table:
    rng = np.random.default_rng(seed)
    n = parts * tests
    p = np.repeat(np.arange(parts), tests)
    t = np.tile(np.arange(tests), parts)
    lo = np.where(t % 3 == 0, np.nan, -1.0 - t)
    res = rng.normal(0, 1, n)
    cols = {
        "lot_id": ["L1"] * n,
        "station": ["S1"] * n,
        "build": ["B0"] * n,
        "data_origin": ["SIMULATED"] * n,
        "site_num": (p % 4).astype(np.uint8),
        "part_id": [f"P{i}" for i in p],
        "part_ts": 1.7e9 + p.astype(float),
        "retest": p == parts - 1,
        "test_num": (1000 + t).astype(np.uint32),
        "test_name": [f"t{i}" for i in t],
        "units": ["dBm" if i % 2 else "V" for i in t],
        "lo_limit": pa.array(lo, from_pandas=True),
        "hi_limit": 2.0 + t,
        "result": pa.array(np.where((p == 1) & (t == 0), np.nan, res), from_pandas=True),
        "test_pass": np.abs(res) < 2,
        "part_pass": p % 3 != 0,
        "hard_bin": (1 + p % 3).astype(np.uint16),
        "soft_bin": (10 + p % 5).astype(np.uint16),
    }
    return pa.table(cols).cast(MEASUREMENT_SCHEMA)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "fuzz", "seeds"))
    ap.add_argument("--max-len", type=int, default=65536)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    k = 0
    for parts, tests, seed in [(1, 1, 0), (3, 4, 1), (5, 7, 2), (12, 3, 3)]:
        for bo in "<>":
            data = write_bytes(synth(parts, tests, seed), byte_order=bo)
            with open(os.path.join(a.out, f"writer_{parts}x{tests}_{'le' if bo == '<' else 'be'}.stdf"), "wb") as f:
                f.write(data)
            k += 1
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "raw", "stdf_samples", "*.std*"))):
        data = open(p, "rb").read()
        base = os.path.basename(p)
        if len(data) <= a.max_len:
            open(os.path.join(a.out, base), "wb").write(data)
            k += 1
        for cut in (4096, 16384):
            if len(data) > cut:
                open(os.path.join(a.out, f"{base}.head{cut}"), "wb").write(data[:cut])
                k += 1
    for p in glob.glob(os.path.join(ROOT, "stdf", "fuzz", "regressions", "*")):
        shutil.copy(p, a.out)
        k += 1
    print(f"wrote {k} seeds to {a.out}")


if __name__ == "__main__":
    main()
