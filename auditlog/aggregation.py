"""
Normalizes the existing accountability records (see models.py's
docstring) into one row shape for the audit log view, the same way
dashboard/stats.py normalizes five apps' differing status vocabularies
into one shared bucket scheme — no new storage, just a read-time merge.

Row shape (a plain dict, not a model — nothing here is ever saved):
    {
        "timestamp": datetime,
        "module": str,           # e.g. "Leave", "CTO", "Employee Record"
        "action": str,           # e.g. "SUBMITTED", "Edited Surname"
        "detail": str,           # human-readable one-liner
        "actor": User or None,   # who did it
        "employee_ids": {int},   # employee(s) the record concerns, for filtering
        "employee_display": str, # human-readable name(s) for the column
    }
"""

from cto.models import CTOClaimAction, CTOUsageApplicationAction
from datechange.models import DateChangeAction
from documents.models import UploadedDocumentEvent
from employees.models import EmployeeEditHistory
from exchange.models import DutyExchangeRequestAction
from attendance.models import AttendanceCorrectionRequestAction
from leave.models import LeaveApplicationAction
from official_requests.models import OfficialRequestAction


# Readable labels for action codes that aren't self-explanatory.
ACTION_LABELS = {
    "opening_line_added": "Opening CTO line added",
    "opening_line_voided": "Opening CTO line voided",
    "correct_day_type": "Day type corrected",
    "skip": "Step skipped",
    "endorse_unit_first": "Endorsed by the first of two unit heads",
    "cancel": "Cancelled by the filer",
    "hr_close": "Closed by HR (time in entered)",
    "hr_not_used": "Marked not used by HR",
    "tap_out": "Tap Out", "tap_in": "Tap In", "complete": "Charged",
}


def _rows_from_out_on_pass_setup():
    """Out-on-Pass contract periods and Jan 1-10 opening lines: entered and voided."""
    from outonpass.models import CospContractPeriod, PassOpeningUsage
    from outonpass.rules import hm

    rows = []
    for line in CospContractPeriod.objects.select_related("employee", "entered_by", "voided_by"):
        what = f"COSP contract {line.start_date:%b %d, %Y} - {line.end_date:%b %d, %Y}"
        rows.append({"timestamp": line.entered_at, "module": "Out-on-Pass", "action": "Contract period added",
                     "detail": what, "actor": line.entered_by, "employee_ids": {line.employee_id},
                     "employee_display": str(line.employee)})
        if line.voided_at:
            rows.append({"timestamp": line.voided_at, "module": "Out-on-Pass", "action": "Contract period voided",
                         "detail": f"{what}: {line.void_reason}", "actor": line.voided_by,
                         "employee_ids": {line.employee_id}, "employee_display": str(line.employee)})
    for line in PassOpeningUsage.objects.select_related("employee", "entered_by", "voided_by"):
        what = f"{hm(line.minutes_used)} used before the HRIS ({line.year}): {line.reason}"
        rows.append({"timestamp": line.entered_at, "module": "Out-on-Pass", "action": "Opening hours added",
                     "detail": what, "actor": line.entered_by, "employee_ids": {line.employee_id},
                     "employee_display": str(line.employee)})
        if line.voided_at:
            rows.append({"timestamp": line.voided_at, "module": "Out-on-Pass", "action": "Opening hours voided",
                         "detail": f"{what} - {line.void_reason}", "actor": line.voided_by,
                         "employee_ids": {line.employee_id}, "employee_display": str(line.employee)})
    return rows


def _employees_for_object(obj):
    """Best-effort list of Employee objects a transaction record concerns."""
    if obj is None:
        return []
    if hasattr(obj, "employee_a_id"):  # DutyExchangeRequest
        return [e for e in (obj.employee_a, obj.employee_b) if e is not None]
    if hasattr(obj, "employee"):
        return [obj.employee] if obj.employee_id else []
    return []


def _employee_display(employees):
    return ", ".join(str(e) for e in employees) or "—"


