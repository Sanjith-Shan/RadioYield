"""Rig-session report: what a day on the ESP32-C6 rig produced, organised by the questions
the session asks (RF fits, the two deliberate "builds", drift across the session) rather
than as a production lot. Used by `radioyield report --rig DIR`.

For a virtual-rig session (radioyield.sim.rig) the directory also holds TRUTH.json, and the
report adds a known-answer table: each fit next to the value the simulation was built with.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from radioyield.analysis.compare import compare_builds
from radioyield.analysis.rig import load_rig_dir, rf_from_rig
from .html import CSS, NOT_MEASURED, _origin_banner, _section_compare, _section_rf, _table, _tile
from .svg import esc, fmt

BUILD_PAIRS = (("EVT1", "EVT2", "firmware TX-power table variant (EVT2 = every power 2 dB lower), same board"),
               ("ORIENT_A", "ORIENT_B", "antenna orientation variant (DUT rotated 90 degrees), same board and firmware"))

VIRTUAL_RIG = [
    "Nothing in this report is a measurement. The collector, STDF writer and fits are the real code; the boards and "
    "the hallway are radioyield.sim.rig, a simulation whose ground truth is listed in the known-answer table below.",
    "Path-loss exponent and shadowing come from ITU-R P.1238-13 (corridor LoS), sensitivities from the ESP32-C6 "
    "datasheet; wall loss, fading, RSSI bias, TX error and attenuator tolerance are assumptions.",
    "Not simulated: interference from other 2.4 GHz traffic, radiated leakage around the conducted chain, board "
    "resets, USB drop-outs, temperature.",
]


def _inventory(steps: pd.DataFrame, packets: pd.DataFrame) -> pd.DataFrame:
    g = steps.groupby("run_uid", sort=False)
    inv = g.agg(plan=("plan", "first"), build=("build", "first"), unit=("unit_serial", "first"),
                mode=("mode", "first"), position=("position_label", "first"), distance_m=("distance_m", "first"),
                steps=("step_idx", "size"), per_min=("per_pct", "min"), per_max=("per_pct", "max"),
                t=("t_start", "min")).reset_index()
    flags = g["flags"].apply(lambda s: ";".join(sorted({f for x in s.dropna() for f in x.split(";") if f})))
    inv["flags"] = inv["run_uid"].map(flags)
    n_pk = packets.groupby("run_uid").size() if len(packets) else pd.Series(dtype=int)
    inv["packets"] = inv["run_uid"].map(n_pk).fillna(0).astype(int)
    return inv.sort_values("t")


def _comparisons(rig_dir: Path, n_boot: int) -> list[tuple[str, object]]:
    fs = sorted(rig_dir.rglob("build_compare*.measurements.parquet"))
    if not fs:
        return []
    m = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    m = m[m["test_name"].str.endswith("RSSI_MEDIAN")]
    out = []
    for a, b, what in BUILD_PAIRS:
        if {a, b} <= set(m["build"].unique()):
            out.append((what, compare_builds(m, a, b, n_boot=n_boot)))
    return out


def _smoke_drift(steps: pd.DataFrame) -> str:
    sm = steps[steps["plan"].str.startswith("smoke_wifi")].sort_values("t_start")
    runs = list(dict.fromkeys(sm["run_uid"]))
    if len(runs) < 2:
        return "<p>Only one 1 m smoke run; no start-versus-end check.</p>"
    first, last = sm[sm["run_uid"] == runs[0]], sm[sm["run_uid"] == runs[-1]]
    rows = []
    for ch in sorted(sm["channel"].unique()):
        a = first.loc[first["channel"] == ch, "rssi_median"].median()
        b = last.loc[last["channel"] == ch, "rssi_median"].median()
        rows.append({"channel": int(ch), "start": a, "end": b, "change": b - a,
                     "per_start": first.loc[first["channel"] == ch, "per_pct"].max(),
                     "per_end": last.loc[last["channel"] == ch, "per_pct"].max()})
    return ("<p>The same 1 m Wi-Fi smoke plan at the start and the end of the session. RSSI is an integer, so a "
            "change of 1 dB is one quantisation step.</p>"
            + _table(pd.DataFrame(rows), [("channel", "channel"), ("start", "RSSI median start (dBm)"),
                                          ("end", "RSSI median end (dBm)"), ("change", "change (dB)"),
                                          ("per_start", "PER start (%)"), ("per_end", "PER end (%)")]))


def _known_answers(rig_dir: Path, truth: dict) -> str:
    from radioyield.sim.rig import HallTruth, analyze_session

    t = HallTruth(seed=int(truth.get("seed", 0)))
    r = analyze_session(rig_dir, t)
    rows = []

    def add(name, est, lo, hi, tv, note=""):
        covers = "" if lo is None else ("yes" if lo <= tv <= hi else "no")
        rows.append({"quantity": name, "estimate": est, "lo": lo, "hi": hi, "truth": tv, "covers": covers, "note": note})

    if "path_loss" in r:
        p = r["path_loss"]
        add("path-loss exponent n", p["n"], p["lo"], p["hi"], p["truth"], "P.1238-13 corridor LoS")
        add("RSSI at 1 m, 20 dBm (dBm)", p["intercept"], None, None, p["truth_intercept"], "includes the REF RSSI bias")
    for k, v in r["tx_linearity"].items():
        add(f"TX linearity slope, {k}", v["slope"], v["lo"], v["hi"], 1.0)
    for k, v in r["waterfall"].items():
        if v.get("fitted", True):
            add(f"level at 10% PER, {k} (dBm)", v["level"], v["lo"], v["hi"], v["truth"],
                "CI is packet statistics only; chain errors are systematic" if "CONDUCTED" in k else "")
        else:
            rows.append({"quantity": f"level at 10% PER, {k}", "estimate": None, "lo": None, "hi": None,
                         "truth": v.get("truth"), "covers": "",
                         "note": f"not fitted: PER stayed {v['per_range_pct'][0]:g}-{v['per_range_pct'][1]:g}%"})
    for key, name in (("build_evt", "EVT2 - EVT1 median shift (dB)"), ("build_orient", "ORIENT_B - ORIENT_A (dB)")):
        if key in r:
            b = r[key]
            add(name, b["median_shift_db"], None, None, b["truth_median_shift_db"],
                f"{b['n_flagged']}/{b['n_tests']} tests flagged")
    if "unit_to_unit" in r:
        add("DUT-02 - DUT-01 TX (dB)", r["unit_to_unit"]["est_db"], None, None, r["unit_to_unit"]["truth_db"])
    df = pd.DataFrame(rows)
    return ('<h2 id="truth">Known-answer check (virtual rig only)</h2>'
            f"<p>Simulation seed {esc(truth.get('seed'))}. The REF's simulated RSSI bias is "
            f"{fmt(truth.get('rssi_bias_db'))} dB, so OTA levels are compared with what that RSSI would read. "
            "Across 30 seeds: <code>docs/results/sim_rig.md</code>.</p>"
            + '<div class="wrap-last">' + _table(df, [("quantity", "quantity"), ("estimate", "estimate"),
                                                      ("lo", "95% lo"), ("hi", "95% hi"), ("truth", "truth"),
                                                      ("covers", "CI covers truth"), ("note", "note")]) + "</div>")


def render_rig(rig_dir: str | Path, *, title: str | None = None, n_boot: int = 1000) -> str:
    rig_dir = Path(rig_dir)
    steps, packets = load_rig_dir(rig_dir)
    if steps.empty:
        raise ValueError(f"no *.steps.parquet under {rig_dir}")
    rf, notes = rf_from_rig(steps, packets)
    origins = sorted(steps["data_origin"].dropna().unique().tolist())
    origin = ",".join(origins)
    truth_path = rig_dir / "TRUTH.json"
    truth = json.loads(truth_path.read_text()) if truth_path.exists() else None
    simulated = "SIMULATED" in origin
    title = title or ("Virtual rig session (SIMULATED)" if simulated else "Rig session")

    inv = _inventory(steps, packets)
    nm = (VIRTUAL_RIG if simulated else []) + [i for k in ("RIG_OTA", "RIG_CONDUCTED") if k in origin
                                               for i in NOT_MEASURED[k]]
    nm += NOT_MEASURED["ALWAYS"] + notes
    banner = ('<div class="banner">SIMULATED RIG SESSION: real collector and analysis code, simulated boards and '
              'hallway. Not a measurement, not quotable as one.</div>' if simulated else _origin_banner(origin))

    comps = _comparisons(rig_dir, n_boot)
    comp_html = []
    for what, c in comps:
        comp_html.append(f"<p><b>What changed:</b> {esc(what)}. Tests: RSSI median per channel and power; "
                         f"{int(c.table['n_a'].max())} vs {int(c.table['n_b'].max())} repeats.</p>")
        comp_html.append(_section_compare(SimpleNamespace(comparison=c)))
    if not comps:
        comp_html.append('<h2 id="builds">Build over build</h2><p>No build_compare runs with both builds of a pair.</p>')

    nav = " · ".join(f'<a href="#{i}">{t}</a>' for i, t in [("runs", "Runs"), ("rf", "RF fits"), ("builds", "Builds"),
                                                             ("drift", "Drift")] + ([("truth", "Known answers")] if truth else []))
    body = "".join([
        f"<h1>{esc(title)}</h1>",
        f"<p>{len(inv)} runs · {len(steps):,} steps · {len(packets):,} packets logged · units "
        f"{esc(', '.join(sorted(steps['unit_serial'].dropna().unique())))} · generated "
        f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC</p>",
        banner, f"<p>{nav}</p>",
        '<div class="card nm"><h3 style="margin-top:0">What was not measured</h3><ul>'
        + "".join(f"<li>{esc(i)}</li>" for i in nm) + "</ul></div>",
        '<h2 id="runs">Runs</h2><div class="tiles">',
        _tile("runs", f"{len(inv)}"), _tile("steps", f"{len(steps):,}"), _tile("packets logged", f"{len(packets):,}"),
        _tile("steps with flags", f"{int((steps['flags'].fillna('') != '').sum())}"), "</div>",
        "<details><summary>Every run, in session order</summary>"
        + _table(inv, [("plan", "plan"), ("build", "build"), ("unit", "unit"), ("mode", "mode"),
                       ("position", "position"), ("distance_m", "m"), ("steps", "steps"), ("packets", "packets"),
                       ("per_min", "PER min %"), ("per_max", "PER max %"), ("flags", "flags")], max_rows=500)
        + "</details>",
        _section_rf(rf),
        *comp_html,
        '<h2 id="drift">Start versus end of session</h2>', _smoke_drift(steps),
        _known_answers(rig_dir, truth) if truth else "",
        "<p>Generated by RadioYield. Intervals are bootstraps with a fixed seed: the same data gives the same report.</p>",
    ])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
            f"<style>{CSS}.wrap-last td:last-child{{white-space:normal;text-align:left;min-width:220px}}</style></head><body><main>{body}</main></body></html>")


def write_rig_report(rig_dir, path, **kw) -> str:
    html = render_rig(rig_dir, **kw)
    Path(path).write_text(html, encoding="utf-8")
    return str(path)
