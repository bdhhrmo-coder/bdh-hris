from django import template

from outonpass.rules import hm as _hm

register = template.Library()


@register.filter
def hm(minutes):
    """Minutes as '1 h 05 min'."""
    return _hm(int(minutes or 0))
