"""Deductions (PMS step 5)."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from . import rules
from .models import DeductionCriterion
from .views import SETUP_TABS, _acting


@login_required
def criteria(request):
    acting = _acting(request)
    if not rules.is_hr(acting):
        raise PermissionDenied("Deduction Criteria are kept by HR.")
    return render(request, "performance/criteria.html", {"tabs": SETUP_TABS, "rows": DeductionCriterion.objects.all(),
                                                         "is_admin": rules.is_hr_admin(acting)})
