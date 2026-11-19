"""
Out-on-Pass Slip printout - BDH-ADM-AO-01F16, Revision 1 layout (draft
attached to the DICR, 2026-11-14), filled in from the HRIS record.

The form (printouts/assets/f16_base.docx) is filled as it is: boxes are
ticked (☐ -> ☒), blanks get the values, and the "Filed by / Endorsed by /
Approved by" rows carry the digital stamps from the pass's action log
(current cycle), as on BDH's other forms (CLAUDE.md 6.3.1). Printing is on
request; the HRIS record is the official one.
"""

from pathlib import Path

from django.conf import settings
from django.utils import timezone
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from employees.models import Employee
from leave.pdf_convert import docx_to_pdf
from printouts.stamps import actor_name, actor_position

from . import rules
from .models import MissedTap
from .models import OutOnPass as P

BASE = Path(settings.BASE_DIR) / "printouts" / "assets" / "f16_base.docx"
FONT = "Arial"
VALUE = Pt(9)
BLUE = RGBColor(0x17, 0x3D, 0x72)
GREY = RGBColor(0x80, 0x80, 0x80)
RED = RGBColor(0xC9, 0x36, 0x3E)

KIND_LABEL = {P.ORDINARY: "Personal pass", P.THIRD: "Third pass this month",
              P.EXCEPTION: "Exception (beyond balance or 2 hours)",
              P.EMERGENCY: "Documented emergency pass (filed after leaving)"}
REASON_LABEL = dict(P.REASON_CHOICES)


def _cells(row):
    seen, out = [], []
    for c in row.cells:
        if c._tc not in seen:
            seen.append(c._tc)
            out.append(c)
    return out


def _run(p, text, bold=False, size=VALUE, color=None, italic=False):
    r = p.add_run(text)
    r.bold, r.italic = bold, italic
    r.font.name, r.font.size = FONT, size
    r.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
    if color is not None:
        r.font.color.rgb = color
    return r


def _put(cell, text, bold=True):
    p = cell.paragraphs[0]
    for r in p.runs:
        r.text = ""
    _run(p, str(text or ""), bold=bold)


def _tick(cell, label):
    """Tick the box in front of `label` (text like '☐ Permanent')."""
    for p in cell.paragraphs:
        for r in p.runs:
            if f"☐ {label}" in r.text:
                r.text = r.text.replace(f"☐ {label}", f"☒ {label}", 1)
                return True
    return False


def _fill_blank(cell, after, value):
    """Replace the first '____' after `after` with the value."""
    for p in cell.paragraphs:
        for r in p.runs:
            i = r.text.find(after)
            if i >= 0:
                j = r.text.find("_", i + len(after))
                if j >= 0:
                    k = j
                    while k < len(r.text) and r.text[k] == "_":
                        k += 1
                    r.text = r.text[:j] + str(value) + r.text[k:]
                    return


def _stamp_cell(cell, stamp, empty):
    p = cell.paragraphs[0]
    for r in p.runs:
        r.text = ""
    if stamp is None:
        _run(p, empty, bold=True, color=GREY)
        return
    _run(p, f"{stamp['word']}  ", bold=True, color=BLUE, size=Pt(8))
    _run(p, stamp["name"].upper(), bold=True)
    if stamp["position"]:
        _run(p, f"  ·  {stamp['position']}", size=Pt(8))
    _run(p, f"  ·  {timezone.localtime(stamp['when']):%b %d, %Y %I:%M %p}", size=Pt(8))


def _stamp(action, word):
    if action is None:
        return None
    return {"word": word, "name": actor_name(action.acted_by), "position": actor_position(action.acted_by),
            "when": action.acted_at}


def _cycle(actions):
    starts = [i for i, a in enumerate(actions) if a.action in ("submit", "resubmit")]
    return actions[starts[-1]:] if starts else actions


def _last(actions, *names):
    found = [a for a in actions if a.action in names]
    return found[-1] if found else None


def _area(e):
    units = ", ".join(u.name for u in e.units.all())
    sections = ", ".join(s.name for s in e.sections.all())
    return " / ".join(x for x in (units, sections) if x)


def _employment(e):
    if e.employment_status == Employee.EMPLOYMENT_STATUS_COSP:
        return "PGSS (Security)" if e.cosp_under == Employee.COSP_UNDER_PGSS else "COSP"
    return "Permanent"


def _local(dt, fmt="%b %d, %Y %I:%M %p"):
    return f"{timezone.localtime(dt):{fmt}}" if dt else ""


