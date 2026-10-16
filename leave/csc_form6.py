"""
Fills the actual CSC Form No. 6 (2020 revision, BDH copy — signatories
already printed on it: PGDH Rolando B. Buñi, Chief of Hospital Dr. Bon
Karlo C. Ladia, Governor Amy Roa Alvarez) rather than redrawing the form
from scratch, per the project's institutional-template-fidelity rule.

Only the blank answer cells are touched; everything else in the template
(headers, legal citations, signatories, instructions pages) is left as-is.

Column A next to each leave-type label (rows 16-28) is that leave type's
checkbox on the real form. COSP Leave prints on its own custom form (see
cosp_leave_form.py) — it never uses this one. Wellness and Emergency Leave
have no row of their own on the official CSC form, so they're marked
against the "Others:" line with the leave type name written in.

Batch 8 (2026-10-16): 6.B Details of Leave and 6.D Commutation are now
collected on the application and printed in their own boxes and lines
(cell map: DETAILS_6B below, taken from the template's borders). Wellness
Leave has no row or 6.B line on the 2020 form, so its reason is written on
the "Others:" line with the leave type: "Wellness Leave - Mental Health Care".
"""

from copy import copy
from decimal import Decimal
from pathlib import Path

import openpyxl

from .pdf_convert import workbook_to_pdf

TEMPLATE_PATH = Path(__file__).parent / "form_templates" / "CSC_Form_6_blank.xlsx"

# Position titles are free text and can run long ("Nursing Attendant / OIC,
# ER Unit"); the official form only gives that field ~26 characters of
# width before it would overlap "5. SALARY" next to it. Rather than
# truncating someone's job title, shrink the font just enough to fit.
POSITION_FIELD_MAX_CHARS = 26
POSITION_FIELD_MIN_FONT_SIZE = 8


def _write_shrink_to_fit(ws, coordinate, text, max_chars, min_size):
    cell = ws[coordinate]
    cell.value = text
    if text and len(text) > max_chars:
        base_size = cell.font.sz or 11
        fitted_size = max(min_size, base_size * max_chars / len(text))
        new_font = copy(cell.font)
        new_font.sz = fitted_size
        cell.font = new_font

# leave_type.code -> the row of its checkbox in column A on the real form.
LEAVE_TYPE_CHECKBOX_ROW = {
    "VL": 16,
    "MANDATORY": 17,
    "SL": 18,
    "MATERNITY": 19,
    "PATERNITY": 20,
    "SPL": 21,
    "SOLO_PARENT": 22,
    "STUDY": 23,
    "VAWC": 24,
    "REHAB": 25,
    "SPECIAL_WOMEN": 26,
    "CALAMITY": 27,
    "ADOPTION": 28,
}


# 6.B Details of Leave: checkbox cells (column R) and the ruled answer lines.
CHECK_PH, LINE_PH = "R17", "Y17"            # Within the Philippines ______ (Y17:AD17)
CHECK_ABROAD, LINE_ABROAD = "R18", "X18"    # Abroad (Specify) ______ (X18:AD18)
CHECK_HOSPITAL, LINE_HOSPITAL = "R20", "Z20"  # In Hospital (Specify Illness) ______ (Z20:AD20)
CHECK_OUTPATIENT, LINE_OUTPATIENT = "R21", "Z21"  # Out Patient (Specify Illness) ______ (Z21:AD21)
LINE_SL_MORE = "R22"                        # second ruled line under Sick Leave (R22:AD22)
LINE_SLBW, LINE_SLBW_MORE = "V24", "R25"    # Special Leave Benefits for Women (Specify Illness)
CHECK_MASTERS, CHECK_BAR = "R27", "R28"
CHECK_MONETIZATION, CHECK_TERMINAL = "R30", "R31"
CHECK_NOT_REQUESTED, CHECK_REQUESTED = "R35", "R36"  # 6.D Commutation
# How many characters fit on each ruled line at LINE_FONT_SIZE before the text
# would run past the form's right border (measured on the printed PDF).
LINE_FONT_SIZE = 8
LINE_CHARS = {LINE_PH: 30, LINE_ABROAD: 35, LINE_HOSPITAL: 24, LINE_OUTPATIENT: 24, LINE_SL_MORE: 66,
              LINE_SLBW: 44, LINE_SLBW_MORE: 66}
LINE_MIN_FONT_SIZE = 6.5


def _put(ws, coordinate, text, size):
    cell = ws[coordinate]
    cell.value = " " + text  # a small gap after the printed label
    font = copy(cell.font)
    font.sz = size
    cell.font = font


