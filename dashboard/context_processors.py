from employees.permissions import get_acting_employee

from .permissions import can_view_dashboard as _can_view_dashboard


def dashboard_link(request):
    """Exposes `can_view_dashboard` to every template, so base.html can show
    (or hide) the Dashboard link without every view passing it in."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"can_view_dashboard": _can_view_dashboard(get_acting_employee(user))}
