from django import template

from employees.permissions import get_acting_employee
from leave.permissions import may_print_csc6, print_wait_reason

register = template.Library()


@register.simple_tag(takes_context=True)
def print_wait(context, application):
    """'' if the signed-in person may print this leave form now, else the
    short reason to show in place of the Print link (pilot issue 2)."""
    request = context.get("request")
    if request is None:
        return ""
    return print_wait_reason(get_acting_employee(request.user), application) or ""


@register.simple_tag(takes_context=True)
def csc6_hidden(context, application):
    """Part B: True when the signed-in person may not print this application's
    CSC Form No. 6 at all (someone else's form, and not HR). The button is
    then left out entirely; the filer's own form keeps its waiting message."""
    request = context.get("request")
    if request is None or application.is_cosp_route:
        return False
    acting = get_acting_employee(request.user)
    if acting is not None and acting.pk == application.employee_id:
        return False
    return not may_print_csc6(acting, application)
