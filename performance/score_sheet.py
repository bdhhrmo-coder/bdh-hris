"""Printable TPE score sheet (Design Package v1.1 §5.5; gap 2, 2026-11-26).

Same builder and letterhead as the other BDH forms (printouts/sheet.py). The
form code is not assigned yet, so the HRMO placeholder prints. Paperless like
the 01F04 series: each step prints as a digital stamp from the period's log
instead of a signature line (CLAUDE.md §6.3.1)."""

from leave.pdf_convert import workbook_to_pdf
from printouts.sheet import FormBuilder
from printouts.stamps import Stamp, actor_name, actor_position, format_when

from . import deductions, rules, scoring
from .models import EvaluationPeriod as P

FORM_CODE = "[form code to be assigned by HRMO]"


def reference_no(period):
    return f"TPE-{period.pk:06d}"


def _stamp(label, text, event):
    if event is None:
        return Stamp(role_label=label, state="pending", text="PENDING")
    return Stamp(role_label=label, state="done", text=text, name=actor_name(event.user) if event.user else "System",
                 position=actor_position(event.user) if event.user else "", when=format_when(event.at))


def stamps(period):
    last = {}
    for e in period.events.select_related("user__employee"):
        last[e.action] = e
    out = [_stamp("Prepared (HR)", "DIGITALLY COMPUTED", last.get("computed"))]
    if rules.is_cosp(period.employee):
        out.append(_stamp("Reviewed (next level)", "DIGITALLY REVIEWED", last.get("review_approved")))
    out.append(_stamp("Released (HR Administrator)", "DIGITALLY RELEASED", last.get("released")))
    if period.acknowledged_at:
        user = period.employee.user
        out.append(Stamp(role_label="Received by the employee", state="done",
                         text="RECEIVED (NOT NECESSARILY AGREED)", name=period.employee.full_name,
                         position=actor_position(user) if user else "", when=format_when(period.acknowledged_at)))
    else:
        out.append(Stamp(role_label="Received by the employee", state="pending", text="PENDING"))
    return out


def build(period, printed_by=None):
    from outonpass.report import area_of

    emp = period.employee
    f = FormBuilder("TOTAL PERFORMANCE EVALUATION (TPE) SCORE SHEET", FORM_CODE,
                    subtitle="BDH Performance Evaluation. This is not the official SPMS rating.")
    f.section("1.  EMPLOYEE AND PERIOD")
    f.field("Name:", emp.full_name)
    f.field("Position:", emp.position)
    f.field("Section/Unit:", area_of(emp))
    f.field("Employment Type:", emp.get_employment_status_display())
    f.field("Period:", f"{period.get_period_type_display()}, {period.start_date:%B %d, %Y} to "
                       f"{period.end_date:%B %d, %Y} ({period.get_track_display()} track)")
    f.field("Status:", period.get_status_display())
    f.gap()

    f.section("2.  COMPONENTS")
    spans = [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"), ("E", "E"), ("F", "G")]
    rows = []
    for c in period.components.all():
        rows.append([c.label, f"{c.weight_used:.1f}", "—" if c.mean is None else f"{c.mean:.2f}", str(c.n),
                     f"{scoring.show(c.points)}", c.note or ("skipped" if c.skipped else "")])
    f.table(["Component", "Weight used", "Mean", "n", "Points", "Note"], rows, spans)
    f.gap()

    f.section("3.  DEDUCTIONS (final decisions only)")
    ded_rows, raw = deductions.period_deductions(period)
    cap = rules.num("deduction_cap", period)
    if ded_rows:
        f.table(["Criterion", "Incident date", "Final on", "Points", "", "Cap applied"],
                [[f"{r['d'].criterion.code} {r['d'].criterion.name}", f"{r['d'].incident_date:%b %d, %Y}",
                  f"{r['d'].final_at:%b %d, %Y}" if r["d"].final_at else "", f"{r['points']:.2f}", "",
                  r["note"] or ("criterion cap" if r["capped"] else "")] for r in ded_rows],
                [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D"), ("E", "E"), ("F", "G")])
        if raw > cap:
            f.note(f"Deductions total {raw:.2f}; the period cap of {cap:.0f} points applies.", size=9)
    else:
        f.note("None.", size=10, italic=False)
    for d in deductions.elsewhere_notes(period):
        f.note(f"Note: {d.criterion.code} (incident {d.incident_date:%b %d, %Y}) counts in another period; a "
               "deduction counts in one period only.", size=9)
    for d in deductions.late_notes(period):
        f.note(f"Note: {d.criterion.code} became final after release; it does not change this total.", size=9)
    f.gap()

    f.section("4.  TOTAL")
    f.field("Subtotal:", scoring.show(period.subtotal))
    f.field("Deductions:", f"-{scoring.show(period.deduction_total or 0)}")
    f.field("TPE total:", f"{scoring.show(period.tpe_total)}  ({scoring.description(period.tpe_total, period)})")
    f.note("How it is worked out: each part's average (1 to 5) ÷ 5 × its weight used; attendance is 100 minus the "
           "points for unauthorized absences and tardy days, ÷ 100 × its weight. A part without enough raters is "
           "left out and its weight is shared out among the others. Deductions come off the total, at most "
           f"{cap:.0f} points, never below 0. Shown to one decimal; the parts may differ from the total by 0.1 "
           "because of rounding.", height=40)
    f.note("Permitted uses: development, rewards and recognition (with the PHRMO-certified rating), and "
           "supplementary input to personnel decisions. Never the sole basis for any adverse action.", height=26)
    if period.disagreement_note:
        f.field("Employee's note:", period.disagreement_note, height=30)
    f.gap()

    f.section("5.  ROUTING AND ACTION (digitally processed)")
    f.gap()
    f.stamps(stamps(period))
    f.footer(reference_no(period), printed_by)
    return f.finish()


def render_pdf(period, printed_by=None) -> bytes:
    return workbook_to_pdf(build(period, printed_by), "TPE score sheet")


def may_print(acting, period):
    from .result_views import can_view_result

    if period.tpe_total is None or period.status in (P.PLANNED, P.OPEN, P.CANCELLED):
        return False
    return can_view_result(acting, period)
