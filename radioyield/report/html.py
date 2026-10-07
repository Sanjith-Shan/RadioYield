"""Self-contained HTML report: one file per lot (with a build comparison when
the lot holds two builds). No external assets, no JavaScript dependencies.

Same no-lies rules as Dyno: every number shows its interval or its refusal
reason, data origin is printed at the top and cannot be removed, and a "what was
not measured" box lists what this data cannot speak to.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from radioyield.analysis.lot import LotAnalysis
from . import svg
from .svg import esc, fmt

NOT_MEASURED = {
    "SIMULATED": [
        "Nothing in this report is a measurement. Every value comes from radioyield.sim with injected, known faults, "
        "and exists to score the detectors.",
        "Distribution shapes, spreads and most limits are assumptions (see radioyield/sim/params.py for the source of each).",
    ],
    "RIG_OTA": [
        "Over-the-air only: every level includes both antennas, the room and the fixture geometry. "
        "Not comparable with conducted datasheet sensitivity.",
        "EVM, spectral mask, ACLR and frequency error: need a vector signal analyser / RF tester.",
        "TRP / TIS: need an anechoic chamber; a hallway is not one.",
        "RSSI is the receiving radio's own estimate (integer dBm, uncalibrated), not a power meter.",
    ],
    "RIG_CONDUCTED": [
        "Conducted through a pigtail and fixed attenuators: attenuator tolerance and cable loss (as recorded in the "
        "run metadata) set the absolute accuracy.",
        "EVM, spectral mask, ACLR: need a vector signal analyser / RF tester.",
        "TRP / TIS: need an anechoic chamber.",
    ],
    "ALWAYS": [
        "NFC: out of scope; no NFC hardware or data.",
        "Cellular: out of scope.",
    ],
}

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;
--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--band:rgba(42,120,214,.10);--banner:#fff3d6;--banner-ink:#5a3c00}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;
--s3:#199e70;--s4:#c98500;--band:rgba(57,135,229,.16);--banner:#3a2c08;--banner-ink:#ffe2a3}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;
--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;
--band:rgba(57,135,229,.16);--banner:#3a2c08;--banner-ink:#ffe2a3}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:8px 0 4px}h2{font-size:19px;margin:36px 0 8px;padding-top:12px;border-top:1px solid var(--border)}
h3{font-size:15px;margin:18px 0 6px}p,li{color:var(--ink2);max-width:72ch}
.banner{background:var(--banner);color:var(--banner-ink);border-radius:8px;padding:10px 14px;font-weight:600;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:12px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px}
.tile .v{font-size:26px;font-weight:650}.tile .k{color:var(--ink2);font-size:13px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin:10px 0}
.nm{border-left:4px solid var(--warn)}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:5px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th{color:var(--ink2);font-weight:600}td:first-child,th:first-child,td.l,th.l{text-align:left}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--border);border-radius:10px;margin:8px 0}
.flag{color:var(--crit);font-weight:650}.ok{color:var(--ink2)}
.figure-row{display:grid;gap:12px}
figure.chart{margin:8px 0;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:8px}
figure.chart svg{width:100%;height:auto;display:block}
figcaption{color:var(--ink2);font-size:13px;padding:4px 6px}
svg text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.grid{stroke:var(--grid);stroke-width:1}.ref{stroke:var(--axis);stroke-width:1.5;stroke-dasharray:4 3}
.tick{fill:var(--muted);font-size:11px}.axis-label{fill:var(--ink2);font-size:12px}
.label{fill:var(--ink2);font-size:12px}.value{fill:var(--ink);font-size:12px}
.flagtext{fill:var(--crit);font-size:11px;font-weight:600}
.s1{fill:var(--s1)}.s2{fill:var(--s2)}.s3{fill:var(--s3)}.crit{fill:var(--crit)}
.s1-stroke{stroke:var(--s1)}.s2-stroke{stroke:var(--s2)}.s3-stroke{stroke:var(--s3)}.crit-stroke{stroke:var(--crit)}
.ring{stroke:var(--surface);stroke-width:2}.ci{stroke-width:2;stroke-linecap:round}
.line{fill:none;stroke-width:2}.band{fill:var(--band)}
.vl-limit{stroke:var(--crit);stroke-width:1.5}.vl-pat{stroke:var(--warn);stroke-width:1.5;stroke-dasharray:5 3}
.vl-ref{stroke:var(--axis);stroke-width:1.5;stroke-dasharray:4 3}
.mark:hover rect,.mark:hover circle{opacity:.8}
details summary{cursor:pointer;color:var(--ink2);font-size:13px;margin:4px 0}
code{font-size:13px}
"""


