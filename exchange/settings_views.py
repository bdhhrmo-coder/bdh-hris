"""HR Administration pages for the Exchange of Duty rules (owner decisions
2026-11-26): Positions and Exchange Groups, and the duty-check settings.
HR Administrator only; every change is logged for the Audit Log."""

from datetime import time
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import redirect, render

from employees.models import ExchangeGroup, Position
from employees.positions import set_group

from .models import ExchangeRules, ExchangeRulesChange
from .permissions import get_acting_employee


def _hr_admin(request):
    acting = get_acting_employee(request.user)
    if acting is None or not acting.is_hr_administrator():
        raise PermissionDenied("This page is for the HR Administrator.")
    return acting


@login_required
def positions(request):
    _hr_admin(request)
    groups = list(ExchangeGroup.objects.all())
    if request.method == "POST":
        if request.POST.get("what") == "add_group":
            name = " ".join(request.POST.get("name", "").split())
            if not name:
                messages.error(request, "Type the name of the new group.")
            elif ExchangeGroup.objects.filter(name__iexact=name).exists():
                messages.error(request, f"There is already a group called {name}.")
            else:
                ExchangeGroup.objects.create(name=name)
                messages.success(request, f"Group {name} added. Give it to the positions it covers below.")
            return redirect("exchange:positions")
        by_pk = {g.pk: g for g in groups}
        changed = 0
        for pos in Position.objects.all():
            raw = request.POST.get(f"group_{pos.pk}")
            if raw is None:
                continue
            group = by_pk.get(int(raw)) if raw.isdigit() else None
            changed += set_group(pos, group, request.user)
        messages.success(request, f"{changed} position(s) changed. The change applies to exchange requests filed "
                                  "from now on." if changed else "Nothing was changed.")
        return redirect("exchange:positions")
    rows = Position.objects.select_related("exchange_group").annotate(n=Count("employees")).order_by("title")
    return render(request, "exchange/positions.html", {"rows": rows, "groups": groups})


def _parse(field, raw):
    if field == "pm_from":
        h, m = raw.split(":")[:2]
        return time(int(h), int(m))
    if field.startswith("consecutive"):
        value = int(raw)
        if not 1 <= value <= 31:
            raise ValueError
        return value
    value = Decimal(raw)
    if not Decimal("0") <= value <= Decimal("48"):
        raise ValueError
    return value


@login_required
def rules_settings(request):
    _hr_admin(request)
    rules = ExchangeRules.get()
    if request.method == "POST":
        changes = []
        for field in ExchangeRules.FIELDS:
            raw = (request.POST.get(field) or "").strip()
            try:
                new = _parse(field, raw)
            except (ValueError, InvalidOperation):
                messages.error(request, f"{ExchangeRules._meta.get_field(field).verbose_name}: not a valid value. "
                                        "Nothing was changed.")
                return redirect("exchange:rules")
            old = getattr(rules, field)
            if str(old) != str(new) and not (isinstance(old, Decimal) and Decimal(old) == new):
                changes.append((field, old, new))
        for field, old, new in changes:
            setattr(rules, field, new)
            ExchangeRulesChange.objects.create(field=field, old_value=str(old), new_value=str(new),
                                               changed_by=request.user)
        if changes:
            rules.save()
            messages.success(request, "Saved. The rules apply to exchange requests filed from now on.")
        else:
            messages.info(request, "Nothing was changed.")
        return redirect("exchange:rules")
    fields = [(f, ExchangeRules._meta.get_field(f).verbose_name, getattr(rules, f)) for f in ExchangeRules.FIELDS]
    return render(request, "exchange/rules.html", {"fields": fields,
                                                   "history": ExchangeRulesChange.objects.select_related(
                                                       "changed_by__employee").order_by("-changed_at")[:20]})
