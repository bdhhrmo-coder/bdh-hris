"""
Travel Order printout (owner decisions 2026-11-03).

Official Business (Travel Order) is approved by the Governor at the Capitol,
not inside BDH. The HRIS fills in BDH's own Travel Order template (letterhead,
header and footer kept from printouts/assets/travel_order_base.docx) with
wet-signature lines - no BDH form code and no digital stamps, the same
treatment as CSC Form 6 for regular leave:

  - rank-and-file: "RECOMMENDING APPROVAL" by the Chief of Hospital;
  - the Chief of Hospital's own Travel Order: no recommending line;
  - "Certified as to Availability of Funds" (Provincial Budget Officer) only
    when Per Diem is requested; "APPROVED BY" the Governor always.

A group request prints one Travel Order listing every employee on it.
Names of officials come from TravelOrderSettings.
"""

from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from leave.pdf_convert import docx_to_pdf

from .models import OfficialRequest, TravelOrderSettings

BASE = Path(settings.BASE_DIR) / "printouts" / "assets" / "travel_order_base.docx"
FONT = "Century Gothic"
SIZE = Pt(12)


# -- data ---------------------------------------------------------------------------

def _person_line(emp):
    middle = f" {emp.middle_name[0]}." if emp.middle_name else ""
    ext = f" {emp.name_extension}" if emp.name_extension else ""
    return f"{emp.first_name}{middle} {emp.surname}{ext}".upper(), (emp.position or "").upper()


def _period(dates):
    dates = sorted(set(dates))
    first, last = dates[0], dates[-1]
    consecutive = (last - first).days + 1 == len(dates)
    fmt = lambda d: f"{d:%B} {d.day:02d}, {d.year}"
    if first == last:
        return fmt(first)
    if consecutive:
        return f"{fmt(first)} to {fmt(last)}"
    return "; ".join(fmt(d) for d in dates)


def _days(start, end):
    from datetime import timedelta

    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _prepared_date(actions, fallback):
    """The date HR processed it (the order is prepared by HR), else the filing date."""
    hr = [a for a in actions if a.action == "process"]
    when = hr[-1].acted_at if hr else fallback
    return timezone.localtime(when).date() if hasattr(when, "tzinfo") else when


def data_for_request(req):
    req_dates = _days(req.start_date, req.end_date)
    return dict(
        people=[_person_line(req.employee)], period=_period(req_dates), destination=req.destination,
        purpose=req.purpose, transport=req.transportation_allowed, expenses=req.expenses_authorized,
        coh_own=req.recorded_only,
        date=_prepared_date(list(req.actions.order_by("acted_at", "pk")), req.submitted_at),
    )


def data_for_batch(batch):
    lines = list(batch.active_lines().select_related("employee"))
    employees = sorted({ln.employee for ln in lines}, key=lambda e: (e.surname, e.first_name))
    dates = [d for ln in lines for d in _days(ln.start_date, ln.end_date)]
    return dict(
        people=[_person_line(e) for e in employees], period=_period(dates), destination=batch.destination,
        purpose=batch.purpose, transport=batch.transportation_allowed, expenses=batch.expenses_authorized,
        coh_own=False,
        date=_prepared_date(list(batch.actions.order_by("acted_at", "pk")), batch.filed_at),
    )


def printable(status, recorded_only=False):
    """Printed for signing once the COH has recommended it (or, for the COH's
    own Travel Order, once filed); kept printable after the Governor approves."""
    if recorded_only:
        return status in (OfficialRequest.SUBMITTED, OfficialRequest.RECORDED)
    return status in (OfficialRequest.FORWARDED_TO_CAPITOL, OfficialRequest.APPROVED)


# -- document -----------------------------------------------------------------------

def _run(p, text, bold=False, underline=False, italic=False, size=SIZE, color=None):
    r = p.add_run(text)
    r.bold, r.underline, r.italic = bold, underline, italic
    r.font.name, r.font.size = FONT, size
    r.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    if color:
        r.font.color.rgb = color
    return r


def _para(doc_or_cell, align=None, before=0, after=0, indent=None):
    p = doc_or_cell.add_paragraph()
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    if align is not None:
        p.alignment = align
    if indent is not None:
        pf.left_indent = indent
    return p


def _no_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tbl_pr.append(borders)