def _rows_from_employee_edit_history(qs):
    rows = []
    for row in qs.select_related("employee", "changed_by"):
        rows.append(
            {
                "timestamp": row.edited_at,
                "module": "Employee Record",
                "action": f"Edited {row.field_label or row.field_name}",
                "detail": f"“{row.old_value}” → “{row.new_value}” — reason: {row.reason}",
                "actor": row.changed_by,
                "employee_ids": {row.employee_id},
                "employee_display": str(row.employee),
            }
        )
    return rows


def _oic_flag(notes):
    """Item 5: an OIC acting again on a request they already acted on is flagged."""
    from leave.permissions import OIC_REPEAT_TAG

    return " (OIC, acted earlier)" if (notes or "").startswith(OIC_REPEAT_TAG) else ""


def _rows_from_request_actions(qs, module_label, request_field):
    rows = []
    for row in qs.select_related(request_field, "acted_by"):
        request_obj = getattr(row, request_field)
        employees = _employees_for_object(request_obj)
        rows.append(
            {
                "timestamp": row.acted_at,
                "module": module_label,
                "action": ACTION_LABELS.get(row.action, row.action) + _oic_flag(row.notes),
                "detail": f"{request_obj} — {row.notes}" if row.notes else str(request_obj),
                "actor": row.acted_by,
                "employee_ids": {e.pk for e in employees},
                "employee_display": _employee_display(employees),
            }
        )
    return rows


def _rows_from_document_events(qs):
    rows = []
    for row in qs.select_related("document", "document__content_type", "acted_by"):
        content_object = row.document.content_object
        employees = _employees_for_object(content_object)
        rows.append(
            {
                "timestamp": row.acted_at,
                "module": "Documents",
                "action": row.get_event_type_display(),
                "detail": f"{row.document.original_filename} ({row.document.content_type.model} #{row.document.object_id})"
                + (f" — {row.notes}" if row.notes else ""),
                "actor": row.acted_by,
                "employee_ids": {e.pk for e in employees},
                "employee_display": _employee_display(employees),
            }
        )
    return rows


def _rows_from_announcement_actions():
    """Batch 4: announcements are hospital-wide notices, not about one
    employee, so they have no employee filter value."""
    from announcements.models import AnnouncementAction

    rows = []
    for row in AnnouncementAction.objects.select_related("announcement", "acted_by"):
        rows.append({
            "timestamp": row.acted_at, "module": "Announcements", "action": row.action,
            "detail": f"{row.announcement}" + (f" — {row.notes}" if row.notes else ""),
            "actor": row.acted_by, "employee_ids": set(), "employee_display": "—",
        })
    return rows


def _rows_from_schedule_events():
    """Batch 5: duty-schedule routing steps, and every cell changed after
    COH approval (approved exchange or HR correction)."""
    from schedules.models import ScheduleAction, ScheduleChange

    rows = []
    for row in ScheduleAction.objects.select_related("schedule__section", "schedule__unit", "acted_by"):
        rows.append({
            "timestamp": row.acted_at, "module": "Duty Schedule", "action": row.action,
            "detail": f"{row.schedule}" + (f" — {row.notes}" if row.notes else ""),
            "actor": row.acted_by, "employee_ids": set(), "employee_display": "—",
        })
    for ch in ScheduleChange.objects.select_related("schedule__section", "schedule__unit", "employee", "changed_by"):
        rows.append({
            "timestamp": ch.changed_at, "module": "Duty Schedule", "action": "change",
            "detail": f"{ch.schedule}: {ch.date:%b %d} {ch.old_shift or 'blank'} → {ch.new_shift or 'blank'} "
                      f"— {ch.reason}",
            "actor": ch.changed_by, "employee_ids": {ch.employee_id},
            "employee_display": ch.employee.full_name,
        })
    return rows


