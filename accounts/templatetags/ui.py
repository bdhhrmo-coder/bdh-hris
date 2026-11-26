"""Small presentation helpers shared by all templates (Batch 6 design system).

{% load ui %}
{% icon "house" %}            -> a line icon from templates/components/icon_sprite.html
{% icon "bell" "icon-lg" %}   -> with an extra CSS class
Icons are always decorative (aria-hidden): every icon sits next to a text label.
"""

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def icon(name, extra_class=""):
    return format_html('<svg class="icon {}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>',
                       extra_class, name)


# -- Filing date and time (pilot Item 2, owner request 2026-11-24; rebuilt 2026-11-26) -----------
#
# {% filed obj %}          -> "Nov 26, 2026 2:31 PM" (+ "Resubmitted Nov 27, 2026 9:10 AM" on a new line)
# {% filed obj short=1 %}  -> the same on one line, for tables and queues
# The first filing time never changes; a resubmission after a return is shown beside it.
# Times are Philippine time (settings.TIME_ZONE = Asia/Manila).

def filing_times(obj):
    """(first filing, latest resubmission or None) for any request or schedule."""
    first = getattr(obj, "submitted_at", None) or getattr(obj, "filed_at", None)
    actions = getattr(obj, "actions", None)
    resubmitted = None
    if actions is not None and hasattr(actions, "filter"):
        if first is None:  # duty schedules: the first "submit" in the log
            a = actions.filter(action="submit").order_by("acted_at").first()
            first = a.acted_at if a else None
        r = actions.filter(action="resubmit").order_by("-acted_at").first()
        resubmitted = r.acted_at if r else None
    return first, resubmitted


@register.simple_tag
def filed(obj, short=False):
    from django.utils import timezone

    first, again = filing_times(obj)
    if first is None:
        return format_html('<span class="muted">—</span>')
    fmt = "%b %d, %Y %I:%M %p"
    when = timezone.localtime(first).strftime(fmt).replace(" 0", " ")
    if again is None:
        return format_html('<span class="filed-at">{}</span>', when)
    again_s = timezone.localtime(again).strftime(fmt).replace(" 0", " ")
    if short:
        return format_html('<span class="filed-at">{} <small class="muted">· resubmitted {}</small></span>', when, again_s)
    return format_html('<span class="filed-at">{}<br><small class="muted">Resubmitted {}</small></span>', when, again_s)


@register.simple_tag
def schedule_submitted(schedule):
    """Item 2 / audit fix F5: "Submitted: <date, time>" for a duty schedule, or "Not yet submitted"."""
    first, _ = filing_times(schedule)
    if first is None:
        return format_html('<span class="muted">Not yet submitted</span>')
    return format_html("Submitted: {}", filed(schedule, short=True))
