"""
Printable Exchange of Duty application - Form No. BDH-ADM-HR-01F04-C (code
assigned by BDH, 2026-10-06). Same look as the COSP Leave form (01F04-A).

Paperless by default: printing is incidental. The two employees' filing
and consent, and each approval step, print as "digitally processed/
approved" stamps (name, position, date, time) from
DutyExchangeRequestAction instead of signature lines. See printouts/.
"""

from leave.pdf_convert import workbook_to_pdf
from printouts.sheet import FormBuilder
from printouts.stamps import StepDef, compute_stamps

FORM_CODE = "BDH-ADM-HR-01F04-C"

ROUTING = [
    StepDef("Employee A (requesting)", "file", "DIGITALLY FILED"),
    StepDef("Employee B (consenting)", "consent", "DIGITALLY CONSENTED"),
    StepDef("Supervisor", "endorse", "DIGITALLY ENDORSED"),
    StepDef("HR", "process", "DIGITALLY PROCESSED"),
    StepDef("Administrative Officer", "recommend", "DIGITALLY RECOMMENDED"),
    StepDef("Chief of Hospital", "approve", "DIGITALLY APPROVED"),
]


def reference_no(request):
    return f"EOD-{request.pk:06d}"


def _sections(employee):
    return ", ".join(s.name for s in employee.sections.all())


def fill_exchange_form(req, printed_by=None):
    f = FormBuilder("APPLICATION FOR EXCHANGE OF DUTY", FORM_CODE)
    a, b = req.employee_a, req.employee_b

    f.section("1.  EMPLOYEES AND DUTY DATES")
    f.table(
        ["Employee", "Original Duty Date", "Will Render Duty On"],
        [
            [f"A:  {a.full_name}\n{a.position}", f"{req.date_a:%b %d, %Y}", f"{req.date_b:%b %d, %Y}"],
            [f"B:  {b.full_name}\n{b.position}", f"{req.date_b:%b %d, %Y}", f"{req.date_a:%b %d, %Y}"],
        ],
        [("A", "C"), ("D", "D"), ("E", "G")],
    )
    for r in (f.row - 2, f.row - 1):
        f.ws.row_dimensions[r].height = 30
    f.gap()

    f.section("2.  DETAILS")
    f.field("Section/Unit (A):", _sections(a))
    f.field("Section/Unit (B):", _sections(b))
    f.field("Date Filed:", f"{req.filed_at:%m/%d/%Y}")
    f.field("Reason:", req.reason)
    if req.is_emergency:
        f.field("Emergency:", f"Yes — {req.emergency_justification}")
    f.note(
        "One-for-one exchange of duty. It does not create overtime, CTO, or additional pay "
        "(BDH HR Policy on CTO and Exchange of Duty, approved December 16, 2025).",
        height=24,
    )
    f.gap()

    f.section("3.  CONSENT, ROUTING AND ACTION (digitally processed)")
    f.gap()
    stamps, stopped = compute_stamps(req.actions.select_related("acted_by__employee"), ROUTING)
    f.stamps(stamps, stopped)
    f.footer(reference_no(req), printed_by)
    return f.finish()


def render_pdf(req, printed_by=None) -> bytes:
    return workbook_to_pdf(fill_exchange_form(req, printed_by), "Exchange of Duty form")
