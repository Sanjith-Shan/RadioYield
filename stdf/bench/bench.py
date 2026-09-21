"""STDF read throughput: radioyield (C++) vs pystdf vs Semi-ATE STDF.

Same files, same machine, same process; N timed runs per (file, tool), runs
interleaved across tools. Reports median wall time, records/s, MB/s, a 95%
percentile-bootstrap CI of the median, and "x faster" (competitor median /
our median) with a bootstrap CI of that ratio (fixed seed).

Timed region per tool (file bytes are in the OS page cache for every tool):
  ours          radioyield.stdf.read(path): open + mmap, decode every record in
                C++, build the measurement table (MEASUREMENT_SCHEMA) and one
                pyarrow table of raw fields per record type.
  ours_meas     read(path, records=False): same, without raw tables for the
                bulky record types (PTR/MPR/FTR/PIR/PRR/DTR/GDR). Informational.
  pystdf        pystdf.IO.Parser over the open file with a sink that appends
                every (record type, field list) to a Python list: every record
                fully decoded into Python objects. No table is built (that
                would only add time to pystdf).
  semiate       list(Semi_ATE.STDF.records_from_file(path)): every record
                decoded into a Semi-ATE record object. No table is built.

usage:
  python stdf/bench/bench.py --gen-large               # writes data/cache/stdf_bench/*.stdf
  python stdf/bench/bench.py --runs 7 --out build/stdf_bench.json
"""
from __future__ import annotations

import argparse
import contextlib
import gc
import glob
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LARGE_DIR = os.path.join(ROOT, "data", "cache", "stdf_bench")


# ------------------------------------------------------------------ tools
def run_ours(path):
    from radioyield.stdf import read

    f = read(path, warn=False)
    return f.stats["records"]


def run_ours_meas(path):
    from radioyield.stdf import read

    f = read(path, records=False, warn=False)
    return f.stats["records"]


def run_pystdf(path):
    from pystdf.IO import Parser

    out = []

    class Sink:
        def after_send(self, ds, data):
            out.append(data)

    with open(path, "rb") as fh, contextlib.redirect_stderr(io.StringIO()):
        p = Parser(inp=fh)
        p.addSink(Sink())
        p.parse()
    return len(out)


def run_semiate(path):
    from Semi_ATE.STDF import records_from_file

    recs = list(records_from_file(path))
    return sum(r is not None for r in recs)


TOOLS = {"ours": run_ours, "ours_meas": run_ours_meas, "pystdf": run_pystdf, "semiate": run_semiate}


# ------------------------------------------------------------------ stats
def boot_median_ci(x, rng, b=10000):
    x = np.asarray(x)
    meds = np.median(x[rng.integers(0, len(x), (b, len(x)))], axis=1)
    return float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def boot_ratio_ci(num, den, rng, b=10000):
    num, den = np.asarray(num), np.asarray(den)
    a = np.median(num[rng.integers(0, len(num), (b, len(num)))], axis=1)
    c = np.median(den[rng.integers(0, len(den), (b, len(den)))], axis=1)
    r = a / c
    return float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


# ------------------------------------------------------------------ data
def gen_large(parts: int, tests: int, compact: bool) -> str:
    import pyarrow as pa

    from radioyield.schema import MEASUREMENT_SCHEMA
    from radioyield.stdf import write

    os.makedirs(LARGE_DIR, exist_ok=True)
    path = os.path.join(LARGE_DIR, f"large_{parts}x{tests}_{'compact' if compact else 'full'}.stdf")
    if os.path.exists(path):
        return path
    rng = np.random.default_rng(2026)
    n = parts * tests
    p = np.repeat(np.arange(parts), tests)
    t = np.tile(np.arange(tests), parts)
    bands = ["wifi_tx_pwr_ch", "ble_rx_sens_ch", "wifi_evm_ch", "zb_tx_pwr_ch"]
    names = [f"{bands[i % 4]}{i // 4:03d}_mcs{i % 8}" for i in range(tests)]
    units = ["dBm", "dBm", "dB", "dBm"]
    nominal = rng.normal(0, 5, tests)
    res = nominal[t] + rng.normal(0, 0.5, n)
    lo, hi = nominal[t] - 1.5, nominal[t] + 1.5
    tp = (res >= lo) & (res <= hi)
    pp = np.ones(parts, bool)
    np.logical_and.at(pp, p, tp)
    table = pa.table({
        "lot_id": pa.array(["BENCHLOT"] * n), "station": pa.array(["RF1"] * n),
        "build": pa.array(["DVT"] * n), "data_origin": pa.array(["SIMULATED"] * n),
        "site_num": (p % 4 + 1).astype(np.uint8), "part_id": pa.array([f"U{i:06d}" for i in p]),
        "part_ts": 1.75e9 + p * 2.5, "retest": np.zeros(n, bool), "test_num": (1000 + t).astype(np.uint32),
        "test_name": pa.array([names[i] for i in t]), "units": pa.array([units[i % 4] for i in t]),
        "lo_limit": lo, "hi_limit": hi, "result": res, "test_pass": tp, "part_pass": pp[p],
        "hard_bin": np.where(pp[p], 1, 2).astype(np.uint16), "soft_bin": np.where(pp[p], 1, 20).astype(np.uint16),
    }).cast(MEASUREMENT_SCHEMA)
    write(path, table, byte_order="<", compact_ptr=compact)
    return path


