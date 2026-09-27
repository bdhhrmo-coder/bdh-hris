from employees.permissions import get_acting_employee

from .permissions import can_view_audit_log as _can_view_audit_log


def audit_log_link(request):
    """Exposes `can_view_audit_log` to every template, so base.html can show
    (or hide) the Audit Log link without every view passing it in."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"can_view_audit_log": _can_view_audit_log(get_acting_employee(user))}
