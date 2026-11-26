"""Holiday list and working-day counts (owner decision N3, 2026-11-22).

Working days = Monday to Friday minus the active holidays. Used by the
Performance Management windows; leave and CTO do not use it."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone


def holiday_dates(start, end):
    from .models import Holiday

    return set(Holiday.objects.filter(voided_at__isnull=True, date__range=(start, end))
               .values_list("date", flat=True))


def is_working_day(day, holidays=None):
    if day.weekday() >= 5:
        return False
    holidays = holiday_dates(day, day) if holidays is None else holidays
    return day not in holidays


def working_days(start, end):
    """Working days from start to end, both included (0 if end < start)."""
    if end < start:
        return 0
    hols = holiday_dates(start, end)
    n, d = 0, start
    while d <= end:
        if is_working_day(d, hols):
            n += 1
        d += timedelta(days=1)
    return n


def add_working_days(start, n):
    """The date n working days after start (start itself never counts)."""
    hols = holiday_dates(start, start + timedelta(days=n * 3 + 30))
    d, count = start, 0
    while count < n:
        d += timedelta(days=1)
        if is_working_day(d, hols):
            count += 1
    return d


def year_has_holidays(year):
    from datetime import date

    return bool(holiday_dates(date(year, 1, 1), date(year, 12, 31)))


def add_holiday(user, day, name, kind, basis_ref=""):
    from .models import Holiday

    name = " ".join((name or "").split())
    if not name:
        raise ValidationError("Type the name of the holiday.")
    if Holiday.objects.filter(date=day, voided_at__isnull=True).exists():
        raise ValidationError(f"{day:%b %d, %Y} is already on the list. Void that entry first to correct it.")
    return Holiday.objects.create(date=day, name=name, kind=kind, basis_ref=basis_ref.strip(), created_by=user)


def void_holiday(user, holiday, reason):
    if not (reason or "").strip():
        raise ValidationError("Type the reason for voiding it.")
    if holiday.voided_at:
        raise ValidationError("Already voided.")
    holiday.voided_at, holiday.voided_by, holiday.void_reason = timezone.now(), user, reason.strip()[:255]
    holiday.save(update_fields=["voided_at", "voided_by", "void_reason"])
