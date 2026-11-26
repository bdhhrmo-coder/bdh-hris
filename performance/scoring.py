"""TPE calculation, Design Package v1.1 §5.1 (rebuilt 2026-11-26).

Settings are read as in force on the period's end date. Full precision is
stored; one decimal is shown, rounding half up."""

from decimal import ROUND_HALF_UP, Decimal

from . import rules
from .models import EvaluationPeriod as P, RaterAssignment as A, Response

D0, D5, D100 = Decimal("0"), Decimal("5"), Decimal("100")

# component -> (label, groups, weight key)
RANK_PARTS = [("perf", "Performance", None, "w_rank_perf"), ("sup", "Immediate supervisor", [A.SUPERVISOR], "w_rank_sup"),
              ("peer", "Peers", [A.PEER, A.POOLED], "w_rank_peer"), ("att", "Attendance", None, "w_rank_att")]
SUP_PARTS = [("obj", "Objective performance", None, "w_sup_obj"), ("top", "Top management", [A.TOP], "w_sup_top"),
             ("sub", "Subordinates", [A.SUBORDINATE], "w_sup_sub"), ("peer", "Supervisor peers", [A.SUP_PEER], "w_sup_peer")]
CONFIDENTIAL_PARTS = {"peer", "sub"}


def show(value):
    """One decimal, half up."""
    if value is None:
        return None
    return Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def scored_codes(form):
    return [f"A{i}" for i in range(1, 13)] if form == "A" else [f"B{i}" for i in range(1, 16)]


def rater_average(a, period):
    """(average or None, reason). N/O excluded; a form with more than the
    allowed share of N/O is not counted (subordinates: items B4-B12 only, N5)."""
    codes = scored_codes(a.form)
    resp = {r.indicator.code: r for r in Response.objects.filter(assignment=a, indicator__code__in=codes)
            .select_related("indicator")}
    test = [f"B{i}" for i in range(4, 13)] if a.group == A.SUBORDINATE else codes
    no = sum(1 for c in test if c not in resp or resp[c].not_observed)
    if Decimal(no) / Decimal(len(test)) > rules.num("max_no_share", period):
        return None, "too many N/O"
    values = [Decimal(r.value) for r in resp.values() if not r.not_observed and r.value]
    if not values:
        return None, "no rated items"
    return sum(values) / len(values), ""


def counted(period, groups):
    """Assignments that count: submitted and not voided after a flag review."""
    return [a for a in period.raters.filter(group__in=groups, status=A.SUBMITTED).exclude(flag_review="VOIDED")]


def group_mean(period, part, groups):
    """(mean, n, note, averages) for a rater group."""
    avgs = []
    for a in counted(period, groups):
        avg, _ = rater_average(a, period)
        if avg is not None:
            avgs.append((a, avg))
    n = len(avgs)
    note = ""
    if part in CONFIDENTIAL_PARTS:
        min_n = int(rules.num("min_confidential_n", period))
        if n < min_n:
            return None, n, f"skipped: {n} valid rater(s), {min_n} needed" + (", never pooled" if part == "sub" else ""), avgs
        trim = int(rules.num("trim_threshold", period))
        values = sorted(v for _, v in avgs)
        if trim and n >= trim:
            values = values[1:-1]
            note = "highest and lowest trimmed"
        if any(a.group == A.POOLED for a, _ in avgs):
            note = (note + "; " if note else "") + "pooled"
        return sum(values) / len(values), n, note, avgs
    if n == 0:
        return None, 0, "skipped: no submitted form", avgs
    return sum(v for _, v in avgs) / n, n, note, avgs


def attendance_score(period):
    if period.absence_days is None or period.tardy_days is None or period.dtr_verified_by_id is None:
        return None
    score = D100 - rules.num("absence_points", period) * period.absence_days \
        - rules.num("tardy_points", period) * period.tardy_days
    return max(D0, score)


def performance_input(period):
    """(rating on 1-5, label) or (None, waiting text)."""
    if rules.is_cosp(period.employee):
        form = getattr(period, "cosp_form", None)
        if form is None or form.submitted_at is None or form.score is None:
            return None, "awaiting the COSP Performance Form"
        return Decimal(form.score), "COSP Performance Form"
    if period.certified_rating is None or period.certified_verified_by_id is None:
        return None, "awaiting PHRMO certification"
    return Decimal(period.certified_rating), "PHRMO-certified rating"


