from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from employees.permissions import get_acting_employee

from .models import Notification


@login_required
def notification_list(request):
    """
    The employee's own notification inbox. Opening this page marks every
    currently-unread notification as read — a standard inbox behavior that
    needs no separate "mark as read" click for every row.
    """
    acting_employee = get_acting_employee(request.user)
    if acting_employee is None:
        raise PermissionDenied("No employee record is linked to your account.")

    notifications = list(acting_employee.notifications.all()[:100])
    unread_ids = [n.pk for n in notifications if not n.is_read]
    if unread_ids:
        Notification.objects.filter(pk__in=unread_ids).update(is_read=True)

    return render(
        request,
        "notifications/notification_list.html",
        {"notifications": notifications, "unread_ids": set(unread_ids)},
    )
