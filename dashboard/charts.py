"""
Dashboard charts as inline SVG (Batch 3 Item 4, owner decision 2026-10-07).

Each function returns ready-to-place SVG markup (marked safe; every label
is escaped). Drawing them as SVG instead of pictures lets the browser
animate each part with plain CSS (static/css/animations.css, "Dashboard
charts"): bars grow from the baseline, the trend line draws left to right,
pie slices sweep round. The numbers come straight from the same data as
before and are never changed by the animation. No chart library and
nothing from the internet. With "Reduce motion" on, charts appear at once.

Same function names and inputs as the earlier matplotlib version, so
dashboard/views.py did not change.
"""

import math
import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

# The app's palette (static/css/app.css).
NAVY = "#123a63"
TEAL = "#157a6e"
GREEN = "#1f7a3d"
AMBER = "#a3690a"
RED = "#a3241f"
GRAY = "#5b6570"
GRID = "#e3e8ee"

PIE_R = 25                        # pie drawn as a thick-stroked circle
PIE_C = 2 * math.pi * PIE_R       # its circumference (157.08)


def _fmt(v):
    v = float(v)
    return str(int(v)) if v == int(v) else f"{v:.1f}"


def _nice_max(value):
    """A round axis maximum just above value (1, 2, 5, 10, 20, 50...)."""
    value = max(float(value), 1.0)
    step = 10 ** math.floor(math.log10(value))
    for m in (1, 2, 5, 10):
        if value <= m * step:
            return m * step
    return 10 * step


def _svg(width, height, title, body, css_class):
    t = escape(title)
    return mark_safe(
        f'<svg class="svg-chart {css_class}" viewBox="0 0 {width} {height}" role="img" aria-label="{t}" '
        f'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"><title>{t}</title>'
        f'<text class="chart-title" x="{width / 2:.0f}" y="18" text-anchor="middle">{t}</text>{body}</svg>'
    )


def _empty(width, height, title, message, css_class):
    body = (f'<text class="chart-empty" x="{width / 2:.0f}" y="{height / 2:.0f}" text-anchor="middle">'
            f'{escape(message)}</text>')
    return _svg(width, height, title, body, css_class)


# -- pies --------------------------------------------------------------------

def _pie(slices, title, empty_message):
    """slices: [(label, count, colour)]. A pie plus a legend with count and %."""
    shown = [s for s in slices if s[1]]
    total = sum(s[1] for s in shown)
    width, height = 340, 190
    if not total:
        return _empty(width, height, title, empty_message, "chart-pie")
    parts, legend, offset = [], [], 0.0
    for i, (label, count, colour) in enumerate(shown):
        length = PIE_C * count / total
        parts.append(
            f'<circle r="{PIE_R}" cx="50" cy="50" fill="none" stroke="{colour}" stroke-width="{PIE_R * 2}" '
            f'stroke-dasharray="{length:.3f} {PIE_C:.3f}" stroke-dashoffset="{-offset:.3f}"/>'
        )
        offset += length
        pct = round(count * 100 / total)
        y = 52 + i * 24
        legend.append(
            f'<g class="chart-legend" style="--i:{i}"><rect x="176" y="{y - 10}" width="12" height="12" rx="2" '
            f'fill="{colour}"/><text x="194" y="{y}">{escape(label)}: {count} ({pct}%)</text></g>'
        )
    # The pie sits inside a mask whose ring "unrolls" clockwise: the sweep.
    body = (
        '<defs><mask id="{m}" maskUnits="userSpaceOnUse" x="-10" y="-10" width="120" height="120"><circle class="pie-sweep" r="{r}" cx="50" cy="50" fill="none" stroke="#fff" '
        'stroke-width="{w}" stroke-dasharray="{c:.3f} {c:.3f}"/></mask></defs>'
        '<g transform="translate(12 30) scale(1.5)"><g mask="url(#{m})" transform="rotate(-90 50 50)">{parts}</g></g>'
        '{legend}'
    ).format(m="sweep-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"), r=PIE_R + 1, w=PIE_R * 2 + 4, c=2 * math.pi * (PIE_R + 1),
             parts="".join(parts), legend="".join(legend))
    return _svg(width, height, title, body, "chart-pie")


def status_pie_chart(totals):
    return _pie([("Pending", totals["pending"], AMBER), ("Approved", totals["approved"], GREEN),
                 ("Not approved", totals["not_approved"], RED)],
                "Applications by status", "No applications in this period")


def count_pie_chart(rows, title):
    palette = [NAVY, TEAL, GRAY, AMBER]
    return _pie([(r["label"], r["count"], palette[i % len(palette)]) for i, r in enumerate(rows)], title, "No data yet")


# -- bars ----------------------------------------------------------------------

