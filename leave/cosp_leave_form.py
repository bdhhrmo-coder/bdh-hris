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

Design notes, flagged rather than silently assumed:
  - "BDH-HR-COSP-01" is a form code I'm assigning for internal filing —
    it is not an official CSC/DBM form number, since COSP Leave is a
    BDH/contract-of-service benefit, not a Civil Service leave type.
  - The routing block (6) prints the applicant's name and action date for
    every step already taken (pulled from LeaveApplicationAction, the
    same accountability log the queue screens use) and leaves the rest
    blank for steps not yet reached. A signature line is always printed
    underneath each name — a printed name from the system record is not
    a substitute for the actual signature CLAUDE.md's "preserve human
    control" principle calls for.
  - The BDH seal (form_templates/bdh_logo.png) is placed at the top left
    of the letterhead, matching how the Provincial Government seal
    appears on CSC Form 6.
"""

from pathlib import Path

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, Side

from .balances import compute_available_balance, contract_years_started
from .pdf_convert import workbook_to_pdf

LOGO_PATH = Path(__file__).parent / "form_templates" / "bdh_logo.png"
THIN = Side(style="thin")
BOX = Border(top=THIN, bottom=THIN, left=THIN, right=THIN)
BOTTOM = Border(bottom=THIN)


def _actor_name(user):
    employee = getattr(user, "employee", None)
    if employee is not None:
        return employee.full_name
    return user.get_full_name() or user.username


def _merge_and_set(ws, cell_range, value, *, bold=False, italic=False, size=11, align="left", border=None):
    ws.merge_cells(cell_range)
    top_left = cell_range.split(":")[0]
    cell = ws[top_left]
    cell.value = value
    cell.font = Font(bold=bold, italic=italic, size=size, name="Arial")
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
    if border:
        for row in ws[cell_range]:
            for c in row:
                c.border = border


def fill_cosp_leave_form(application):
    """Returns an openpyxl Workbook with `application`'s data filled in."""
    employee = application.employee
    leave_type = application.leave_type

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "COSP Leave"
    ws.sheet_view.showGridLines = False
    for col, width in zip("ABCDEFG", [22, 11, 11, 18, 9, 9, 4]):
        ws.column_dimensions[col].width = width
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    # --- Letterhead ------------------------------------------------------
    ws["G1"] = "Form No. BDH-HR-COSP-01"
    ws["G1"].font = Font(size=8, italic=True, name="Arial")
    ws["G1"].alignment = Alignment(horizontal="right")

    _merge_and_set(ws, "A2:G2", "Republic of the Philippines", italic=True, align="center")
    _merge_and_set(ws, "A3:G3", "PROVINCIAL GOVERNMENT OF PALAWAN", bold=True, size=13, align="center")
    _merge_and_set(ws, "A4:G4", "BATARAZA DISTRICT HOSPITAL", bold=True, size=13, align="center")
    _merge_and_set(ws, "A5:G5", "Barangay Marangas, Bataraza, Palawan", italic=True, size=9, align="center")
    _merge_and_set(ws, "A7:G7", "APPLICATION FOR COSP LEAVE", bold=True, size=15, align="center")
    _merge_and_set(
        ws, "A8:G8", "(Contract of Service Personnel — internal BDH leave benefit)",
        italic=True, size=9, align="center",
    )

    if LOGO_PATH.exists():
        logo = XLImage(str(LOGO_PATH))
        logo.width = 70
        logo.height = 70
        ws.add_image(logo, "A1")

    row = 10

    def label_value(label, value, span="B:D", height=1):
        nonlocal row
        ws[f"A{row}"] = label
        ws[f"A{row}"].font = Font(bold=True, size=10, name="Arial")
        start_col, end_col = span.split(":")
        _merge_and_set(ws, f"{start_col}{row}:{end_col}{row}", value, size=11, border=BOTTOM)
        row += height

    # --- 1. Applicant ------------------------------------------------------
    _merge_and_set(ws, f"A{row}:G{row}", "1.  APPLICANT INFORMATION", bold=True, size=11)
    ws[f"A{row}"].fill = openpyxl.styles.PatternFill("solid", fgColor="DDDDDD")
    for c in ws[f"A{row}:G{row}"][0]:
        c.fill = openpyxl.styles.PatternFill("solid", fgColor="DDDDDD")
    row += 1

    label_value("Name:", employee.full_name, span="B:G")
    label_value("Position:", employee.position, span="B:G")
    sections = ", ".join(s.name for s in employee.sections.all())
    label_value("Section/Unit:", sections, span="B:G")
    label_value(
        "Contract Period:",
        f"Starting {employee.date_hired:%B %d, %Y}" if employee.date_hired else "",
        span="B:G",
    )
    label_value("Date Filed:", f"{application.submitted_at:%m/%d/%Y}", span="B:G")
    row += 1

    # --- 2. Leave details ----------------------------------------------
    _merge_and_set(ws, f"A{row}:G{row}", "2.  LEAVE DETAILS", bold=True, size=11)
    for c in ws[f"A{row}:G{row}"][0]:
        c.fill = openpyxl.styles.PatternFill("solid", fgColor="DDDDDD")
    row += 1

    label_value(
        "Inclusive Dates:",
        f"{application.start_date:%B %d, %Y} to {application.end_date:%B %d, %Y}",
        span="B:G",
    )
    label_value("No. of Days:", str(application.number_of_days), span="B:G")
    label_value("Purpose (optional):", application.justification, span="B:G")
    row += 1

    # --- 3. Leave credit certification ----------------------------------
    _merge_and_set(ws, f"A{row}:G{row}", "3.  CERTIFICATION OF COSP LEAVE CREDITS", bold=True, size=11)
    for c in ws[f"A{row}:G{row}"][0]:
        c.fill = openpyxl.styles.PatternFill("solid", fgColor="DDDDDD")
    row += 1

    as_of = application.start_date
    grants = contract_years_started(employee.date_hired, as_of)
    earned = (leave_type.annual_fixed_days or 0) * grants
    available_before = compute_available_balance(employee, leave_type, as_of_date=as_of)
    used_to_date = (earned - available_before) if available_before is not None else None

    headers = ["", "Total Earned to Date", "Less: Used to Date", "Balance Before This Application"]
    for i, h in enumerate(headers[1:], start=0):
        col = ["B", "D", "F"][i]
        end_col = ["C", "E", "G"][i]
        _merge_and_set(ws, f"{col}{row}:{end_col}{row}", h, bold=True, size=9, align="center", border=BOX)
    row += 1
    values = [earned, used_to_date, available_before]
    for i, v in enumerate(values):
        col = ["B", "D", "F"][i]
        end_col = ["C", "E", "G"][i]
        _merge_and_set(ws, f"{col}{row}:{end_col}{row}", str(v) if v is not None else "—", align="center", border=BOX)
    row += 2

    ws[f"A{row}"] = "Certified correct by (HR):"
    ws[f"A{row}"].font = Font(bold=True, size=9, italic=True, name="Arial")
    row += 1

    # --- 4. Routing ------------------------------------------------------
    _merge_and_set(ws, f"A{row}:G{row}", "4.  ROUTING", bold=True, size=11)
    for c in ws[f"A{row}:G{row}"][0]:
        c.fill = openpyxl.styles.PatternFill("solid", fgColor="DDDDDD")
    row += 1

    actions_by_type = {a.action: a for a in application.actions.all()}
    routing_steps = [
        ("Applicant", "submit"),
        ("Supervisor (Endorsement)", "endorse"),
        ("HR (Processed)", "process"),
        ("Administrative Officer (Recommendation)", "recommend"),
        ("Chief of Hospital (Approval)", "approve"),
    ]

    _merge_and_set(ws, f"A{row}:C{row}", "Step", bold=True, size=9, align="center", border=BOX)
    _merge_and_set(ws, f"D{row}:E{row}", "Name", bold=True, size=9, align="center", border=BOX)
    _merge_and_set(ws, f"F{row}:G{row}", "Date", bold=True, size=9, align="center", border=BOX)
    row += 1

    for label, action_code in routing_steps:
        action = actions_by_type.get(action_code)
        name = _actor_name(action.acted_by) if action else ""
        acted_date = f"{action.acted_at:%m/%d/%Y}" if action else ""
        _merge_and_set(ws, f"A{row}:C{row}", label, size=9, border=BOX)
        _merge_and_set(ws, f"D{row}:E{row}", name, size=10, align="center", border=BOX)
        _merge_and_set(ws, f"F{row}:G{row}", acted_date, size=10, align="center", border=BOX)
        row += 1
        _merge_and_set(ws, f"D{row}:E{row}", "(Signature over printed name)", italic=True, size=7, align="center", border=BOTTOM)
        row += 1

    ws.print_area = f"A1:G{row - 1}"
    return wb


def render_pdf(application) -> bytes:
    """Fills the form and converts it to PDF bytes via headless LibreOffice
    (see leave/pdf_convert.py for how the executable is located)."""
    return workbook_to_pdf(fill_cosp_leave_form(application), "COSP Leave form")
