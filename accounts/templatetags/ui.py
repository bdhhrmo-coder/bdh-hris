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
