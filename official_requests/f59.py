"""
F59 printout - Travel Authorization and Transportation Request
(BDH-ADM-AO-01F59, Rev 0), for an approved Local Official Errand (owner
request 2026-11-03).

BDH's own form (printouts/assets/f59_base.docx, the file HR supplied) is
filled in as it is: two identical copies on one page with the cut line
between them (one for the requestor, one for HR). Every value comes from the
approved request; nothing is typed at print time. Like BDH's other forms
(CLAUDE.md 6.3.1) the signature boxes carry the digital stamps from the
request's action log (current cycle) instead of wet signatures:
  Requested  - the employee who filed (or the filer of a group request);
  Noted      - the Supervisor / unit head who endorsed (for a group request
               filed by a Supervisor, the filer's step counts as done);
  Approved   - the AO (or the COH when the AO is on the request).
Type of Transport: Ambulance / Service Vehicle, ticked from the request
(stored in transportation_allowed for an errand). Printable once approved.
"""

from pathlib import Path

from django.conf import settings
from django.utils import timezone
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from leave.pdf_convert import docx_to_pdf
from printouts.stamps import actor_name, actor_position

from .models import OfficialRequest

BASE = Path(settings.BASE_DIR) / "printouts" / "assets" / "f59_base.docx"
FONT = "Arial Narrow"
VALUE = Pt(10)
STAMP = Pt(7)
BLUE = RGBColor(0x17, 0x3D, 0x72)
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"

AMBULANCE = "Ambulance"
SERVICE_VEHICLE = "Service Vehicle"
VEHICLE_CHOICES = [("", "None (walking / own means)"), (AMBULANCE, AMBULANCE), (SERVICE_VEHICLE, SERVICE_VEHICLE)]


def printable(status):
    return status == OfficialRequest.APPROVED


# -- data ---------------------------------------------------------------------------

def _cycle(actions):
    """Actions since the latest filing/resubmission."""
    starts = [i for i, a in enumerate(actions) if a.action in ("submit", "file", "resubmit", "edit")]
    return actions[starts[-1]:] if starts else actions


def _stamp(action, word):
    if action is None:
        return None
    return {"word": word, "name": actor_name(action.acted_by), "position": actor_position(action.acted_by),
            "when": timezone.localtime(action.acted_at)}


def _last(actions, *names):
    found = [a for a in actions if a.action in names]
    return found[-1] if found else None


def _when(t):
    return t.strftime("%I:%M %p").lstrip("0") if t else ""


def _date(d):
    return f"{d:%b} {d.day}, {d.year}" if d else ""


def _sections(emp):
    return ", ".join(s.name for s in emp.sections.all())


def data_for_request(req):
    acts = _cycle(list(req.actions.select_related("acted_by__employee").order_by("acted_at", "pk")))
    filed = _last(acts, "submit", "file", "resubmit", "edit")
    return dict(
        filed=timezone.localtime(req.submitted_at).date(), requestor=req.employee,
        trip_date=req.start_date, return_date=req.end_date, depart=req.time_from, back=req.time_to,
        destination=req.destination, purpose=req.purpose, vehicle=req.transportation_allowed,
        riders=[req.employee], special=req.line_note,
        requested=_stamp(filed, "DIGITALLY FILED") or {"word": "FILED", "name": req.employee.full_name,
                                                        "position": req.employee.position or "", "when": None},
        noted=_stamp(_last(acts, "endorse", "endorse_unit"), "DIGITALLY NOTED"),
        approved=_stamp(_last(acts, "approve"), "DIGITALLY APPROVED"),
    )


def data_for_batch(batch):
    from .models import OfficialRequestBatch as B

    acts = _cycle(list(batch.actions.select_related("acted_by__employee").order_by("acted_at", "pk")))
    lines = list(batch.active_lines().select_related("employee"))
    riders = sorted({ln.employee for ln in lines}, key=lambda e: (e.surname, e.first_name))
    filed = _last(acts, "submit", "resubmit", "edit")
    noted = _last(acts, "endorse", "endorse_unit")
    noted_stamp = _stamp(noted, "DIGITALLY NOTED")
    if noted_stamp is None and batch.filer_role in (B.FILER_SUPERVISOR, B.FILER_UNIT_HEAD) and filed is not None:
        noted_stamp = _stamp(filed, "FILED BY THE SUPERVISOR")
    filer = getattr(batch.filed_by, "employee", None)
    return dict(
        filed=timezone.localtime(batch.filed_at).date(), requestor=filer,
        trip_date=min(ln.start_date for ln in lines) if lines else None,
        return_date=max(ln.end_date for ln in lines) if lines else None,
        depart=min((ln.time_from for ln in lines if ln.time_from), default=None),
        back=max((ln.time_to for ln in lines if ln.time_to), default=None),
        destination=batch.destination, purpose=batch.purpose, vehicle=batch.transportation_allowed,
        riders=riders, special="",
        requested=_stamp(filed, "DIGITALLY FILED"), noted=noted_stamp,
        approved=_stamp(_last(acts, "approve"), "DIGITALLY APPROVED"),
    )


