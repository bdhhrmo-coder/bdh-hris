"""
Personnel-record retention review — CLAUDE.md §13, 10-year retention
period for separated employees' records (confirmed with the project owner
2026-09-27). Flag-and-decide only; see models.py's docstring for why this
app never deletes anything itself.

RECORD_RETENTION_YEARS lives in settings (not hardcoded here) so it can be
revised if BDH's records disposal schedule changes, the same way CTO's
multipliers are kept configurable per CLAUDE.md §7.
"""

from datetime import date

from django.conf import settings

from employees.models import Employee

from .models import RetentionReviewRecord


def retention_cutoff_date(today=None):
    """The separation date on or before which a record becomes due for
    retention review, given today's date and the configured period."""
    today = today or date.today()
    years = getattr(settings, "RECORD_RETENTION_YEARS", 10)
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        # today is Feb 29 and (today.year - years) isn't a leap year.
        return today.replace(month=2, day=28, year=today.year - years)


def employees_due_for_retention_review(today=None):
    """
    Separated employees whose separation_date is old enough to trigger
    review, and who don't already have a recorded decision. Once ANY
    decision (RETAIN or DISPOSE) is recorded, the employee drops off this
    list — a fresh decision requires a deliberate re-review, not an
    automatic re-flag (see RetentionReviewRecord's help text).
    """
    cutoff = retention_cutoff_date(today)
    already_decided_ids = RetentionReviewRecord.objects.values_list("employee_id", flat=True)
    return (
        Employee.objects.filter(separation_date__isnull=False, separation_date__lte=cutoff)
        .exclude(pk__in=already_decided_ids)
        .order_by("separation_date")
    )


def retention_review_history():
    return RetentionReviewRecord.objects.select_related("employee", "reviewed_by").all()