def machine_info() -> dict:
    def sh(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            return None

    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu": sh(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor(),
        "cores": os.cpu_count(),
        "mem_bytes": int(sh(["sysctl", "-n", "hw.memsize"]) or 0) or None,
        "model": sh(["sysctl", "-n", "hw.model"]),
    }
    from importlib.metadata import version

    for pkg in ("numpy", "pyarrow", "pystdf", "semi-ate-stdf", "pybind11"):
        try:
            info[pkg] = version(pkg)
        except Exception:
            info[pkg] = None
    cache = os.path.join(ROOT, "build", "stdf", "CMakeCache.txt")
    if os.path.exists(cache):
        for line in open(cache):
            if line.startswith(("CMAKE_CXX_COMPILER:", "CMAKE_BUILD_TYPE:")):
                k, v = line.strip().split("=", 1)
                info[k.split(":")[0]] = v
    cxx = info.get("CMAKE_CXX_COMPILER")
    if cxx:
        info["cxx_version"] = (sh([cxx, "--version"]) or "").splitlines()[0]
    return info


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "stdf_bench.json"))
    ap.add_argument("--tools", default="ours,ours_meas,pystdf,semiate")
    ap.add_argument("--files", nargs="*", help="default: public samples >= 40 kB + large generated files")
    ap.add_argument("--gen-large", action="store_true")
    ap.add_argument("--large-parts", type=int, default=3000)
    ap.add_argument("--large-tests", type=int, default=400)
    ap.add_argument("--large-compact", action="store_true", help="also write a compact-PTR variant")
    ap.add_argument("--seed", type=int, default=12345)
    a = ap.parse_args()

    if a.gen_large:
        for compact in ((False, True) if a.large_compact else (False,)):
            p = gen_large(a.large_parts, a.large_tests, compact)
            print(f"{p}: {os.path.getsize(p) / 1e6:.1f} MB")
        return

    files = a.files
    if not files:
        files = [p for p in sorted(glob.glob(os.path.join(ROOT, "data", "raw", "stdf_samples", "*.std*")))
                 if os.path.getsize(p) >= 40_000]
        files += sorted(glob.glob(os.path.join(LARGE_DIR, "*.stdf")))
    tools = a.tools.split(",")
    rng = np.random.default_rng(a.seed)
    result = {"machine": machine_info(), "runs": a.runs, "seed": a.seed, "tools": tools,
              "command": " ".join([os.path.relpath(sys.executable, ROOT) if sys.executable.startswith(ROOT)
                                   else "python"] + [os.path.relpath(sys.argv[0], ROOT)] + sys.argv[1:]),
              "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": []}

    # warm imports once (not timed)
    small = min(files, key=os.path.getsize)
    for t in tools:
        try:
            TOOLS[t](small)
        except Exception:
            pass

    for path in files:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:  # page cache
            while fh.read(1 << 24):
                pass
        entry = {"file": os.path.relpath(path, ROOT), "bytes": size, "tools": {}}
        times = {t: [] for t in tools}
        counts, errors = {}, {}
        for r in range(a.runs):
            for t in tools:
                if t in errors:
                    continue
                gc.collect()
                t0 = time.perf_counter()
                try:
                    n = TOOLS[t](path)
                except Exception as e:  # a competitor failing is reported, not dropped
                    errors[t] = f"{type(e).__name__}: {str(e)[:300]}"
                    continue
                dt = time.perf_counter() - t0
                times[t].append(dt)
                counts[t] = n
            print(f"  {os.path.basename(path)} run {r + 1}/{a.runs}: " +
                  ", ".join(f"{t}={times[t][-1]:.3f}s" for t in tools if times[t] and t not in errors), flush=True)
        ref = counts.get("ours")
        for t in tools:
            if t in errors:
                entry["tools"][t] = {"error": errors[t]}
                continue
            med = statistics.median(times[t])
            lo, hi = boot_median_ci(times[t], rng)
            d = {"times_s": times[t], "median_s": med, "median_ci95_s": [lo, hi], "records": counts[t],
                 "records_per_s": counts[t] / med, "mb_per_s": size / 1e6 / med}
            if t != "ours" and "ours" in times and times["ours"]:
                d["x_vs_ours"] = med / statistics.median(times["ours"])
                d["x_vs_ours_ci95"] = list(boot_ratio_ci(times[t], times["ours"], rng))
            if ref is not None and counts[t] != ref:
                d["note"] = f"record count {counts[t]} differs from ours ({ref})"
            entry["tools"][t] = d
        result["files"].append(entry)
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w") as fh:
            json.dump(result, fh, indent=1)
        print(json.dumps({t: {k: v for k, v in d.items() if k != "times_s"} for t, d in entry["tools"].items()},
                         indent=None), flush=True)
    result["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(a.out, "w") as fh:
        json.dump(result, fh, indent=1)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
