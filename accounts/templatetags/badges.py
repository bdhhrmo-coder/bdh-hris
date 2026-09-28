"""
{% status_badge %} — renders any request/application status as a
color-coded badge (visual design pass, 2026-09-28).

Buckets exactly match dashboard/stats.py's `_bucket()` — pending / approved
/ not-approved — so a status reads the same color on a queue screen as it
does rolled up on the Dashboard. Kept here (not per-app) because every
routed request type (leave, CTO, exchange, attendance correction, official
requests) uses the same SUBMITTED/ENDORSED_BY_.../APPROVED/REJECTED/... /
RECORDED naming convention, so one shared mapping covers all of them
instead of five near-identical ones.
"""

from django import template
from django.utils.html import format_html

register = template.Library()

_APPROVED = {"APPROVED", "RECORDED"}
_NOT_APPROVED = {"REJECTED", "RETURNED", "CANCELLED", "CONSENT_DECLINED", "FORFEITED", "DECLINED"}


@register.simple_tag
def status_badge(status_code, display_text):
    code = (status_code or "").upper()
    if code in _APPROVED:
        css_class = "badge-approved"
    elif code in _NOT_APPROVED:
        css_class = "badge-not-approved"
    else:
        css_class = "badge-pending"
    return format_html('<span class="badge {}">{}</span>', css_class, display_text)
