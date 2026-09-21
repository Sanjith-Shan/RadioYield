"""Render the benchmark JSON from bench.py as a Markdown table (no hand-copied numbers).

usage: python stdf/bench/report.py docs/results/stdf_bench.json
"""
from __future__ import annotations

import json
import os
import sys


def fmt_s(x: float) -> str:
    return f"{x * 1e3:.1f} ms" if x < 1 else f"{x:.2f} s"


def main(path: str) -> None:
    r = json.load(open(path))
    m = r["machine"]
    print(f"Machine: {m.get('model')} / {m.get('cpu')}, {m.get('cores')} cores, "
          f"{(m.get('mem_bytes') or 0) / 2**30:.0f} GiB, {m.get('platform')}; Python {m.get('python')}, "
          f"numpy {m.get('numpy')}, pyarrow {m.get('pyarrow')}, pystdf {m.get('pystdf')}, "
          f"Semi-ATE-STDF {m.get('semi-ate-stdf')}; C++ {m.get('cxx_version')} ({m.get('CMAKE_BUILD_TYPE')}).")
    print(f"Runs per (file, tool): {r['runs']}; bootstrap: 10,000 resamples, seed {r['seed']}; "
          f"started {r['started']}, finished {r.get('finished', '?')}.\n")
    print("| file | MB | records | ours median [95% CI] | ours MB/s | ours rec/s | records=False median | "
          "pystdf median | pystdf x slower than ours [95% CI] | Semi-ATE median | Semi-ATE x slower [95% CI] |")
    print("| --- | ---: | ---: | --- | ---: | ---: | --- | --- | --- | --- | --- |")
    for f in r["files"]:
        t = f["tools"]
        o = t.get("ours", {})
        row = [os.path.basename(f["file"]), f"{f['bytes'] / 1e6:.2f}", f"{o.get('records', '?'):,}"]
        if "error" in o:
            row += [f"ERROR {o['error']}", "", ""]
        else:
            lo, hi = o["median_ci95_s"]
            row += [f"{fmt_s(o['median_s'])} [{fmt_s(lo)}, {fmt_s(hi)}]", f"{o['mb_per_s']:.0f}",
                    f"{o['records_per_s'] / 1e6:.2f} M"]
        om = t.get("ours_meas")
        row.append(fmt_s(om["median_s"]) if om and "median_s" in om else "n/a")
        for k in ("pystdf", "semiate"):
            d = t.get(k)
            if d is None:
                row += ["not run", ""]
            elif "error" in d:
                row += [f"FAILED: {d['error'][:80]}", ""]
            else:
                lo, hi = d["x_vs_ours_ci95"]
                note = f" ({d['note']})" if "note" in d else ""
                row += [fmt_s(d["median_s"]) + note, f"**{d['x_vs_ours']:.1f}x** [{lo:.1f}, {hi:.1f}]"]
        print("| " + " | ".join(row) + " |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/results/stdf_bench.json")
