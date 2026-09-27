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


def status_pie_chart(totals):
    labels = ["Pending", "Approved", "Not approved"]
    values = [totals["pending"], totals["approved"], totals["not_approved"]]

    fig, ax = plt.subplots(figsize=(4, 4))
    if sum(values) == 0:
        ax.text(0.5, 0.5, "No applications in this period", ha="center", va="center", wrap=True)
        ax.axis("off")
    else:
        ax.pie(values, labels=labels, autopct="%1.0f%%", startangle=90)
    ax.set_title("Applications by status")
    return _fig_to_data_uri(fig)


def monthly_trend_chart(trend):
    labels = [point["label"] for point in trend]
    values = [point["count"] for point in trend]

    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.plot(labels, values, marker="o")
    ax.set_title("Applications filed per month (all types)")
    ax.set_ylabel("Applications filed")
    ax.set_ylim(bottom=0)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    return _fig_to_data_uri(fig)
