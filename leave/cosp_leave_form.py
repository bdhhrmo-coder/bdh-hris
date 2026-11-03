"""
Renders the custom BDH-branded COSP leave application form (CLAUDE.md
§6.3, Form No. BDH-ADM-HR-01F04-A) for every leave of a COSP employee:
COSP Leave, and since 2026-11-03 (owner, dry-run item 3) Wellness and
Emergency Leave too, which now go through the full COSP chain. Regular
employees' leave (incl. their Wellness/Emergency) prints on CSC Form 6.
"""

from printouts.sheet import FormBuilder
from printouts.stamps import StepDef, compute_stamps, with_unit_head_step

from .models import LeaveType
from .balances import compute_available_balance, contract_years_started
from .pdf_convert import workbook_to_pdf

FORM_CODE = "BDH-ADM-HR-01F04-A"

ROUTING = [
    StepDef("Applicant", "submit", "DIGITALLY FILED"),
    StepDef("Supervisor", "endorse", "DIGITALLY ENDORSED"),
    StepDef("HR (credits verified)", "process", "DIGITALLY PROCESSED"),
    StepDef("Administrative Officer", "recommend", "DIGITALLY RECOMMENDED"),
    StepDef("Chief of Hospital", "approve", "DIGITALLY APPROVED"),
]


def reference_no(application):
    return f"LV-{application.pk:06d}"


def fill_cosp_leave_form(application, printed_by=None):
    """Returns an openpyxl Workbook with `application`'s data filled in."""
    employee = application.employee
    leave_type = application.leave_type

    f = FormBuilder(
        f"APPLICATION FOR {leave_type.name.upper()}", FORM_CODE,
        subtitle="(Contract of Service Personnel — internal BDH leave benefit)",
    )

    f.section("1.  APPLICANT INFORMATION")
    f.field("Name:", employee.full_name)
    f.field("Position:", employee.position)
    f.field("Section/Unit:", ", ".join(s.name for s in employee.sections.all()))
    f.field("Contract Period:", f"Starting {employee.date_hired:%B %d, %Y}" if employee.date_hired else "")
    f.field("Date Filed:", f"{application.submitted_at:%m/%d/%Y}")
    f.gap()

    f.section("2.  LEAVE DETAILS")
    f.field("Type of Leave:", leave_type.name + (f" - {application.get_wellness_reason_display()}"
                                                 if application.wellness_reason else ""))
    f.field("Inclusive Dates:", f"{application.start_date:%B %d, %Y} to {application.end_date:%B %d, %Y}")
    f.field("No. of Days:", str(application.number_of_days))
    f.field("Justification:" if leave_type.requires_justification else "Purpose (optional):",
            application.justification)
    f.gap()

    f.section(f"3.  {leave_type.name.upper()} CREDITS (as of the first day of leave)")
    as_of = application.start_date
    if leave_type.balance_tracking == LeaveType.TRACKING_UNTRACKED:
        earned = None  # e.g. Emergency Leave: no fixed allotment in the system
    elif leave_type.is_cumulative:
        earned = (leave_type.annual_fixed_days or 0) * contract_years_started(employee.date_hired, as_of)
    else:
        earned = leave_type.annual_fixed_days or 0  # this year's allotment (Wellness)
    available_before = compute_available_balance(employee, leave_type, as_of_date=as_of)
    used_to_date = (earned - available_before) if available_before is not None and earned is not None else None
    show = lambda v: "—" if v is None else str(v)  # noqa: E731
    f.table(
        ["Total Earned to Date", "Less: Used to Date", "Balance Before This Application"],
        [[show(earned), show(used_to_date), show(available_before)]],
        [("B", "C"), ("D", "E"), ("F", "G")],
    )
    f.gap()

    f.section("4.  ROUTING AND ACTION (digitally processed)")
    f.gap()
    actions = list(application.actions.select_related("acted_by__employee"))
    stamps, stopped = compute_stamps(actions, with_unit_head_step(ROUTING, actions, [application.employee]))
    f.stamps(stamps, stopped)
    f.footer(reference_no(application), printed_by)
    return f.finish()


def render_pdf(application, printed_by=None) -> bytes:
    """Fills the form and converts it to PDF bytes via headless LibreOffice
    (see leave/pdf_convert.py for how the executable is located)."""
    return workbook_to_pdf(fill_cosp_leave_form(application, printed_by), "COSP Leave form")