def build_document(p):
    actions = _cycle(list(p.actions.select_related("acted_by__employee").order_by("acted_at", "pk")))
    e = p.employee
    doc = Document(str(BASE))
    rows = doc.tables[0].rows

    # Part I
    c = _cells(rows[1]); _put(c[1], e.full_name); _put(c[3], f"{p.pass_date:%B %d, %Y}")
    c = _cells(rows[2]); _put(c[1], e.position or ""); _put(c[3], _area(e))
    _tick(_cells(rows[3])[1], _employment(e))
    c = _cells(rows[4]); _put(c[1], p.pass_number); _put(c[3], _local(p.filed_at))
    _tick(_cells(rows[5])[1], KIND_LABEL[p.kind])
    c = _cells(rows[6]); _put(c[1], f"{p.time_out:%I:%M %p}"); _put(c[3], f"{p.time_in:%I:%M %p}")
    reason_cell = _cells(rows[7])[1]
    _tick(reason_cell, REASON_LABEL[p.reason_type])
    _fill_blank(reason_cell, "Details:", f" {p.reason}")
    _stamp_cell(_cells(rows[8])[1], _stamp(_last(actions, "submit", "resubmit"), "DIGITALLY FILED"), "")

    # Part II - the Supervisor step (an AO/COH who endorsed and approved in one step counts here too)
    endorse = _last(actions, "endorse")
    approve = _last(actions, "approve", "record")
    one_step = endorse is None and approve is not None and not p.recorded_only
    returned = _last(actions, "return", "reject")
    part2 = _cells(rows[10])[0]
    if endorse or one_step:
        _tick(part2, "Endorsed")
        if p.confirmed_coverage:
            _tick(part2, "The unit can be covered.")
        if p.confirmed_no_surge:
            _tick(part2, "No surge or Code activation is ongoing.")
        if p.confirmed_shift_hours:
            _tick(part2, "Not in the first or last hour")
    elif returned and p.status in (P.RETURNED, P.REJECTED) and returned.resulting_status != P.ENDORSED:
        _tick(part2, "Returned.")
        _fill_blank(part2, "Reason:", f" {returned.notes}")
    if p.covering_staff:
        _fill_blank(part2, "staff covering the unit:", f" {p.covering_staff}")
    if p.recorded_only:
        _stamp_cell(_cells(rows[11])[1], None, "NOT APPLICABLE (CHIEF OF HOSPITAL'S OWN PASS)")
    else:
        _stamp_cell(_cells(rows[11])[1], _stamp(endorse or (approve if one_step else None), "DIGITALLY ENDORSED"),
                    "PENDING")

    # Part III - system check
    others = rules.balance_minutes(e, p.pass_date.year, exclude_pk=p.pk)
    c = _cells(rows[13])
    _put(c[1], rules.hm(others))
    _put(c[3], str(rules.passes_in_month(e, p.pass_date.year, p.pass_date.month)))
    part3 = _cells(rows[14])[0]
    _tick(part3, {P.ORDINARY: "Within all limits", P.THIRD: "THIRD PASS", P.EXCEPTION: "Exception requested",
                  P.EMERGENCY: "Documented emergency pass"}[p.kind])

    # Part IV - approval
    part4 = _cells(rows[16])[0]
    if p.status in (P.APPROVED, P.RECORDED, P.COMPLETED) or (approve and p.status not in (P.RETURNED, P.REJECTED)):
        _tick(part4, "Approved")
    elif p.status == P.REJECTED and returned:
        _tick(part4, "Disapproved.")
        _fill_blank(part4, "Reason:", f" {returned.notes}")
    word = "RECORDED BY HR" if p.recorded_only else "DIGITALLY APPROVED"
    _stamp_cell(_cells(rows[17])[1], _stamp(approve, word), "PENDING")

    # Part V - gate times
    c = _cells(rows[19]); _put(c[1], _local(p.tapped_out_at, "%I:%M %p")); _put(c[3], _local(p.tapped_in_at, "%I:%M %p"))
    missed = list(p.missed_taps.filter(status=MissedTap.REVIEWED).prefetch_related("actions__acted_by__employee"))
    guards = []
    part5 = _cells(rows[21])[0]
    if missed:
        _tick(part5, "Confirmed by the supervisor")
        _tick(part5, "Time verified by the guard on duty")
        _tick(part5, "Reviewed by HRMO")
        _fill_blank(part5, "missed tap:", " " + "; ".join(
            f"{m.get_which_display()} {_local(m.time_to_record, '%I:%M %p')}" for m in missed))
        for m in missed:
            for a in m.actions.all():
                if a.action == "verify":
                    guards.append(actor_name(a.acted_by))
                if a.action == "review":
                    _fill_blank(part5, "(digital stamp):", f" {actor_name(a.acted_by)}, {_local(a.acted_at)}")
    _put(_cells(rows[20])[1], ", ".join(guards), bold=False)

    # Part VI - hours charged
    c = _cells(rows[23])
    if p.status == P.COMPLETED:
        _put(c[1], rules.hm(p.minutes_charged or 0))
        _put(c[3], str(p.excess_minutes or 0))

    if p.status in (P.RETURNED, P.REJECTED, P.CANCELLED):
        _banner(doc, f"{p.get_status_display().upper()}" + (f": {returned.notes}" if returned and returned.notes else ""))
    if getattr(settings, "TEST_SYSTEM", False):
        _banner(doc, "TEST SYSTEM PRINTOUT - NOT A VALID SLIP")
    return doc


def _banner(doc, text):
    from docx.text.paragraph import Paragraph

    first = doc.tables[0]._tbl
    new_p = doc.element.body.makeelement(qn("w:p"), {})
    first.addprevious(new_p)
    _run(Paragraph(new_p, doc._body), text, bold=True, color=RED, size=Pt(10))


def render_pdf(p):
    return docx_to_pdf(build_document(p), "Out-on-Pass Slip")
