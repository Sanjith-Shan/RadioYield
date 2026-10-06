"""Tiny dependency-free SVG chart primitives for the self-contained report.

Every chart is drawn against CSS custom properties (``--series-1``, ``--grid``,
...) defined once in the page, so light and dark mode are one stylesheet swap.
Each mark carries a ``<title>`` so hovering shows its exact value. Charts use a
single y axis and thin marks; text is in ink tokens, never series colours.
"""
from __future__ import annotations

import html
import math

import numpy as np

W = 720


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def fmt(x, nd=3) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n/a"
    ax = abs(x)
    if ax != 0 and (ax >= 1e5 or ax < 1e-3):
        return f"{x:.2e}"
    return f"{x:.{nd}g}" if ax < 1 else f"{x:.{max(nd - 1 - int(math.log10(ax + 1e-12)), 0) + 1}f}".rstrip("0").rstrip(".")


def nice_ticks(lo, hi, n=5):
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi == lo:
        return [lo]
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= n:
            step *= m
            break
    start = math.ceil(lo / step) * step
    return [round(start + i * step, 10) for i in range(int((hi - start) / step + 1e-9) + 1)]


class Scale:
    def __init__(self, d0, d1, r0, r1, pad=0.05):
        if d1 == d0:
            d0, d1 = d0 - 1, d1 + 1
        p = (d1 - d0) * pad
        self.d0, self.d1, self.r0, self.r1 = d0 - p, d1 + p, r0, r1

    def __call__(self, v):
        return self.r0 + (np.asarray(v, float) - self.d0) / (self.d1 - self.d0) * (self.r1 - self.r0)


def _frame(h, body, label, desc):
    return (f'<figure class="chart"><svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(label)}" '
            f'preserveAspectRatio="xMidYMid meet">{body}</svg>'
            + (f'<figcaption>{esc(desc)}</figcaption>' if desc else "") + "</figure>")


def _xaxis(xs: Scale, y0, y1, label, ticks=None):
    out = []
    for t in ticks or nice_ticks(xs.d0, xs.d1):
        x = float(xs(t))
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y0}" y2="{y1}" class="grid"/>'
                   f'<text x="{x:.1f}" y="{y1 + 14}" class="tick" text-anchor="middle">{esc(fmt(t))}</text>')
    out.append(f'<text x="{(xs.r0 + xs.r1) / 2:.1f}" y="{y1 + 32}" class="axis-label" text-anchor="middle">{esc(label)}</text>')
    return "".join(out)


def _yaxis(ys: Scale, x0, x1, label, ticks=None):
    out = []
    for t in ticks or nice_ticks(ys.d0, ys.d1):
        y = float(ys(t))
        out.append(f'<line x1="{x0}" x2="{x1}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{x0 - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{esc(fmt(t))}</text>')
    out.append(f'<text transform="translate(14 {(ys.r0 + ys.r1) / 2:.1f}) rotate(-90)" class="axis-label" '
               f'text-anchor="middle">{esc(label)}</text>')
    return "".join(out)


def hbar(items, xlabel, *, label="bar chart", desc=""):
    """items: [(label, value, tooltip)] -> horizontal bars, largest first, labelled with values."""
    if not items:
        return ""
    row = 26
    left = 230
    h = 40 + row * len(items) + 40
    vmax = max(v for _, v, _ in items) or 1
    xs = Scale(0, vmax, left, W - 60, pad=0.0)
    xs.d0 = 0
    body = [_xaxis(xs, 20, 20 + row * len(items), xlabel)]
    for i, (lab, v, tip) in enumerate(items):
        y = 24 + i * row
        w = max(float(xs(v)) - left, 1.5)
        body.append(f'<g class="mark"><title>{esc(tip)}</title>'
                    f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{row - 8}" rx="4" class="s1"/>'
                    f'<rect x="{left - 4}" y="{y - 2}" width="{W - left}" height="{row - 4}" fill="transparent"/></g>'
                    f'<text x="{left - 8}" y="{y + 13}" class="label" text-anchor="end">{esc(lab)}</text>'
                    f'<text x="{left + w + 6:.1f}" y="{y + 13}" class="value">{esc(fmt(v))}</text>')
    return _frame(h, "".join(body), label, desc)