def score(period):
    """{'parts': [...], 'subtotal', 'deductions', 'total', 'missing': [...]}"""
    from .deductions import period_deductions

    parts_def = RANK_PARTS if period.track == P.RANK else SUP_PARTS
    parts, missing = [], []
    for i, (code, label, groups, wkey) in enumerate(parts_def):
        w = rules.num(wkey, period)
        item = {"component": code, "label": label, "weight_nominal": w, "mean": None, "n": 0, "note": "",
                "skipped": False, "pooled": False, "sort_order": i}
        if code in ("perf", "obj"):
            value, text = performance_input(period)
            item["note"] = text
            if value is None:
                missing.append(text)
            item["mean"], item["n"] = value, 1 if value is not None else 0
        elif code == "att":
            value = attendance_score(period)
            if value is None:
                missing.append("awaiting verified DTR counts")
                item["note"] = "awaiting verified DTR counts"
            item["mean"], item["n"] = value, 1 if value is not None else 0
        else:
            mean, n, note, _ = group_mean(period, code, groups)
            item.update(mean=mean, n=n, note=note, skipped=mean is None, pooled="pooled" in note)
        parts.append(item)
    live = [p for p in parts if not p["skipped"]]
    total_w = sum(p["weight_nominal"] for p in live) or Decimal(1)
    subtotal = D0
    for p in parts:
        if p["skipped"]:
            p["weight_used"], p["points"] = D0, D0
            continue
        p["weight_used"] = p["weight_nominal"] / total_w * D100
        if p["mean"] is None:
            p["points"] = D0
            continue
        scale = D100 if p["component"] == "att" else D5
        p["points"] = Decimal(p["mean"]) / scale * p["weight_used"]
        subtotal += p["points"]
    ded_rows, ded_total = period_deductions(period)
    cap = rules.num("deduction_cap", period)
    ded = min(cap, ded_total)
    total = None if missing else max(D0, subtotal - ded)
    return {"parts": parts, "subtotal": subtotal, "deductions": ded, "deduction_rows": ded_rows,
            "deduction_raw": ded_total, "total": total, "missing": missing}


def description(total, period):
    if total is None:
        return ""
    t = show(total)
    for key, label in (("band_exemplary", "Exemplary"), ("band_strong", "Strong"), ("band_solid", "Solid"),
                       ("band_developing", "Developing")):
        if t >= rules.num(key, period):
            return label
    return "Needs close support"


DESCRIPTIONS = {
    "Exemplary": "Consistently shows the expected behaviors and results; a model for others.",
    "Strong": "Often goes beyond what is expected; a few areas to refine.",
    "Solid": "Meets expectations in most areas, with clear areas to develop.",
    "Developing": "Meets some expectations; needs a focused development plan.",
    "Needs close support": "Below expectations in several areas; the supervisor agrees a support plan with the employee.",
}


# -- flags ----------------------------------------------------------------------------------

def flags_for(a, period):
    codes = scored_codes(a.form)
    resp = list(Response.objects.filter(assignment=a, indicator__code__in=codes, not_observed=False))
    values = [r.value for r in resp if r.value]
    out = []
    if len(values) >= 5 and len(set(values)) == 1:
        out.append("straight-line")
    if a.first_shown_at and a.submitted_at and values:
        seconds = (a.submitted_at - a.first_shown_at).total_seconds()
        if seconds < float(rules.num("fast_seconds_per_item", period)) * len(codes):
            out.append("fast")
    if a.group in (A.PEER, A.POOLED, A.SUP_PEER) and period.window_id:
        if A.objects.filter(period__window=period.window, period__employee=a.rater, rater=period.employee,
                            group__in=[A.PEER, A.POOLED, A.SUP_PEER], status=A.SUBMITTED).exists():
            out.append("reciprocal")
    return out


def mark_flags(period):
    """Sets flags on submitted forms (outliers inside confidential groups too).
    Returns the number of forms newly waiting for review."""
    new = 0
    by_group = {}
    for a in counted(period, [g for g, _ in A.GROUPS if g != A.SELF]):
        avg, _ = rater_average(a, period)
        by_group.setdefault("peer" if a.group in (A.PEER, A.POOLED) else a.group, []).append((a, avg))
    gap = rules.num("outlier_gap", period)
    for key, rows in by_group.items():
        for a, avg in rows:
            flags = flags_for(a, period)
            others = [v for b, v in rows if b.pk != a.pk and v is not None]
            if a.is_confidential and avg is not None and len(others) >= 2:
                if abs(avg - sum(others) / len(others)) > gap:
                    flags.append("outlier")
            text = ",".join(flags)
            if text and a.flag_review not in ("KEPT", "VOIDED"):
                if a.flags != text or a.flag_review != "PENDING":
                    new += 1
                a.flags, a.flag_review = text, "PENDING"
                a.save(update_fields=["flags", "flag_review"])
    return new