def _table(df: pd.DataFrame, cols: list[tuple[str, str]], *, flag_col: str | None = None, max_rows: int = 200) -> str:
    if df is None or df.empty:
        return '<p class="ok">No rows.</p>'
    head = "".join(f'<th class="{"l" if i == 0 else ""}">{esc(h)}</th>' for i, (_, h) in enumerate(cols))
    body = []
    for _, r in df.head(max_rows).iterrows():
        cells = []
        for c, _ in cols:
            v = r.get(c)
            if isinstance(v, (bool, np.bool_)):
                txt = "yes" if v else "no"
            elif isinstance(v, (float, np.floating)):
                txt = fmt(float(v))
            else:
                txt = "" if v is None else str(v)
            cls = "flag" if flag_col and c == flag_col and bool(r.get(flag_col)) else ""
            cells.append(f'<td class="{cls}">{esc(txt)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    more = f'<p class="ok">{len(df) - max_rows} more rows not shown.</p>' if len(df) > max_rows else ""
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>{more}'


def _tile(k, v):
    return f'<div class="tile"><div class="v">{esc(v)}</div><div class="k">{esc(k)}</div></div>'


def _pct(x):
    return "n/a" if x is None or not math.isfinite(x) else f"{100 * x:.2f}%"


def _origin_banner(origin: str) -> str:
    if "SIMULATED" in origin:
        return ('<div class="banner">SIMULATED DATA: synthetic lot with injected faults. '
                'Not a measurement, not quotable as one.</div>')
    if "RIG_OTA" in origin:
        return '<div class="banner">MEASURED, OVER THE AIR (ESP32-C6 rig). Levels include antennas and the room.</div>'
    if "RIG_CONDUCTED" in origin:
        return '<div class="banner">MEASURED, CONDUCTED (ESP32-C6 rig, cable + fixed attenuators).</div>'
    return f'<div class="banner">Data origin: {esc(origin)}</div>'


def _section_yield(a: LotAnalysis) -> str:
    y = a.yields
    out = ['<h2 id="yield">Yield</h2>', '<div class="tiles">',
           _tile("first-pass yield", _pct(y.first_pass_yield)), _tile("final yield (after retest)", _pct(y.final_yield)),
           _tile("units", f"{y.n_units:,}"), _tile("insertions", f"{y.n_insertions:,}"),
           _tile("units retested", f"{y.retested_units:,}"), _tile("recovered by retest", f"{y.recovered_by_retest:,}"),
           "</div>"]
    out += [f'<p class="flag">{esc(n)}</p>' for n in y.notes]
    if len(y.pareto_by_test):
        items = [(r.test_name, int(r.fails), f"{r.test_name}: {int(r.fails)} first-pass failures "
                  f"({r.pct_of_fail_events:.1f}% of failure events, cumulative {r.cum_pct:.1f}%)")
                 for r in y.pareto_by_test.itertuples()]
        out.append("<h3>Failure Pareto by test (first insertion)</h3>")
        out.append(svg.hbar(items, "first-pass failures", label="Failure Pareto by test",
                            desc="A unit failing several tests counts once per test."))
    if len(y.pareto_by_bin):
        out.append("<h3>Failing units by soft bin</h3>")
        out.append(_table(y.pareto_by_bin, [("soft_bin", "soft bin"), ("units", "units"), ("pct", "%"), ("cum_pct", "cum %")]))
    out.append("<details><summary>Yield by test</summary>")
    out.append(_table(y.by_test, [("test_name", "test"), ("n", "n"), ("n_fail", "fails"), ("yield", "yield")]))
    out.append("</details>")
    return "".join(out)


def _section_capability(a: LotAnalysis) -> str:
    c = a.capability
    rep = c[c["status"] == "reported"]
    rows = [(r.test_name, r.cpk, r.cpk_low, r.cpk_high, r.cpk_low < 1.33,
             f"{r.test_name}: Cpk {fmt(r.cpk)} [{fmt(r.cpk_low)}, {fmt(r.cpk_high)}], n={r.n}") for r in rep.itertuples()]
    out = ['<h2 id="cpk">Capability (Cpk, 95% bootstrap interval)</h2>',
           "<p>First insertion, product units only. Flagged when the interval's lower end is below 1.33. "
           "Refused tests are listed with the reason: a Cpk on quantized, skewed or mixed data would be a "
           "number without meaning.</p>",
           svg.intervals(rows, "Cpk", refs=[(1.0, "1.0"), (1.33, "1.33")], label="Cpk by test")]
    ref = c[c["status"] == "refused"]
    if len(ref):
        out.append("<h3>Refused</h3>")
        out.append(_table(ref, [("test_name", "test"), ("n", "n"), ("reason", "reason")]))
    out.append("<details><summary>Full capability table</summary>")
    out.append(_table(c, [("test_name", "test"), ("units", "units"), ("n", "n"), ("mean", "mean"), ("sigma", "sigma"),
                          ("lo_limit", "LSL"), ("hi_limit", "USL"), ("cp", "Cp"), ("cpk", "Cpk"),
                          ("cpk_low", "Cpk lo"), ("cpk_high", "Cpk hi"), ("status", "status")]))
    out.append("</details>")
    bm = a.bimodality[a.bimodality["bimodal"]] if len(a.bimodality) else a.bimodality
    if len(bm):
        out.append("<h3>Bimodal tests (mixed populations)</h3>")
        df = a.frame
        out.append(_table(bm.assign(attribution=[_attribute(df, r.test_name, r.mode_a, r.mode_b) for r in bm.itertuples()]),
                          [("test_name", "test"), ("mode_a", "mode A"), ("mode_b", "mode B"), ("minor_weight", "minor share"),
                           ("ashman_d", "Ashman D"), ("attribution", "explained by")]))
        for r in list(bm.itertuples())[:3]:
            v = df[(df["test_name"] == r.test_name) & ~df["retest"] & ~df["part_id"].str.startswith("GU-")]["result"]
            lim = df[df["test_name"] == r.test_name][["lo_limit", "hi_limit"]].iloc[0]
            out.append(svg.histogram(v, r.test_name, vlines=[(lim.lo_limit, "LSL", "limit"), (lim.hi_limit, "USL", "limit")],
                                     label=f"{r.test_name} distribution",
                                     desc=f"Two populations near {fmt(r.mode_a)} and {fmt(r.mode_b)} "
                                          f"(Ashman D {fmt(r.ashman_d)}, minor mode {100 * r.minor_weight:.0f}%). "
                                          "Most units may still pass: a bimodal lot is a process signal, not a yield one."))
    return "".join(out)


def _attribute(df, test, mode_a, mode_b) -> str:
    """Name the station or build whose units make up the minor mode, if one does."""
    d = df[(df["test_name"] == test) & ~df["retest"] & ~df["part_id"].str.startswith("GU-")]
    mid = (mode_a + mode_b) / 2
    hi_side = d["result"] > mid
    minor_hi = hi_side.mean() < 0.5
    in_minor = hi_side if minor_hi else ~hi_side
    for col in ("station", "build"):
        share = in_minor.groupby(d[col]).mean()
        if len(share) < 2:
            continue
        top = share.idxmax()
        rest = share.drop(top)
        if share[top] > 0.8 and rest.max() < 0.1:
            return f"{col} {top}: {share[top]:.0%} of its units are in the minor mode, others <= {rest.max():.0%}"
    return "no single station or build; look at the units (e.g. a component lot)"


def _section_compare(a: LotAnalysis) -> str:
    if a.comparison is None:
        return ('<h2 id="builds">Build over build</h2><p>Only one build in this data; nothing to compare.</p>')
    t = a.comparison.table
    ok = t[t["verdict"] != "insufficient data"]
    rows = []
    for r in ok.itertuples():
        sc = r.shift / r.shift_sigmas if r.shift_sigmas and math.isfinite(r.shift_sigmas) and r.shift_sigmas != 0 else None
        if sc is None or sc == 0:
            pooled = math.sqrt((r.robust_sigma_a ** 2 + r.robust_sigma_b ** 2) / 2)
            sc = pooled if pooled > 0 else None
        if not sc:
            continue
        rows.append((r.test_name, r.shift / sc, r.shift_low / sc, r.shift_high / sc, r.verdict == "shift",
                     f"{r.test_name}: shift {fmt(r.shift)} {r.units} [{fmt(r.shift_low)}, {fmt(r.shift_high)}] "
                     f"= {fmt(r.shift / sc)} robust sigma; Holm p={fmt(r.p_location_holm)}; verdict: {r.verdict}"))
    shifted = a.comparison.shifted
    summary = (f"Detected a location shift on {len(shifted)} test(s): {', '.join(shifted)}." if shifted
               else "No detectable change on any test.")
    return "".join([
        f'<h2 id="builds">Build over build: {esc(a.comparison.build_b)} vs {esc(a.comparison.build_a)}</h2>',
        f"<p>{esc(summary)} Median shift (B minus A) with a 95% bootstrap interval; Mann-Whitney U with Holm "
        "correction across tests. The chart puts every test on one scale by dividing by the test's pooled robust "
        "sigma; the table keeps the test's own units.</p>",
        svg.intervals(rows, "shift in median (robust sigmas of the test)", refs=[(0.0, "0")], label="Build shift by test"),
        _table(t, [("test_name", "test"), ("units", "units"), ("n_a", "n A"), ("n_b", "n B"), ("shift", "shift"),
                   ("shift_low", "lo"), ("shift_high", "hi"), ("cliffs_delta", "Cliff's d"),
                   ("p_location_holm", "p (Holm)"), ("sigma_ratio", "sigma B/A"), ("spread_change", "spread change"),
                   ("verdict", "verdict")])])


def _section_station(a: LotAnalysis) -> str:
    out = ['<h2 id="stations">Station correlation</h2>']
    g = a.golden
    if g is None or g.empty:
        out.append("<p>No golden-unit data (part_id GU-*) in this lot; golden correlation not possible.</p>")
    else:
        flagged = g[g["flag"]]
        out.append(f"<p>Golden units run on every station, compared with the consensus reference. "
                   f"{len(flagged)} station/test pair(s) flagged. Shaded band: tolerance (10% of the spec window).</p>")
        for st, gs in g.groupby("station"):
            pass
        # one forest chart over all (station, test) pairs for TX-power-like tests in dBm keeps a single unit
        for units, gu in g.groupby("units"):
            rows = [(f"{r.station} {r.test_name}", r.offset, r.offset_low, r.offset_high, bool(r.flag),
                     f"{r.station} {r.test_name}: offset {fmt(r.offset)} {units} [{fmt(r.offset_low)}, "
                     f"{fmt(r.offset_high)}], tolerance {fmt(r.tolerance)}") for r in gu.itertuples()]
            tol = gu["tolerance"].dropna()
            band = (-float(tol.min()), float(tol.min())) if len(tol) and tol.nunique() == 1 else None
            if len(rows) <= 40:
                out.append(svg.intervals(rows, f"offset vs consensus ({units})", refs=[(0.0, "0")], band=band,
                                         label=f"Station offsets in {units}"))
        out.append("<details><summary>Golden correlation table</summary>")
        out.append(_table(g, [("station", "station"), ("test_name", "test"), ("n_readings", "readings"),
                              ("offset", "offset"), ("offset_low", "lo"), ("offset_high", "hi"), ("tolerance", "tol"),
                              ("slope", "slope"), ("slope_status", "slope status"), ("flag", "flag")], flag_col="flag"))
        out.append("</details>")
    p = a.station_production
    if p is not None and len(p):
        out.append("<h3>Production cross-check (no golden units needed)</h3>")
        out.append("<p>Each station's product median against the other stations pooled (Mann-Whitney, Holm). Valid "
                   "only if units reach stations without regard to their quality.</p>")
        out.append(_table(p[p["flag"]] if p["flag"].any() else p.head(0),
                          [("station", "station"), ("test_name", "test"), ("delta_median", "delta median"),
                           ("p_holm", "p (Holm)")]) if p["flag"].any() else '<p class="ok">No station flagged.</p>')
    gr = a.gauge_rr
    if gr is not None and len(gr):
        out.append("<h3>Gauge R&amp;R (ANOVA, golden units x stations x repeats)</h3>")
        out.append(_table(gr, [("test_name", "test"), ("n_parts", "parts"), ("n_stations", "stations"),
                               ("n_repeats", "repeats"), ("repeatability_sd", "EV sd"), ("reproducibility_sd", "AV sd"),
                               ("part_sd", "PV sd"), ("pct_grr_tv", "%GRR TV"), ("pct_grr_tol", "%GRR tol"),
                               ("ndc", "ndc"), ("verdict", "verdict")]))
        out.append("<p>Five golden units near nominal give little part variation, so %TV and ndc understate the "
                   "gauge; %tolerance is the meaningful column here.</p>")
    return "".join(out)


def _section_drift(a: LotAnalysis) -> str:
    d = a.drift
    out = ['<h2 id="drift">Drift (CUSUM per station and test)</h2>']
    if d is None or d.empty:
        return out[0] + "<p>No streams.</p>"
    charted = d[d["status"] == "charted"]
    al = charted[charted["alarm"]]
    h = charted["h"].iloc[0] if len(charted) and "h" in charted else float("nan")
    src = charted["h_source"].iloc[0] if len(charted) and "h_source" in charted else ""
    out.append(f"<p>{len(charted)} charts, {len(al)} alarm(s). k = 0.5 sigma, h = {fmt(h)} ({esc(src)}). "
               "The first 150 readings per stream set the in-control mean and sigma.</p>")
    for r in al.head(6).itertuples():
        tr = r.trace
        t0 = tr["ts"][0]
        hrs = (np.asarray(tr["ts"]) - t0) / 3600
        alarm_h = (r.alarm_ts - t0) / 3600
        out.append(svg.lines([("CUSUM high side", hrs, tr["s1"], "s1"), ("CUSUM low side", hrs, tr["s2"], "s2")],
                             "hours since charting started", "CUSUM (sigmas)",
                             hlines=[(h, f"h = {fmt(h)}")], vlines=[(alarm_h, "alarm")],
                             label=f"CUSUM {r.station} {r.test_name}",
                             desc=f"{r.station} {r.test_name}: alarm ({r.direction}) after {alarm_h:.2f} h of charting."))
    if len(al) > 6:
        out.append(f"<p>{len(al) - 6} further alarm chart(s) omitted; see the table.</p>")
    skipped = d[d["status"] != "charted"][["test_name", "status"]].drop_duplicates("test_name")
    if len(skipped):
        out.append("<h3>Not charted</h3>" + _table(skipped, [("test_name", "test"), ("status", "reason")]))
    out.append("<details><summary>All charts</summary>")
    out.append(_table(d.drop(columns=[c for c in ("trace",) if c in d]),
                      [("station", "station"), ("test_name", "test"), ("status", "status"), ("charted", "readings"),
                       ("baseline_mu", "mu"), ("baseline_sigma", "sigma"), ("alarm", "alarm"), ("direction", "dir")],
                      flag_col="alarm"))
    out.append("</details>")
    return "".join(out)


def _section_pat(a: LotAnalysis) -> str:
    p = a.pat
    out = ['<h2 id="pat">Outlier units (dynamic PAT, AEC-Q001)</h2>',
           f"<p>{len(p.outlier_units)} unit(s) pass every spec limit but sit outside median +/- 6 robust sigma "
           "((Q3-Q1)/1.35) of this lot on at least one test. These are the units to quarantine or screen harder.</p>"]
    if len(p.outliers):
        out.append(_table(p.outliers, [("part_id", "unit"), ("station", "station"), ("test_name", "test"),
                                       ("result", "result"), ("pat_lo", "PAT lo"), ("pat_hi", "PAT hi"),
                                       ("robust_sigmas", "robust sigmas")]))
        df = a.frame
        for test in p.outliers["test_name"].unique()[:4]:
            v = df[(df["test_name"] == test) & ~df["retest"] & ~df["part_id"].str.startswith("GU-")]
            lim = p.limits[p.limits["test_name"] == test].iloc[0]
            o = p.outliers[p.outliers["test_name"] == test]["result"]
            out.append(svg.histogram(v["result"], test, highlight=o,
                                     vlines=[(v["lo_limit"].iloc[0], "LSL", "limit"), (v["hi_limit"].iloc[0], "USL", "limit"),
                                             (lim.pat_lo, "PAT lo", "pat"), (lim.pat_hi, "PAT hi", "pat")],
                                     label=f"{test} with PAT limits",
                                     desc="Solid: spec limits. Dashed: dynamic PAT limits. Dots: PAT outlier units."))
    sk = p.limits[p.limits["status"] != "ok"] if len(p.limits) else p.limits
    if len(sk):
        out.append("<h3>Tests not screened</h3>" + _table(sk, [("test_name", "test"), ("status", "reason")]))
    return "".join(out)


def _section_rf(rf: dict | None) -> str:
    out = ['<h2 id="rf">RF fits</h2>']
    if not rf:
        out.append("<p>No rig data in this report. PER waterfall, path loss and TX power linearity come from the "
                   "ESP32-C6 rig (milestone M2) and are not produced from simulated lots.</p>")
        return "".join(out)
    wf = rf.get("waterfall")
    for name, w in (wf or {}).items():
        lv = np.asarray(w["level"])
        curve_x = np.linspace(lv.min(), lv.max(), 120)
        fit = w["fit"]
        out.append(svg.lines([("logistic fit", curve_x, 100 * fit.per(curve_x), "s1")], "received level (dBm)", "PER (%)",
                             points=[("measured", lv, 100 * np.asarray(w["per"]), "s2")],
                             hlines=[(100 * fit.target_per, f"{100 * fit.target_per:.0f}% PER")],
                             label=f"PER waterfall {name}",
                             desc=f"{name}: level at {100 * fit.target_per:.0f}% PER {fmt(fit.level_at_target.estimate)} dBm "
                                  f"[{fmt(fit.level_at_target.low)}, {fmt(fit.level_at_target.high)}] "
                                  f"({fit.level_at_target.method}). {' '.join(fit.notes)}"))
    pl = rf.get("path_loss")
    if pl:
        f = pl["fit"]
        d = np.asarray(pl["distance"])
        xs = np.linspace(d.min(), d.max(), 100)
        out.append(svg.lines([("log-distance fit", xs, f.intercept_dbm - 10 * f.exponent.estimate * np.log10(xs), "s1")],
                             "distance (m)", "median RSSI (dBm)",
                             points=[("per-position median", pl["positions"], pl["medians"], "s2")],
                             label="Path loss",
                             desc=f"Exponent n = {fmt(f.exponent.estimate)} [{fmt(f.exponent.low)}, {fmt(f.exponent.high)}], "
                                  f"shadowing sigma {fmt(f.shadowing_sigma_db)} dB over {f.n_positions} positions. "
                                  "Nearest references: " + "; ".join(f"{k} n={fmt(v)}{' (inside CI)' if inside else ''}"
                                                                     for k, v, inside in f.closest)))
    tx = rf.get("tx_linearity")
    for name, t in (tx or {}).items():
        f = t["fit"]
        c = np.asarray(t["commanded"])
        out.append(svg.lines([("ideal slope 1 through fit", c, f.intercept + c, "s3")], "commanded TX power (dBm)",
                             "median RSSI at REF (dBm)", points=[("measured", c, t["measured"], "s1")],
                             label=f"TX linearity {name}",
                             desc=f"{name}: slope {fmt(f.slope.estimate)} dB/dB [{fmt(f.slope.low)}, {fmt(f.slope.high)}], "
                                  f"max |residual| {fmt(f.max_abs_residual_db)} dB. {' '.join(f.notes)}"))
    return "".join(out)


def _not_measured(origin: str, extra: list[str] | None) -> str:
    items = []
    for key in ("SIMULATED", "RIG_OTA", "RIG_CONDUCTED"):
        if key in origin:
            items += NOT_MEASURED[key]
    items += NOT_MEASURED["ALWAYS"] + list(extra or [])
    return ('<div class="card nm"><h3 style="margin-top:0">What was not measured</h3><ul>'
            + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul></div>")


def render_lot(a: LotAnalysis, *, title: str | None = None, rf: dict | None = None,
               not_measured: list[str] | None = None) -> str:
    origin = str(a.meta.get("data_origin", "UNKNOWN"))
    lot = a.meta.get("lot_id", "lot")
    title = title or f"RadioYield lot report {lot}"
    meta_rows = "".join(f"<tr><td class='l'>{esc(k)}</td><td class='l'>{esc(v)}</td></tr>" for k, v in sorted(a.meta.items()))
    stations = sorted(a.frame["station"].unique().tolist())
    nav = " · ".join(f'<a href="#{i}">{t}</a>' for i, t in [("yield", "Yield"), ("cpk", "Cpk"), ("builds", "Builds"),
                                                             ("stations", "Stations"), ("drift", "Drift"), ("pat", "PAT"),
                                                             ("rf", "RF fits")])
    body = "".join([
        f"<h1>{esc(title)}</h1>",
        f"<p>Lot <b>{esc(lot)}</b> · {len(stations)} station(s): {esc(', '.join(stations))} · builds: "
        f"{esc(', '.join(a.builds) or 'n/a')} · generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC</p>",
        _origin_banner(origin),
        f"<p>{nav}</p>",
        _not_measured(origin, not_measured),
        _section_yield(a), _section_capability(a), _section_compare(a), _section_station(a),
        _section_drift(a), _section_pat(a), _section_rf(rf),
        "<h2>Run metadata</h2>",
        f'<div class="scroll"><table><tbody>{meta_rows}</tbody></table></div>',
        "<p>Generated by RadioYield. Intervals are percentile bootstraps with a fixed seed: the same data gives the "
        "same report.</p>",
    ])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
            f"<style>{CSS}</style></head><body><main>{body}</main></body></html>")


def write_lot_report(a: LotAnalysis, path, **kw) -> str:
    html = render_lot(a, **kw)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return str(path)