def _write_line(ws, coordinate, text, more=None):
    """Write `text` on a ruled 6.B line at 8 pt. If it is too long, the rest
    goes on the next ruled line (`more`) when the form has one; whatever
    still does not fit is set in a smaller font (never below 6.5 pt) so
    nothing runs past the form's border."""
    if not text:
        return
    room = LINE_CHARS[coordinate]
    if len(text) > room and more:
        cut = text.rfind(" ", 0, room)
        cut = cut if cut > room // 2 else room
        first, rest = text[:cut].strip(), text[cut:].strip()
        _put(ws, coordinate, first, LINE_FONT_SIZE)
        coordinate, text, room = more, rest, LINE_CHARS[more]
    size = LINE_FONT_SIZE if len(text) <= room else max(LINE_MIN_FONT_SIZE, LINE_FONT_SIZE * room / len(text))
    _put(ws, coordinate, text, size)


def _fill_details_6b(ws, a):
    L = type(a)
    if a.location == L.LOCATION_PH:
        ws[CHECK_PH] = "X"
        _write_line(ws, LINE_PH, a.location_detail)
    elif a.location == L.LOCATION_ABROAD:
        ws[CHECK_ABROAD] = "X"
        _write_line(ws, LINE_ABROAD, a.location_detail)
    if a.sick_leave_type == L.SICK_HOSPITAL:
        ws[CHECK_HOSPITAL] = "X"
        _write_line(ws, LINE_HOSPITAL, a.illness, more=LINE_SL_MORE)
    elif a.sick_leave_type == L.SICK_OUTPATIENT:
        ws[CHECK_OUTPATIENT] = "X"
        _write_line(ws, LINE_OUTPATIENT, a.illness, more=LINE_SL_MORE)
    elif a.illness:  # Special Leave Benefits for Women
        _write_line(ws, LINE_SLBW, a.illness, more=LINE_SLBW_MORE)
    if a.study_purpose == L.STUDY_MASTERS:
        ws[CHECK_MASTERS] = "X"
    elif a.study_purpose == L.STUDY_BAR:
        ws[CHECK_BAR] = "X"
    if a.other_purpose == L.PURPOSE_MONETIZATION:
        ws[CHECK_MONETIZATION] = "X"
    elif a.other_purpose == L.PURPOSE_TERMINAL:
        ws[CHECK_TERMINAL] = "X"


def fill_csc_form6(application):
    """Returns an openpyxl Workbook with `application`'s data filled in."""
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    ws = wb["blank"]
    employee = application.employee

    ws["K11"] = employee.surname
    ws["T11"] = employee.first_name
    ws["Z11"] = employee.middle_name

    ws["F12"] = application.submitted_at.strftime("%m/%d/%Y")  # narrow cell (F12:J12) — long month names overflow
    _write_shrink_to_fit(ws, "O12", employee.position, POSITION_FIELD_MAX_CHARS, POSITION_FIELD_MIN_FONT_SIZE)
    ws["AA12"] = employee.salary_grade

    code = application.leave_type.code
    row = LEAVE_TYPE_CHECKBOX_ROW.get(code)
    if row:
        ws.cell(row=row, column=1, value="X")
    else:
        ws["A30"] = "X"
        other = application.leave_type.name
        if application.wellness_reason:
            other = f"{other} - {application.get_wellness_reason_display()}"
        # "Others:" (B30) is answered on the ruled line below it (merged B31:O31)
        _write_shrink_to_fit(ws, "B31", other, 70, 8)

    _fill_details_6b(ws, application)

    ws["B34"] = f"{Decimal(str(application.number_of_days)).normalize():f}"
    if application.start_date and application.end_date:
        if application.start_date == application.end_date:
            ws["B36"] = f"{application.start_date:%B %d, %Y}"
        else:
            ws["B36"] = f"{application.start_date:%B %d, %Y} to {application.end_date:%B %d, %Y}"
    else:
        ws["B36"] = "Not applicable"  # monetization / terminal leave
    requested = application.commutation == type(application).COMMUTATION_REQUESTED
    ws[CHECK_REQUESTED if requested else CHECK_NOT_REQUESTED] = "X"

    return wb


def render_pdf(application) -> bytes:
    """Fills the form and converts it to PDF bytes via headless LibreOffice
    (see leave/pdf_convert.py for how the executable is located)."""
    return workbook_to_pdf(fill_csc_form6(application), "CSC Form 6")