def _rows_from_oic_designations():
    """Batch 7: OIC designations made on the OIC screen (or in admin), and
    early endings."""
    from accounts.models import RoleAssignment

    rows = []
    for ra in RoleAssignment.objects.filter(is_oic=True).select_related(
        "employee", "designated_by_hr", "delegated_by__user", "section", "unit", "created_by", "ended_by"
    ):
        detail = (f"{ra.employee.full_name} as OIC, {ra.scope_label}, "
                  f"{ra.start_date or '—'} to {ra.end_date or 'open'}, for {ra.delegated_by.full_name if ra.delegated_by else '—'}")
        if ra.designated_by_hr_id:
            detail += (f". Entered by HR Administrator {ra.designated_by_hr.full_name} from a signed written "
                       f"designation{' ref. no. ' + ra.designation_reference if ra.designation_reference else ''}. "
                       f"Reason: {ra.designation_reason}. Written instruction attached")
        else:
            detail += ". Designated by the approver in the system"
        actor = ra.created_by or (ra.delegated_by.user if ra.delegated_by else None)
        rows.append({
            "timestamp": ra.created_at, "module": "OIC Designation", "action": "designate (by HR)" if ra.designated_by_hr_id else "designate", "detail": detail,
            "actor": actor, "employee_ids": {ra.employee_id}, "employee_display": ra.employee.full_name,
        })
        if ra.ended_at:
            rows.append({
                "timestamp": ra.ended_at, "module": "OIC Designation", "action": "end early", "detail": detail,
                "actor": ra.ended_by, "employee_ids": {ra.employee_id}, "employee_display": ra.employee.full_name,
            })
    return rows


def _rows_from_date_changes():
    """Date changes of approved leave/CTO (Part 2, 2026-11-19): every step,
    with the old and new dates and the justification or remark."""
    rows = []
    for row in DateChangeAction.objects.select_related("request__employee", "acted_by"):
        r = row.request
        detail = f"#{r.pk} {r.what}: {r.old_dates_label} -> {r.new_dates_label}"
        if row.action == "submit":
            detail += f" - justification: {r.justification}"
        elif row.notes:
            detail += f" - {row.notes}"
        rows.append({
            "timestamp": row.acted_at, "module": "Date Change", "action": row.label + _oic_flag(row.notes),
            "detail": detail,
            "actor": row.acted_by, "employee_ids": {r.employee_id}, "employee_display": str(r.employee),
        })
    return rows


def _rows_from_exchange_settings():
    """Exchange groups given to positions, and the Exchange of Duty rules
    (owner decisions 2026-11-26)."""
    from employees.models import PositionGroupChange
    from exchange.models import ExchangeRulesChange

    rows = []
    for ch in PositionGroupChange.objects.select_related("position", "changed_by"):
        rows.append({"timestamp": ch.changed_at, "module": "Exchange of Duty", "action": "Exchange group changed",
                     "detail": f"{ch.position}: {ch.old_group or 'none'} -> {ch.new_group or 'none'}",
                     "actor": ch.changed_by, "employee_ids": set(), "employee_display": "—"})
    for ch in ExchangeRulesChange.objects.select_related("changed_by"):
        rows.append({"timestamp": ch.changed_at, "module": "Exchange of Duty", "action": "Rule changed",
                     "detail": f"{ch.field}: {ch.old_value} -> {ch.new_value}",
                     "actor": ch.changed_by, "employee_ids": set(), "employee_display": "—"})
    return rows


def _rows_from_holidays_and_history():
    """Holiday list and assignment history (rebuilt 2026-11-26)."""
    from employees.models import AssignmentAction, Holiday

    rows = []
    for h in Holiday.objects.select_related("created_by", "voided_by"):
        rows.append({"timestamp": h.created_at, "module": "Holidays", "action": "Holiday added",
                     "detail": f"{h.date:%b %d, %Y} {h.name} ({h.get_kind_display()})", "actor": h.created_by,
                     "employee_ids": set(), "employee_display": "—"})
        if h.voided_at:
            rows.append({"timestamp": h.voided_at, "module": "Holidays", "action": "Holiday voided",
                         "detail": f"{h.date:%b %d, %Y} {h.name}: {h.void_reason}", "actor": h.voided_by,
                         "employee_ids": set(), "employee_display": "—"})
    for a in AssignmentAction.objects.select_related("line__employee", "line__section", "line__unit",
                                                     "line__action_type", "acted_by"):
        ln = a.line
        detail = f"{ln.area}: {ln.action_type} from {ln.effective_from:%b %d, %Y}"
        if ln.effective_to:
            detail += f" to {ln.effective_to:%b %d, %Y}"
        if a.notes:
            detail += f" - {a.notes}"
        rows.append({"timestamp": a.acted_at, "module": "Assignment History", "action": a.label, "detail": detail,
                     "actor": a.acted_by, "employee_ids": {ln.employee_id}, "employee_display": str(ln.employee)})
    return rows