# -- document -----------------------------------------------------------------------

def _run(p, text, bold=False, size=VALUE, color=None, underline=False):
    r = p.add_run(text)
    r.bold, r.underline = bold, underline
    r.font.name, r.font.size = FONT, size
    r.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
    if color is not None:
        r.font.color.rgb = color
    return r


def _cells(row):
    seen, out = [], []
    for c in row.cells:
        if c._tc not in seen:
            seen.append(c._tc)
            out.append(c)
    return out


def _put(cell, text, bold=True):
    """Value under the cell's label: the first empty paragraph, else a new one."""
    target = next((p for p in cell.paragraphs[1:] if not p.text.strip()), None) or cell.add_paragraph()
    lines = str(text or "").splitlines() or [""]
    for i, line in enumerate(lines):
        if i:
            target.add_run().add_break()
        _run(target, line, bold=bold)


def _tick(cell, which):
    """Tick the Ambulance (0) or Service Vehicle (1) box of the form's checkboxes."""
    boxes = cell._tc.xpath(".//w:sdt")
    if which is None or which >= len(boxes):
        return
    sdt = boxes[which]
    for el in sdt.xpath(".//*[local-name()='checked']"):
        el.set(f"{{{W14}}}val", "1")
    for t in sdt.xpath(".//*[local-name()='t']"):
        t.text = "☒"


def _sign(cell, stamp, empty_text):
    """Signature box: the stamp line above the signature line, the name on it."""
    stamp_p, line_p = cell.paragraphs[1], cell.paragraphs[2]
    for r in line_p.runs:
        r.text = ""
    if stamp is None:
        _run(stamp_p, empty_text, bold=True, size=STAMP, color=RGBColor(0x80, 0x80, 0x80))
        _run(line_p, "_" * 37)
        return
    when = f" {stamp['when']:%b %d, %Y %I:%M %p}" if stamp["when"] else ""
    _run(stamp_p, f"{stamp['word']}{when}", bold=True, size=STAMP, color=BLUE)
    _run(line_p, stamp["name"].upper(), bold=True, underline=True)
    if stamp["position"]:
        _run(line_p, f"  {stamp['position']}", size=Pt(8))


def _fill(table, d):
    rows = table.rows
    date_filed, name, designation, dept = _cells(rows[2])
    _put(date_filed, _date(d["filed"]))
    req = d["requestor"]
    _put(name, req.full_name if req else "")
    _put(designation, (req.position or "") if req else "")
    _put(dept, _sections(req) if req else "")
    trip, depart, ret, back, count = _cells(rows[4])
    _put(trip, _date(d["trip_date"]))
    _put(depart, _when(d["depart"]))
    _put(ret, _date(d["return_date"]))
    _put(back, _when(d["back"]))
    _put(count, str(len(d["riders"])))
    dest, transport, riders = _cells(rows[5])
    _put(dest, d["destination"])
    _tick(transport, {AMBULANCE: 0, SERVICE_VEHICLE: 1}.get(d["vehicle"]))
    if not d["vehicle"]:
        _run(transport.paragraphs[1], "None requested", size=Pt(8))
    _put(riders, "\n".join(f"{i}. {e.full_name}" for i, e in enumerate(d["riders"], 1)), bold=False)
    _put(_cells(rows[6])[0], d["purpose"])
    _put(_cells(rows[7])[0], d["special"] or "")
    requested, noted, approved = _cells(rows[8])
    _sign(requested, d["requested"], "")
    _sign(noted, d["noted"], "NOT REQUIRED (FILED BY HR)" if d["noted"] is None and d["approved"] else "PENDING")
    _sign(approved, d["approved"], "PENDING")


def build_document(data):
    from docx.text.paragraph import Paragraph

    doc = Document(str(BASE))
    body = doc.element.body
    # Keep both copies on one page: drop the empty spacer paragraphs between
    # and after the tables; the paragraph holding the cut line stays.
    cut_kept = False
    for p in list(doc.paragraphs):
        if p._p.getparent() is not body or p.text.strip():
            continue
        is_cut = bool(p._p.xpath("./w:pPr/w:pBdr"))
        if is_cut and not cut_kept:
            cut_kept = True
            continue
        if p._p.getprevious() is not None:
            body.remove(p._p)
    for table in doc.tables:
        _fill(table, data)
        # A copy never splits across pages: if the two don't fit on one page
        # (other fonts), the second copy moves whole to the next page.
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    para.paragraph_format.keep_with_next = True
    if getattr(settings, "TEST_SYSTEM", False):
        first = doc.tables[0]._tbl
        new_p = body.makeelement(qn("w:p"), {})
        first.addprevious(new_p)
        _run(Paragraph(new_p, doc._body), "TEST SYSTEM PRINTOUT - NOT A VALID F59", bold=True,
             color=RGBColor(0xC9, 0x36, 0x3E))
    return doc


def render_pdf(data):
    return docx_to_pdf(build_document(data), "F59 Travel Authorization")