def intervals(rows, xlabel, *, refs=(), band=None, label="interval chart", desc=""):
    """Forest plot. rows: [(label, est, lo, hi, flagged, tooltip)].

    refs: [(x, text)] vertical reference lines; band: (lo, hi) shaded tolerance band.
    Flagged rows are drawn in the status-critical colour *and* get a text marker.
    """
    rows = [r for r in rows if r[1] is not None and math.isfinite(r[1])]
    if not rows:
        return ""
    row = 22
    left = 250
    h = 30 + row * len(rows) + 44
    vals = [v for r in rows for v in r[1:4] if v is not None and math.isfinite(v)]
    vals += [x for x, _ in refs] + (list(band) if band else [])
    any_flag = any(r[4] for r in rows)
    xs = Scale(min(vals), max(vals), left, W - (100 if any_flag else 20))
    y0, y1 = 18, 18 + row * len(rows)
    body = [_xaxis(xs, y0, y1, xlabel)]
    if band:
        bx0, bx1 = float(xs(band[0])), float(xs(band[1]))
        body.append(f'<rect x="{bx0:.1f}" y="{y0}" width="{bx1 - bx0:.1f}" height="{y1 - y0}" class="band"/>')
    for x, t in refs:
        xx = float(xs(x))
        body.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{y0 - 6}" y2="{y1}" class="ref"/>'
                    f'<text x="{xx + 3:.1f}" y="{y0 - 8}" class="tick">{esc(t)}</text>')
    for i, (lab, est, lo, hi, flagged, tip) in enumerate(rows):
        y = y0 + row * i + row / 2
        cls = "crit" if flagged else "s1"
        seg = ""
        if lo is not None and hi is not None and math.isfinite(lo) and math.isfinite(hi):
            seg = (f'<line x1="{float(xs(lo)):.1f}" x2="{float(xs(hi)):.1f}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'class="ci {cls}-stroke"/>')
        body.append(f'<g class="mark"><title>{esc(tip)}</title>{seg}'
                    f'<circle cx="{float(xs(est)):.1f}" cy="{y:.1f}" r="4.5" class="{cls} ring"/>'
                    f'<rect x="{left}" y="{y - row / 2:.1f}" width="{W - left}" height="{row}" fill="transparent"/></g>'
                    f'<text x="{left - 8}" y="{y + 4:.1f}" class="label" text-anchor="end">{esc(lab)}</text>')
        if flagged:
            body.append(f'<text x="{W - 4}" y="{y + 4:.1f}" class="flagtext" text-anchor="end">&#9650; flagged</text>')
    return _frame(h, "".join(body), label, desc)


def histogram(values, xlabel, *, vlines=(), bins=40, highlight=None, label="histogram", desc=""):
    """vlines: [(x, text, cls)] with cls in {"limit", "pat", "ref"}; highlight: values drawn as dots above."""
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return ""
    xs_all = list(v) + [x for x, _, _ in vlines if math.isfinite(x)]
    lo, hi = min(xs_all), max(xs_all)
    counts, edges = np.histogram(v, bins=bins, range=(min(v.min(), lo), max(v.max(), hi)))
    h = 240
    left, top, bottom = 56, 26, 190
    xs = Scale(edges[0], edges[-1], left, W - 20, pad=0.02)
    ys = Scale(0, counts.max(), bottom, top, pad=0.0)
    ys.d0 = 0
    body = [_yaxis(ys, left, W - 20, "units"), _xaxis(xs, top, bottom, xlabel)]
    for c, e0, e1 in zip(counts, edges[:-1], edges[1:]):
        if c == 0:
            continue
        x0, x1 = float(xs(e0)) + 1, float(xs(e1)) - 1
        y = float(ys(c))
        body.append(f'<g class="mark"><title>{esc(f"{fmt(e0)} to {fmt(e1)}: {int(c)} units")}</title>'
                    f'<rect x="{x0:.1f}" y="{y:.1f}" width="{max(x1 - x0, 1):.1f}" height="{bottom - y:.1f}" class="s1"/></g>')
    for i, (x, t, cls) in enumerate(vlines):
        if not math.isfinite(x):
            continue
        xx = float(xs(x))
        body.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{top - 4}" y2="{bottom}" class="vl-{cls}"/>'
                    f'<text x="{xx:.1f}" y="{top - 8 - (i % 2) * 10}" class="tick" text-anchor="middle">{esc(t)}</text>')
    if highlight is not None:
        for x in np.asarray(highlight, float):
            body.append(f'<g class="mark"><title>{esc(f"outlier unit: {fmt(x)}")}</title>'
                        f'<circle cx="{float(xs(x)):.1f}" cy="{bottom - 8}" r="4.5" class="crit ring"/></g>')
    return _frame(h, "".join(body), label, desc)