# Performance (gap 1, 2026-11-26). Rows never show a score, a rating, a
# comment or which rater did what: the Audit Log is also open to the AO, the
# COH and the System Administrator, and rater identity is for HR
# Administrators only (Design Package v1.1 §7.1). Notes are shown only where
# they are a reason someone typed for a step.
PM_PERIOD_ACTIONS = {
    "created": ("Period created", False), "opened": ("Period opened", False),
    "planned": ("Period planned", False), "cancelled": ("Period cancelled", True),
    "rater_added": ("Rater added", True), "rater_removed": ("Rater removed", True),
    "rater_left_off": ("Subordinate left off the list (assignment history)", True),
    "peers_finalized": ("Peers picked", False), "form_closed": ("Rating form closed without counting", False),
    "certified_encoded": ("PHRMO-certified rating encoded", False), "certified_verified": ("Certified rating verified", False),
    "dtr_encoded": ("DTR counts encoded", False), "dtr_verified": ("DTR counts verified", False),
    "cosp_form": ("COSP Performance Form submitted", False), "computed": ("Result computed", False),
    "flag_kept": ("Flagged form kept", False), "flag_voided": ("Flagged form voided", False),
    "comment_release": ("Comment released", False), "comment_edit": ("Comment edited before release", True),
    "comment_withhold": ("Comment withheld", True), "viewed_raw": ("Raw comments or flagged forms viewed", False),
    "review_approved": ("Next-level review: approved", False), "review_returned": ("Next-level review: returned", True),
    "released": ("Result released", False), "conference": ("Feedback conference recorded", False),
    "acknowledged": ("Receipt acknowledged", False), "contest": ("Result contested", False),
    "contest_ao": ("Contest: HR reviewed, sent to the AO", True),
    "contest_coh": ("Contest: endorsed to the COH", True), "contest_upheld": ("Contest decided: result upheld", True),
    "contest_returned": ("Contest decided: returned for correction", True), "final": ("Result final", False), "reopened": ("Period reopened", True),
    "plan_sent": ("Development plan sent", False), "plan_agreed": ("Development plan agreed", False),
    "plan_changes_asked": ("Development plan: changes asked", False),
    "plan_checkin": ("Development plan check-in recorded", True),
    "plan_checkin_missed": ("Development plan check-in marked missed by the system", True),
    "plan_closed": ("Development plan closed", False),
}


