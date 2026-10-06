"""
Printable CTO application - Form No. BDH-ADM-HR-01F04-B (code assigned by
BDH, 2026-10-06). Same look as the COSP Leave form (01F04-A).

Paperless by default: printing is incidental. Routing steps print as
"digitally processed/approved" stamps (name, position, date, time) from
CTOUsageApplicationAction instead of signature lines. See printouts/.
"""

from leave.pdf_convert import workbook_to_pdf
from printouts.sheet import FormBuilder
from printouts.stamps import StepDef, compute_stamps

from .balances import compute_available_cto_balance

FORM_CODE = "BDH-ADM-HR-01F04-B"

ROUTING = [
    StepDef("Applicant", "submit", "DIGITALLY FILED"),
    StepDef("Supervisor", "endorse", "DIGITALLY ENDORSED"),
    StepDef("HR (credits verified)", "process", "DIGITALLY PROCESSED"),
    StepDef("Administrative Officer", "recommend", "DIGITALLY RECOMMENDED"),
    StepDef("Chief of Hospital", "approve", "DIGITALLY APPROVED"),
]


def reference_no(application):
    return f"CTO-{application.pk:06d}"


def fill_cto_form(application, printed_by=None):
    employee = application.employee
    f = FormBuilder("APPLICATION FOR COMPENSATORY TIME-OFF (CTO)", FORM_CODE)

    f.section("1.  APPLICANT INFORMATION")
    f.field("Name:", employee.full_name)
    f.field("Position:", employee.position)
    f.field("Section/Unit:", ", ".join(s.name for s in employee.sections.all()))
    f.field("Employment Status:", employee.get_employment_status_display())
    f.field("Date Filed:", f"{application.submitted_at:%m/%d/%Y}")
    f.gap()

    f.section("2.  CTO DETAILS")
    f.field("Inclusive Dates:", f"{application.start_date:%B %d, %Y} to {application.end_date:%B %d, %Y}")
    f.field("No. of Days:", str(application.number_of_days))
    f.field("Reason (optional):", application.reason)
    f.field("CTO Balance:", f"{compute_available_cto_balance(employee)} day(s) as of printing")
    f.gap()

    f.section("3.  ROUTING AND ACTION (digitally processed)")
    f.gap()
    stamps, stopped = compute_stamps(application.actions.select_related("acted_by__employee"), ROUTING)
    f.stamps(stamps, stopped)
    f.footer(reference_no(application), printed_by)
    return f.finish()


def render_pdf(application, printed_by=None) -> bytes:
    return workbook_to_pdf(fill_cto_form(application, printed_by), "CTO application form")
