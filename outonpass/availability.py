"""When the Out-on-Pass module is in use (owner decision 2026-11-24, Option B).

The module is on main before the policy takes effect, so until the start date
(settings.OUT_ON_PASS_START_DATE, default Jan 11, 2027: the day the HRIS takes
over from the Google Sheets tracker) its menu items are hidden and its pages
show "not yet in use". Only HR Administration -> Out-on-Pass Setup stays open,
so HR can enter COSP contract periods ahead of time. No data is changed.
A blank start date means the module is in use.
"""

from functools import wraps

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone


def start_date():
    return getattr(settings, "OUT_ON_PASS_START_DATE", None)


def in_use(today=None):
    start = start_date()
    return start is None or (today or timezone.localdate()) >= start


def in_use_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        # Signed-out users fall through to the view, which sends them to login.
        if not in_use() and request.user.is_authenticated:
            return render(request, "outonpass/not_in_use.html", {"start": start_date()})
        return view(request, *args, **kwargs)
    return wrapper
