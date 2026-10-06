"""
Renders the custom BDH-branded COSP Leave application form (CLAUDE.md
§6.3). Unlike CSC Form 6, there is no pre-existing official file to reuse
here — the user confirmed no such form was ever actually saved anywhere,
so this is designed from scratch.

Only LeaveType.code == "COSP_LEAVE" prints on this form. Wellness and
Emergency Leave availed by a COSP employee still print on CSC Form 6 (see
csc_form6.py) — CLAUDE.md §6.3 reserves the custom form for COSP Leave
specifically; "Other leave types" (§6.3) covers every CSC-listed type
regardless of who is applying.

Design notes:
  - Form code BDH-ADM-HR-01F04-A was assigned by BDH (2026-10-06). It is a
    BDH form, not an official CSC/DBM form - COSP Leave is a contract-of-
    service benefit, not a Civil Service leave type.
  - Paperless by default (2026-10-06): printing is incidental. The routing
    section prints a "digitally processed/approved" stamp for each step
    already taken - name, position, date and time from
    LeaveApplicationAction - instead of signature lines to be signed by
    hand. Steps not reached yet print as PENDING. See printouts/.
"""

from printouts.sheet import FormBuilder
from printouts.stamps import StepDef, compute_stamps

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
        "APPLICATION FOR COSP LEAVE", FORM_CODE,
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
    f.field("Inclusive Dates:", f"{application.start_date:%B %d, %Y} to {application.end_date:%B %d, %Y}")
    f.field("No. of Days:", str(application.number_of_days))
    f.field("Purpose (optional):", application.justification)
    f.gap()

    f.section("3.  COSP LEAVE CREDITS (as of the first day of leave)")
    as_of = application.start_date
    earned = (leave_type.annual_fixed_days or 0) * contract_years_started(employee.date_hired, as_of)
    available_before = compute_available_balance(employee, leave_type, as_of_date=as_of)
    used_to_date = (earned - available_before) if available_before is not None else None
    show = lambda v: "—" if v is None else str(v)  # noqa: E731
    f.table(
        ["Total Earned to Date", "Less: Used to Date", "Balance Before This Application"],
        [[show(earned), show(used_to_date), show(available_before)]],
        [("B", "C"), ("D", "E"), ("F", "G")],
    )
    f.gap()

    f.section("4.  ROUTING AND ACTION (digitally processed)")
    f.gap()
    stamps, stopped = compute_stamps(application.actions.select_related("acted_by__employee"), ROUTING)
    f.stamps(stamps, stopped)
    f.footer(reference_no(application), printed_by)
    return f.finish()


def render_pdf(application, printed_by=None) -> bytes:
    """Fills the form and converts it to PDF bytes via headless LibreOffice
    (see leave/pdf_convert.py for how the executable is located)."""
    return workbook_to_pdf(fill_cosp_leave_form(application, printed_by), "COSP Leave form")
