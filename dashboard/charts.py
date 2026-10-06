"""
Server-rendered charts (matplotlib -> PNG, embedded in the page as a data
URI) rather than a client-side JS charting library — keeps to the
project's Django Templates + HTMX + Alpine.js stack (CLAUDE.md §2: "no
separate SPA/JS framework") instead of adding one just for two charts.
"""

import base64
from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 (must follow matplotlib.use)


def _fig_to_data_uri(fig):
    buffer = BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", dpi=100)
    plt.close(fig)
    buffer.seek(0)
    return "data:image/png;base64," + base64.b64encode(buffer.read()).decode("ascii")


# Matches the app's CSS palette (static/css/app.css) so the server-rendered
# charts read as part of the same system rather than matplotlib defaults.
_AMBER = "#a3690a"
_GREEN = "#1f7a3d"
_RED = "#a3241f"
_NAVY = "#123a63"
_GRAY = "#5b6570"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.edgecolor": "#dfe3e8",
    "axes.labelcolor": _GRAY,
    "text.color": "#1f2937",
    "xtick.color": _GRAY,
    "ytick.color": _GRAY,
})


def status_pie_chart(totals):
    labels = ["Pending", "Approved", "Not approved"]
    values = [totals["pending"], totals["approved"], totals["not_approved"]]
    colors = [_AMBER, _GREEN, _RED]

    fig, ax = plt.subplots(figsize=(4, 4))
    if sum(values) == 0:
        ax.text(0.5, 0.5, "No applications in this period", ha="center", va="center", wrap=True)
        ax.axis("off")
    else:
        ax.pie(values, labels=labels, colors=colors, autopct="%1.0f%%", startangle=90,
               wedgeprops={"edgecolor": "white", "linewidth": 1.5})
    ax.set_title("Applications by status", color=_NAVY, fontweight="bold")
    return _fig_to_data_uri(fig)


def monthly_trend_chart(trend):
    labels = [point["label"] for point in trend]
    values = [point["count"] for point in trend]

    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.plot(labels, values, marker="o", color=_NAVY, linewidth=2, markerfacecolor="#157a6e", markersize=6)
    ax.fill_between(range(len(labels)), values, color=_NAVY, alpha=0.06)
    ax.set_title("Applications filed per month (all types)", color=_NAVY, fontweight="bold")
    ax.set_ylabel("Applications filed")
    ax.set_ylim(bottom=0)
    ax.spines[["top", "right"]].set_visible(False)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def section_rate_chart(rows, title, target=None):
    """Horizontal bars, one per section, value = rate (%). Sections with no
    data are shown as 'no data' rather than as 0%."""
    names = [r["section"].name.replace(" Section", "") for r in rows]
    values = [float(r["rate"]) if r["rate"] is not None else 0 for r in rows]
    fig, ax = plt.subplots(figsize=(8, max(3, 0.38 * len(rows) + 1)))
    colors = [(_RED if target is not None and v > float(target) else _NAVY) for v in values]
    bars = ax.barh(names, values, color=colors)
    for bar, r in zip(bars, rows):
        label = "no data" if r["rate"] is None else f'{r["rate"]}%'
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2, label, va="center", fontsize=8, color=_GRAY)
    if target is not None:
        ax.axvline(float(target), color=_AMBER, linestyle="--", linewidth=1.5, label=f"Target {target}%")
        ax.legend(loc="lower right", fontsize=8, frameon=False)
    ax.invert_yaxis()
    ax.set_xlabel("%")
    ax.set_xlim(0, max(values + [float(target or 0), 10]) * 1.2)
    ax.set_title(title, color=_NAVY, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def count_pie_chart(rows, title):
    shown = [r for r in rows if r["count"]]
    fig, ax = plt.subplots(figsize=(4, 4))
    if not shown:
        ax.text(0.5, 0.5, "No data yet", ha="center", va="center")
        ax.axis("off")
    else:
        palette = [_NAVY, "#157a6e", _GRAY, _AMBER]
        ax.pie([r["count"] for r in shown], labels=[r["label"] for r in shown], autopct="%1.0f%%", startangle=90,
               colors=palette[:len(shown)], wedgeprops={"edgecolor": "white", "linewidth": 1.5})
    ax.set_title(title, color=_NAVY, fontweight="bold")
    return _fig_to_data_uri(fig)


def count_bar_chart(rows, title, xlabel=""):
    fig, ax = plt.subplots(figsize=(8, 3.5))
    labels = [r["label"] for r in rows]
    values = [r["count"] for r in rows]
    bars = ax.bar(labels, values, color=_NAVY)
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), str(v), ha="center", va="bottom", fontsize=8)
    ax.set_title(title, color=_NAVY, fontweight="bold")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Employees")
    ax.set_ylim(0, max(values + [1]) * 1.2)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return _fig_to_data_uri(fig)