def lines(series, xlabel, ylabel, *, hlines=(), vlines=(), points=None, label="line chart", desc=""):
    """series: [(name, xs, ys, cls)] drawn as 2px lines; points: [(name, xs, ys, cls)] as dots.
    hlines/vlines: [(value, text)]. A legend is drawn when there are >= 2 series."""
    allx = [x for s in list(series) + list(points or []) for x in np.asarray(s[1], float)]
    ally = [y for s in list(series) + list(points or []) for y in np.asarray(s[2], float)]
    ally += [y for y, _ in hlines]
    allx += [x for x, _ in vlines]
    allx = [x for x in allx if math.isfinite(x)]
    ally = [y for y in ally if math.isfinite(y)]
    if not allx:
        return ""
    h = 260
    left, top, bottom = 60, 30, 200
    xs = Scale(min(allx), max(allx), left, W - 20, pad=0.02)
    ys = Scale(min(ally), max(ally), bottom, top)
    body = [_yaxis(ys, left, W - 20, ylabel), _xaxis(xs, top, bottom, xlabel)]
    for y, t in hlines:
        yy = float(ys(y))
        body.append(f'<line x1="{left}" x2="{W - 20}" y1="{yy:.1f}" y2="{yy:.1f}" class="ref"/>'
                    f'<text x="{W - 22}" y="{yy - 4:.1f}" class="tick" text-anchor="end">{esc(t)}</text>')
    for x, t in vlines:
        xx = float(xs(x))
        body.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{top}" y2="{bottom}" class="ref"/>'
                    f'<text x="{xx + 4:.1f}" y="{top + 10}" class="tick">{esc(t)}</text>')
    legend = []
    for name, x, y, cls in series:
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        if x.size > 1500:   # decimate for file size; keeps shape at this width
            idx = np.linspace(0, x.size - 1, 1500).astype(int)
            x, y = x[idx], y[idx]
        pts = " ".join(f"{float(a):.1f},{float(b):.1f}" for a, b in zip(xs(x), ys(y)))
        body.append(f'<polyline points="{pts}" class="line {cls}-stroke"><title>{esc(name)}</title></polyline>')
        legend.append((name, cls))
    for name, x, y, cls in points or []:
        for a, b in zip(np.asarray(x, float), np.asarray(y, float)):
            body.append(f'<g class="mark"><title>{esc(f"{name}: {fmt(a)}, {fmt(b)}")}</title>'
                        f'<circle cx="{float(xs(a)):.1f}" cy="{float(ys(b)):.1f}" r="4" class="{cls} ring"/></g>')
        legend.append((name, cls))
    if len(legend) >= 2:
        lx = left + 4
        for name, cls in legend:
            body.append(f'<rect x="{lx}" y="6" width="12" height="4" rx="2" class="{cls}"/>'
                        f'<text x="{lx + 16}" y="12" class="tick">{esc(name)}</text>')
            lx += 24 + 7 * len(name)
    return _frame(h, "".join(body), label, desc)