def _rows_from_performance():
    from performance.models import CriterionChange, DeductionEvent, PeriodEvent, PMSetting

    rows = []
    for e in PeriodEvent.objects.select_related("period__employee", "user"):
        label, with_note = PM_PERIOD_ACTIONS.get(e.action, (e.action.replace("_", " ").capitalize(), False))
        emp = e.period.employee
        detail = f"{e.period.get_period_type_display()} {e.period.start_date:%b %d, %Y} - {e.period.end_date:%b %d, %Y}"
        if with_note and e.note:
            detail += f": {e.note}"
        # a closed form's actor is the rater; never shown
        actor = "Rater (confidential)" if e.action == "form_closed" else e.user
        rows.append({"timestamp": e.at, "module": "Performance", "action": label, "detail": detail, "actor": actor,
                     "employee_ids": {emp.pk}, "employee_display": str(emp)})
    for e in DeductionEvent.objects.select_related("deduction__employee", "deduction__criterion", "user"):
        d = e.deduction
        detail = f"{d.criterion.code} {d.criterion.name}, incident {d.incident_date:%b %d, %Y}"
        if e.note:
            detail += f": {e.note}"
        rows.append({"timestamp": e.at, "module": "Performance", "action": f"Incident: {e.action.replace('_', ' ')}",
                     "detail": detail, "actor": e.user, "employee_ids": {d.employee_id},
                     "employee_display": str(d.employee)})
    for s in PMSetting.objects.exclude(entered_by=None).select_related("entered_by"):
        rows.append({"timestamp": s.entered_at, "module": "Performance", "action": "Setting changed",
                     "detail": f"{s.key} = {s.value} from {s.effective_from:%b %d, %Y} ({s.approval_ref})",
                     "actor": s.entered_by, "employee_ids": set(), "employee_display": "—"})
    for c in CriterionChange.objects.select_related("criterion", "changed_by"):
        rows.append({"timestamp": c.changed_at, "module": "Performance", "action": "Deduction criterion changed",
                     "detail": f"{c.criterion.code} {c.field}: {c.old_value} -> {c.new_value} ({c.approval_ref})",
                     "actor": c.changed_by, "employee_ids": set(), "employee_display": "—"})
    return rows


def audit_rows(employee_id=None, module=None, date_from=None, date_to=None):
    """
    Merged, filtered, newest-first list of audit rows across all
    sources. Filtering happens in Python after each source applies its own
    date filter at the DB level, since the sources have no common table to
    join on.
    """
    rows = []
    rows += _rows_from_employee_edit_history(EmployeeEditHistory.objects.all())
    rows += _rows_from_request_actions(LeaveApplicationAction.objects.all(), "Leave", "application")
    rows += _rows_from_request_actions(CTOUsageApplicationAction.objects.all(), "CTO", "application")
    rows += _rows_from_date_changes()
    rows += _rows_from_request_actions(CTOClaimAction.objects.all(), "CTO Claim", "claim")
    rows += _rows_from_request_actions(DutyExchangeRequestAction.objects.all(), "Exchange of Duty", "request")
    rows += _rows_from_request_actions(
        AttendanceCorrectionRequestAction.objects.all(), "Attendance Correction", "request"
    )
    rows += _rows_from_request_actions(OfficialRequestAction.objects.all(), "Official Request", "request")
    rows += _rows_from_document_events(UploadedDocumentEvent.objects.all())
    rows += _rows_from_announcement_actions()
    rows += _rows_from_schedule_events()
    rows += _rows_from_exchange_settings()
    rows += _rows_from_holidays_and_history()
    rows += _rows_from_performance()
    rows += _rows_from_oic_designations()
    from outonpass.models import MissedTapAction, OutOnPassAction
    rows += _rows_from_request_actions(OutOnPassAction.objects.all(), "Out-on-Pass", "out_on_pass")
    rows += _rows_from_request_actions(MissedTapAction.objects.select_related("missed_tap__out_on_pass__employee"),
                                       "Out-on-Pass", "missed_tap")
    rows += _rows_from_out_on_pass_setup()

    if employee_id:
        rows = [r for r in rows if employee_id in r["employee_ids"]]
    if module:
        rows = [r for r in rows if r["module"] == module]
    if date_from:
        rows = [r for r in rows if r["timestamp"].date() >= date_from]
    if date_to:
        rows = [r for r in rows if r["timestamp"].date() <= date_to]

    rows.sort(key=lambda r: r["timestamp"], reverse=True)
    return rows


MODULE_CHOICES = [
    "Employee Record",
    "Leave",
    "CTO",
    "Overtime",
    "CTO Claim",
    "Exchange of Duty",
    "Attendance Correction",
    "Official Request",
    "Documents",
    "Announcements",
    "Duty Schedule",
    "OIC Designation",
    "Out-on-Pass",
    "Holidays",
    "Assignment History",
    "Performance",
]
