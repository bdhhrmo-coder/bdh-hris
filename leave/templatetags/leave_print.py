from django import template

from employees.permissions import get_acting_employee
from leave.permissions import print_wait_reason

register = template.Library()


@register.simple_tag(takes_context=True)
def print_wait(context, application):
    """'' if the signed-in person may print this leave form now, else the
    short reason to show in place of the Print link (pilot issue 2)."""
    request = context.get("request")
    if request is None:
        return ""
    return print_wait_reason(get_acting_employee(request.user), application) or ""
