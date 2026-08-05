"""Server-rendered SVG chart primitives.

Charts are generated as SVG strings in Python rather than by a JS charting
library. Three reasons, in order of weight:

1. No CDN, no build step. The dashboard renders with the venv alone, which
   matters for a project whose defining constraint is that everything must
   work without external accounts or downloads.
2. Colour is expressed as CSS custom properties, so light/dark theming is a
   variable swap rather than a re-render.
3. The chart and its table-view twin are produced from the same call, so the
   accessible equivalent cannot drift from the picture.

Mark conventions follow the data-viz method: 2px lines, hairline solid grid
(never dashed), 4px rounded bar ends anchored to the baseline, a 2px surface
gap between adjacent fills, legend whenever there are two or more series, and
selective direct labels rather than a value on every point.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from fxpit.web.provenance import TableView

# Categorical slots 1-3 of the validated palette. Validated all-pairs in both
# modes (worst CVD dE 9.2 light / 9.4 dark; normal-vision 24.0 / 20.9).
# Slots are referenced through CSS variables so the dark steps swap in CSS.
SERIES_VARS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"]

# Past three series the palette no longer clears the all-pairs floor, so the
# fourth slot would put yellow beside orange. Fold the tail into "Other"
# instead of generating a hue.
MAX_SERIES = len(SERIES_VARS)


def _esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _fmt(v: float, places: int = 2) -> str:
    return f"{v:,.{places}f}"


@dataclass
class Chart:
    """An SVG figure and its mandatory table twin."""

    svg: str
    table: TableView


def _grid(x0: int, y0: int, w: int, h: int, ticks: list[tuple[float, str]]) -> str:
    """Horizontal hairline gridlines with left-margin value labels."""
    out = []
    for frac, label in ticks:
        y = y0 + h - frac * h
        out.append(
            f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + w}" y2="{y:.1f}" '
            f'class="grid" />'
        )
        out.append(
            f'<text x="{x0 - 8}" y="{y + 4:.1f}" class="tick" '
            f'text-anchor="end">{_esc(label)}</text>'
        )
    return "".join(out)


def bar_chart(
    labels: list[str],
    values: list[float],
    *,
    unit: str = "",
    height: int = 220,
    places: int = 0,
    highlight: int | None = None,
    caption: str = "",
) -> Chart:
    """Single-series bars. One series means one colour for every bar — a
    value-ramp across nominal categories would double-encode length as hue.
    `highlight` emphasises one bar and recedes the rest.
    """
    w, x0, y0 = 640, 56, 16
    plot_w, plot_h = w - x0 - 16, height - 48

    # Bars must be able to go BELOW zero. A negative value here is not an edge
    # case to clamp away: the Phase 0 spread chart plots a crossed quote
    # (bid > ask), and hiding it would defeat the panel's whole purpose.
    vmin = min(list(values) + [0.0])
    vmax = max(list(values) + [0.0])
    pad = (vmax - vmin) * 0.10 or 1.0
    lo, hi = vmin - (pad if vmin < 0 else 0), vmax + pad
    span = (hi - lo) or 1.0
    zero_y = y0 + plot_h * (hi / span)

    slot = plot_w / max(len(values), 1)
    bar_w = max(slot - 8, 6)  # the 8px gap includes the 2px surface separation

    bars = []
    for i, (lab, val) in enumerate(zip(labels, values, strict=True)):
        bh = abs(val / span) * plot_h
        x = x0 + i * slot + (slot - bar_w) / 2
        y = zero_y - bh if val >= 0 else zero_y
        if val < 0:
            # A negative spread is a defect, not a series. Status colour is
            # correct here, and it ships with a direct label so the meaning
            # never rests on hue alone.
            fill = "var(--critical)"
        elif highlight is None or i == highlight:
            fill = SERIES_VARS[0]
        else:
            fill = "var(--mark-muted)"
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(bh, 1):.1f}" '
            f'rx="4" fill="{fill}" class="bar">'
            f"<title>{_esc(lab)}: {_fmt(val, places)}{_esc(unit)}</title></rect>"
        )
        bars.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{y0 + plot_h + 18}" class="tick" '
            f'text-anchor="middle">{_esc(lab)}</text>'
        )
        # Direct-label the emphasised bar and every anomaly; a number on every
        # bar would be noise.
        if (highlight is not None and i == highlight) or val < 0:
            ly = (y - 6) if val >= 0 else (y + bh + 14)
            bars.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{ly:.1f}" class="value-label" '
                f'text-anchor="middle">{_fmt(val, places)}{_esc(unit)}</text>'
            )

    ticks = [(f, _fmt(lo + span * f, places)) for f in (0.0, 0.5, 1.0)]
    svg = (
        f'<svg viewBox="0 0 {w} {height}" class="chart" role="img" '
        f'aria-label="{_esc(caption or "bar chart")}">'
        f"{_grid(x0, y0, plot_w, plot_h, ticks)}"
        f'<line x1="{x0}" y1="{zero_y:.1f}" x2="{x0 + plot_w}" y2="{zero_y:.1f}" class="axis" />'
        f"{''.join(bars)}</svg>"
    )
    return Chart(
        svg=svg,
        table=TableView(
            columns=["Category", f"Value{f' ({unit})' if unit else ''}"],
            rows=[[lab, _fmt(v, places)] for lab, v in zip(labels, values, strict=True)],
            caption=caption,
        ),
    )


def line_chart(
    x_labels: list[str],
    series: list[tuple[str, list[float]]],
    *,
    unit: str = "",
    height: int = 240,
    places: int = 2,
    caption: str = "",
    zero_base: bool = False,
) -> Chart:
    """Multi-series lines on ONE axis. Never a second y-scale: two measures of
    different magnitude get two charts or a common index, because the
    alignment of two scales is arbitrary and invents correlation.
    """
    if len(series) > MAX_SERIES:
        raise ValueError(
            f"{len(series)} series exceeds the {MAX_SERIES}-slot all-pairs limit; "
            "fold the tail into 'Other' or facet into small multiples"
        )
    w, x0, y0 = 640, 64, 16
    plot_w, plot_h = w - x0 - 16, height - 52
    flat = [v for _, vals in series for v in vals]
    lo, hi = (0.0 if zero_base else min(flat)), max(flat)
    span = (hi - lo) or 1.0
    lo -= span * 0.08
    hi += span * 0.08
    span = hi - lo

    def px(i: int) -> float:
        return x0 + (i / max(len(x_labels) - 1, 1)) * plot_w

    def py(v: float) -> float:
        return y0 + plot_h - ((v - lo) / span) * plot_h

    paths, dots, labels = [], [], []
    for si, (name, vals) in enumerate(series):
        colour = SERIES_VARS[si]
        d = " ".join(
            f"{'M' if i == 0 else 'L'}{px(i):.1f},{py(v):.1f}" for i, v in enumerate(vals)
        )
        paths.append(f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2" '
                     f'stroke-linecap="round" stroke-linejoin="round" />')
        for i, v in enumerate(vals):
            dots.append(
                f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="9" fill="transparent" '
                f'class="hit"><title>{_esc(name)} · {_esc(x_labels[i])}: '
                f"{_fmt(v, places)}{_esc(unit)}</title></circle>"
            )
        # Direct-label the endpoint only.
        labels.append(
            f'<text x="{px(len(vals) - 1) - 4:.1f}" y="{py(vals[-1]) - 10:.1f}" '
            f'class="value-label" text-anchor="end">{_fmt(vals[-1], places)}</text>'
        )

    step = max(len(x_labels) // 8, 1)
    xticks = "".join(
        f'<text x="{px(i):.1f}" y="{y0 + plot_h + 20}" class="tick" '
        f'text-anchor="middle">{_esc(lab)}</text>'
        for i, lab in enumerate(x_labels)
        if i % step == 0 or i == len(x_labels) - 1
    )
    ticks = [(f, _fmt(lo + span * f, places)) for f in (0.0, 0.5, 1.0)]
    svg = (
        f'<svg viewBox="0 0 {w} {height}" class="chart" role="img" '
        f'aria-label="{_esc(caption or "line chart")}">'
        f"{_grid(x0, y0, plot_w, plot_h, ticks)}"
        f'<line x1="{x0}" y1="{y0 + plot_h}" x2="{x0 + plot_w}" y2="{y0 + plot_h}" class="axis" />'
        f"{''.join(paths)}{''.join(labels)}{''.join(dots)}{xticks}</svg>"
    )
    cols = ["Point"] + [n for n, _ in series]
    rows = [
        [x_labels[i]] + [_fmt(vals[i], places) for _, vals in series]
        for i in range(len(x_labels))
    ]
    return Chart(svg=svg, table=TableView(columns=cols, rows=rows, caption=caption))


def heatmap(
    row_labels: list[str],
    col_labels: list[str],
    matrix: list[list[float]],
    *,
    unit: str = "",
    caption: str = "",
) -> Chart:
    """Sequential magnitude on a single hue, light to dark. Never a rainbow.

    Used for flag-density by hour, where the question is "how much" rather
    than "which kind".
    """
    cell, pad_l, pad_t = 26, 92, 26
    w = pad_l + len(col_labels) * cell + 12
    h = pad_t + len(row_labels) * cell + 8
    vmax = max((v for row in matrix for v in row), default=1) or 1

    cells = []
    for r, row in enumerate(matrix):
        for c, v in enumerate(row):
            # 2px surface gap between fills rather than a stroke around marks.
            x, y = pad_l + c * cell, pad_t + r * cell
            intensity = v / vmax
            cells.append(
                f'<rect x="{x + 1}" y="{y + 1}" width="{cell - 2}" height="{cell - 2}" '
                f'rx="3" fill="var(--seq-fill)" fill-opacity="{0.06 + intensity * 0.94:.3f}">'
                f"<title>{_esc(row_labels[r])} · {_esc(col_labels[c])}: "
                f"{_fmt(v, 0)}{_esc(unit)}</title></rect>"
            )
    ylabs = "".join(
        f'<text x="{pad_l - 10}" y="{pad_t + r * cell + cell / 2 + 4}" class="tick" '
        f'text-anchor="end">{_esc(lab)}</text>'
        for r, lab in enumerate(row_labels)
    )
    xlabs = "".join(
        f'<text x="{pad_l + c * cell + cell / 2}" y="{pad_t - 10}" class="tick" '
        f'text-anchor="middle">{_esc(lab)}</text>'
        for c, lab in enumerate(col_labels)
        if c % 2 == 0
    )
    svg = (
        f'<svg viewBox="0 0 {w} {h}" class="chart chart--wide" role="img" '
        f'aria-label="{_esc(caption or "heatmap")}">'
        f"{''.join(cells)}{ylabs}{xlabs}</svg>"
    )
    return Chart(
        svg=svg,
        table=TableView(
            columns=["Row"] + col_labels,
            rows=[[row_labels[r]] + [_fmt(v, 0) for v in row] for r, row in enumerate(matrix)],
            caption=caption,
        ),
    )


def step_timeline(points: list[tuple[str, float]], *, caption: str = "",
                  places: int = 0, unit: str = "") -> Chart:
    """A step line: value held constant until the next revision lands.

    The right form for vintage data specifically because interpolating between
    revisions would draw a value nobody ever published.
    """
    w, h, x0, y0 = 640, 200, 72, 16
    plot_w, plot_h = w - x0 - 20, h - 52
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    lo -= span * 0.25
    hi += span * 0.25
    span = hi - lo

    def px(i: int) -> float:
        return x0 + (i / max(len(points) - 1, 1)) * plot_w

    def py(v: float) -> float:
        return y0 + plot_h - ((v - lo) / span) * plot_h

    d = []
    for i, (_, v) in enumerate(points):
        if i == 0:
            d.append(f"M{px(i):.1f},{py(v):.1f}")
        else:
            d.append(f"L{px(i):.1f},{py(points[i - 1][1]):.1f}")
            d.append(f"L{px(i):.1f},{py(v):.1f}")
    marks = "".join(
        f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="4" fill="var(--series-1)" '
        f'stroke="var(--surface-1)" stroke-width="2" />'
        f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="11" fill="transparent" class="hit">'
        f"<title>{_esc(lab)}: {_fmt(v, places)}{_esc(unit)}</title></circle>"
        for i, (lab, v) in enumerate(points)
    )
    xlabs = "".join(
        f'<text x="{px(i):.1f}" y="{y0 + plot_h + 20}" class="tick" '
        f'text-anchor="middle">{_esc(lab)}</text>'
        for i, (lab, _) in enumerate(points)
    )
    ticks = [(f, _fmt(lo + span * f, places)) for f in (0.0, 0.5, 1.0)]
    path_d = " ".join(d)
    aria = _esc(caption or "revision timeline")
    svg = (
        f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{aria}">'
        f"{_grid(x0, y0, plot_w, plot_h, ticks)}"
        f'<path d="{path_d}" fill="none" stroke="var(--series-1)" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" />'
        f"{marks}{xlabs}</svg>"
    )
    return Chart(
        svg=svg,
        table=TableView(
            columns=["Known at", f"Value{f' ({unit})' if unit else ''}"],
            rows=[[lab, _fmt(v, places)] for lab, v in points],
            caption=caption,
        ),
    )


def legend(names: list[str]) -> str:
    """A legend is always present for two or more series; one series needs
    none because the title names it.
    """
    if len(names) < 2:
        return ""
    items = "".join(
        f'<span class="legend__item"><span class="legend__swatch" '
        f'style="background:{SERIES_VARS[i]}"></span>{_esc(n)}</span>'
        for i, n in enumerate(names)
    )
    return f'<div class="legend">{items}</div>'
