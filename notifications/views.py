from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from employees.permissions import get_acting_employee

from .models import Notification


def _own_employee(request):
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")
    return acting_employee


def badge_label(count):
    """What the red bubble shows: '' (hidden), '1'..'9', or '9+'."""
    if not count:
        return ""
    return "9+" if count > 9 else str(count)


@login_required
def notification_list(request):
    """
    The employee's own notification inbox (only their own rows).

    Batch 2, Item 4: opening this page no longer marks everything as read.
    A notification becomes read when it is opened (open_notification), or
    when the person clicks "Mark all as read". Unread rows stay highlighted
    until then, and the red bubble in the header counts them.
    """
    acting_employee = _own_employee(request)
    notifications = list(acting_employee.notifications.all()[:100])
    unread_ids = {n.pk for n in notifications if not n.is_read}
    return render(
        request,
        "notifications/notification_list.html",
        {"notifications": notifications, "unread_ids": unread_ids},
    )


@login_required
def open_notification(request, pk):
    """Mark one of MY notifications read, then go to the page it points to."""
    notification = get_object_or_404(Notification, pk=pk, recipient=_own_employee(request))
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])
    target = notification.url
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect("notifications:notification_list")


@login_required
@require_POST
def mark_all_read(request):
    _own_employee(request).notifications.filter(is_read=False).update(is_read=True)
    return redirect("notifications:notification_list")


@never_cache
@login_required
def unread_count(request):
    """Polled by the header bell every 45 seconds (templates/base.html)."""
    acting_employee = get_acting_employee(request.user)
    count = acting_employee.notifications.filter(is_read=False).count() if acting_employee else 0
    return JsonResponse({"count": count, "label": badge_label(count)})
