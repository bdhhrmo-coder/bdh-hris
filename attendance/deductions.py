"""
CLAUDE.md §9: "Undertime auto-deducts from leave credits" — confirmed
2026-09-27 to mean Vacation Leave (VL) specifically. Kept isolated from
models.py the same way leave/balances.py and cto/balances.py are, since
this is the one place a change in the deduction rule would need to happen.

Design note: unlike a LeaveApplication's ledger entry (posted once, when a
human approves something, and never touched again), an AttendanceRecord's
undertime is a computed fact that can change every time the record is
re-saved (a biometric re-import, or a correction moving the time out
later). So this keeps at most one linked LeaveCreditTransaction per
AttendanceRecord and replaces it whenever the computed amount changes,
rather than layering reversal-and-correction entries — the audit trail
for *why* it changed lives in the AttendanceRecord's source/import_batch
and any AttendanceCorrectionRequest that produced it, not in the ledger
itself. If VL balance is insufficient, the shortfall is recorded anyway
(no payroll deduction — out of scope) since §9 doesn't say to block
attendance recording over an insufficient VL balance.
"""

from decimal import Decimal, ROUND_HALF_UP


def undertime_days(record):
    """Undertime expressed as a fraction of one shift-day, e.g. 30 minutes
    undertime on an 8-hour (480-minute) shift = 0.06 day."""
    if record.undertime_minutes <= 0:
        return Decimal("0")
    shift_minutes = Decimal(record.employee.shift_hours * 60)
    fraction = Decimal(record.undertime_minutes) / shift_minutes
    return fraction.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def sync_undertime_deduction(record, actor_user):
    """Ensures record.undertime_transactions reflects exactly the current
    undertime amount — zero or one row, always in sync with
    record.undertime_minutes."""
    from leave.models import LeaveCreditTransaction, LeaveType

    target = undertime_days(record)
    existing = list(record.undertime_transactions.all())

    if target <= 0:
        if existing:
            record.undertime_transactions.all().delete()
        return

    if len(existing) == 1 and existing[0].days == -target:
        return  # already correct — no-op, keeps the ledger from growing on every unrelated re-save

    record.undertime_transactions.all().delete()
    vl = LeaveType.objects.get(code="VL")
    LeaveCreditTransaction.objects.create(
        employee=record.employee,
        leave_type=vl,
        transaction_type=LeaveCreditTransaction.USED,
        days=-target,
        transaction_date=record.date,
        attendance_record=record,
        created_by=actor_user,
        notes=f"Auto-deducted for {record.undertime_minutes} minute(s) undertime on {record.date} (CLAUDE.md §9).",
    )