def _cell_text(cell, text, bold=False, underline=False):
    cell.paragraphs[0].paragraph_format.space_after = Pt(0)
    _run(cell.paragraphs[0], text, bold=bold, underline=underline)


def _set_widths(table, widths):
    table.autofit = False
    for row in table.rows:
        for cell, w in zip(row.cells, widths):
            cell.width = w
    for gc, w in zip(table._tbl.tblGrid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(int(w.twips)))


def build_document(data):
    s = TravelOrderSettings.get()
    doc = Document(str(BASE))

    if getattr(settings, "TEST_SYSTEM", False):
        _run(_para(doc, WD_ALIGN_PARAGRAPH.CENTER, after=4),
             "TEST SYSTEM PRINTOUT - NOT A VALID TRAVEL ORDER", bold=True, color=RGBColor(0xC9, 0x36, 0x3E))

    d = data["date"]
    _run(_para(doc, before=6, indent=Cm(11.4)), f"{d:%B} {d.day}, {d.year}", bold=True)
    _run(_para(doc, before=10), "TRAVEL ORDER", bold=True, underline=True, size=Pt(14))
    _run(_para(doc), "NUMBER_______", size=Pt(11))
    p = _para(doc, after=10)
    _run(p, "Series of ", italic=True, size=Pt(11))
    _run(p, str(d.year), italic=True, underline=True, size=Pt(11))

    rows = []
    for i, (name, position) in enumerate(data["people"]):
        rows.append(("Name/Position" if i == 0 else "", name, True))
        rows.append(("", position, True))
    rows += [("Office", s.office, True), ("Destination", data["destination"], True),
             ("Periods of Travel", data["period"], True)]
    purposes = [ln.strip() for ln in (data["purpose"] or "").splitlines() if ln.strip()] or [""]
    if len(purposes) > 1:
        purposes = [f"{i}. {t}" for i, t in enumerate(purposes, 1)]
    for i, text in enumerate(purposes):
        rows.append(("Purpose of Travel" if i == 0 else "", text, True))
    expenses = {OfficialRequest.PER_DIEM: "Per Diem",
                OfficialRequest.EXPENSES_OFFICIAL_TIME: "Official Time"}.get(data["expenses"], "")
    rows += [("Transportation Allowed", data["transport"], True), ("Expenses Authorized", expenses, True)]

    table = doc.add_table(rows=0, cols=3)
    _no_borders(table)
    for label, value, bold in rows:
        cells = table.add_row().cells
        _cell_text(cells[0], label)
        _cell_text(cells[1], ":" if label else "")
        _cell_text(cells[2], value or "", bold=bold)
    _set_widths(table, [Cm(5.2), Cm(0.6), Cm(11.0)])

    if not data["coh_own"]:
        _run(_para(doc, before=18, after=36), "RECOMMENDING APPROVAL:")
        _run(_para(doc), s.coh_name, bold=True, underline=True)
        _run(_para(doc), s.coh_title, bold=True)

    per_diem = data["expenses"] == OfficialRequest.PER_DIEM
    sig = doc.add_table(rows=3, cols=2)
    _no_borders(sig)
    sig.alignment = WD_TABLE_ALIGNMENT.LEFT
    left, right = sig.columns[0].cells, sig.columns[1].cells
    if per_diem:
        _cell_text(left[0], "Certified as to Availability of Funds:", bold=True)
        left[0].add_paragraph()
        _run(left[0].paragraphs[1], f"Responsibility Center: {s.responsibility_center}", bold=True)
        _cell_text(left[1], "\n\n\n" + s.budget_officer_name, bold=True, underline=True)
        _cell_text(left[2], s.budget_officer_title, bold=True)
    right[0].paragraphs[0].paragraph_format.space_before = Pt(12)
    _cell_text(right[0], "\nAPPROVED BY:", bold=True)
    _cell_text(right[1], "\n\n\n" + s.governor_name, bold=True, underline=True)
    _cell_text(right[2], s.governor_title, bold=True)
    for cell in right:
        cell.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_widths(sig, [Cm(10.0), Cm(6.8)])
    sig.rows[0].cells[0].paragraphs[0].paragraph_format.space_before = Pt(18)
    return doc


def render_pdf(data):
    return docx_to_pdf(build_document(data), "Travel Order")
