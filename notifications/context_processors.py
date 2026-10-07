from employees.permissions import get_acting_employee


def unread_notification_count(request):
    """
    Exposes `unread_notification_count` to every template, so base.html can
    show a small badge without every view having to pass it in explicitly.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    acting_employee = get_acting_employee(user)
    if acting_employee is None:
        return {}
    from .views import badge_label

    count = acting_employee.notifications.filter(is_read=False).count()
    return {"unread_notification_count": count, "unread_notification_label": badge_label(count)}