def section_rate_chart(rows, title, target=None):
    """Horizontal bars, one per section, value = rate (%). A section with no
    data says 'no data' (it is not shown as 0%)."""
    width, left, right_pad, top, row_h = 760, 230, 70, 34, 26
    plot_w = width - left - right_pad
    height = top + row_h * max(len(rows), 1) + 34
    values = [float(r["rate"]) if r["rate"] is not None else 0.0 for r in rows]
    vmax = _nice_max(max(values + [float(target or 0), 10]) * 1.1)
    scale = plot_w / vmax
    out = []
    for tick in range(0, 6):  # 5 grid steps
        v = vmax * tick / 5
        x = left + v * scale
        out.append(f'<line class="chart-grid" x1="{x:.1f}" y1="{top - 4}" x2="{x:.1f}" y2="{height - 26}"/>'
                   f'<text class="chart-axis" x="{x:.1f}" y="{height - 10}" text-anchor="middle">{_fmt(v)}%</text>')
    for i, (r, v) in enumerate(zip(rows, values)):
        y = top + i * row_h
        name = r["section"].name.replace(" Section", "")
        over = target is not None and v > float(target)
        colour = RED if over else NAVY
        w = v * scale
        label = "no data" if r["rate"] is None else f'{r["rate"]}%'
        out.append(
            f'<text class="chart-label" x="{left - 8}" y="{y + 17}" text-anchor="end">{escape(name)}</text>'
            f'<rect class="bar bar-h" style="--i:{i}" x="{left}" y="{y + 5}" width="{w:.1f}" height="{row_h - 10}" '
            f'rx="2" fill="{colour}"><title>{escape(name)}: {escape(label)}</title></rect>'
            f'<text class="chart-value" style="--i:{i}" x="{left + w + 6:.1f}" y="{y + 17}">{escape(label)}</text>'
        )
    if target is not None:
        x = left + float(target) * scale
        out.append(f'<line class="chart-target" x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{height - 26}" '
                   f'stroke="{AMBER}"/><text class="chart-axis" x="{x + 4:.1f}" y="{top - 8}" fill="{AMBER}">'
                   f'Target {escape(str(target))}%</text>')
    return _svg(width, height, title, "".join(out), "chart-bars-h")


def count_bar_chart(rows, title, xlabel=""):
    """Vertical bars (e.g. age groups) with the count above each bar."""
    width, height, left, top, bottom = 760, 300, 50, 34, 56
    plot_w, plot_h = width - left - 20, height - top - bottom
    values = [r["count"] for r in rows]
    vmax = _nice_max(max(values + [1]) * 1.15)
    n = max(len(rows), 1)
    slot = plot_w / n
    bar_w = min(slot * 0.62, 70)
    base = top + plot_h
    out = []
    for tick in range(0, 5):
        v = vmax * tick / 4
        y = base - v / vmax * plot_h
        out.append(f'<line class="chart-grid" x1="{left}" y1="{y:.1f}" x2="{width - 20}" y2="{y:.1f}"/>'
                   f'<text class="chart-axis" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(v)}</text>')
    for i, r in enumerate(rows):
        h = r["count"] / vmax * plot_h
        x = left + i * slot + (slot - bar_w) / 2
        cx = x + bar_w / 2
        out.append(
            f'<rect class="bar bar-v" style="--i:{i}" x="{x:.1f}" y="{base - h:.1f}" width="{bar_w:.1f}" '
            f'height="{h:.1f}" rx="2" fill="{NAVY}"><title>{escape(r["label"])}: {r["count"]}</title></rect>'
            f'<text class="chart-value" style="--i:{i}" x="{cx:.1f}" y="{base - h - 5:.1f}" text-anchor="middle">'
            f'{r["count"]}</text>'
            f'<text class="chart-axis" x="{cx:.1f}" y="{base + 16}" text-anchor="middle">{escape(r["label"])}</text>'
        )
    out.append(f'<line class="chart-baseline" x1="{left}" y1="{base}" x2="{width - 20}" y2="{base}"/>')
    if xlabel:
        out.append(f'<text class="chart-axis" x="{left + plot_w / 2:.0f}" y="{height - 12}" text-anchor="middle">'
                   f'{escape(xlabel)}</text>')
    out.append(f'<text class="chart-axis" transform="translate(12 {top + plot_h / 2:.0f}) rotate(-90)" '
               f'text-anchor="middle">Employees</text>')
    return _svg(width, height, title, "".join(out), "chart-bars-v")


# -- line ------------------------------------------------------------------------

def monthly_trend_chart(trend):
    title = "Applications filed per month (all types)"
    width, height, left, top, bottom = 760, 300, 50, 34, 70
    if not trend:
        return _empty(width, height, title, "No applications in this period", "chart-line")
    plot_w, plot_h = width - left - 24, height - top - bottom
    values = [p["count"] for p in trend]
    vmax = _nice_max(max(values + [1]) * 1.15)
    base = top + plot_h
    step = plot_w / max(len(trend) - 1, 1)
    pts = [(left + (i * step if len(trend) > 1 else plot_w / 2), base - v / vmax * plot_h) for i, v in enumerate(values)]
    out = []
    for tick in range(0, 5):
        v = vmax * tick / 4
        y = base - v / vmax * plot_h
        out.append(f'<line class="chart-grid" x1="{left}" y1="{y:.1f}" x2="{width - 24}" y2="{y:.1f}"/>'
                   f'<text class="chart-axis" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(v)}</text>')
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pts[0][0]:.1f},{base} " + line + f" {pts[-1][0]:.1f},{base}"
    out.append(f'<polygon class="chart-area" points="{area}" fill="{NAVY}"/>')
    out.append(f'<polyline class="chart-line-path" points="{line}" fill="none" stroke="{NAVY}" stroke-width="2.5" '
               f'stroke-linejoin="round" stroke-linecap="round" pathLength="1"/>')
    for i, ((x, y), p) in enumerate(zip(pts, trend)):
        out.append(
            f'<circle class="chart-dot" style="--i:{i}" cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{TEAL}">'
            f'<title>{escape(p["label"])}: {p["count"]}</title></circle>'
            f'<text class="chart-axis" text-anchor="end" transform="translate({x + 4:.1f} {base + 14}) rotate(-45)">'
            f'{escape(p["label"])}</text>'
        )
    out.append(f'<line class="chart-baseline" x1="{left}" y1="{base}" x2="{width - 24}" y2="{base}"/>')
    out.append(f'<text class="chart-axis" transform="translate(12 {top + plot_h / 2:.0f}) rotate(-90)" '
               f'text-anchor="middle">Applications filed</text>')
    return _svg(width, height, title, "".join(out), "chart-line")
