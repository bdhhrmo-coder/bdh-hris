"""
Resolves the dashboard's month/quarter/year filter (CLAUDE.md §12:
"Filterable by month/quarter/year") into a concrete (start_date, end_date)
range plus a human label, from GET params. Defaults to the current month
when nothing is specified, so the dashboard is useful with zero filters
set, not just after picking one.
"""

from calendar import monthrange
from datetime import date

PERIOD_CHOICES = ["month", "quarter", "year"]


def resolve_period(params):
    today = date.today()

    period = params.get("period", "month")
    if period not in PERIOD_CHOICES:
        period = "month"

    try:
        year = int(params.get("year", today.year))
    except (TypeError, ValueError):
        year = today.year

    if period == "year":
        return date(year, 1, 1), date(year, 12, 31), str(year)

    if period == "quarter":
        try:
            quarter = int(params.get("quarter", (today.month - 1) // 3 + 1))
        except (TypeError, ValueError):
            quarter = (today.month - 1) // 3 + 1
        quarter = min(max(quarter, 1), 4)
        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 2
        end_day = monthrange(year, end_month)[1]
        return date(year, start_month, 1), date(year, end_month, end_day), f"Q{quarter} {year}"

    # period == "month"
    try:
        month = int(params.get("month", today.month))
    except (TypeError, ValueError):
        month = today.month
    month = min(max(month, 1), 12)
    end_day = monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, end_day), date(year, month, 1).strftime("%B %Y")
