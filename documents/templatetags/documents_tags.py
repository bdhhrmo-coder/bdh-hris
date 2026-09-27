from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag
def documents_url(obj):
    """{% documents_url some_object %} — the generic document-list URL for
    any transaction model, so leave/cto/exchange/attendance/
    official_requests templates can link to it without hardcoding a
    content_type id."""
    return reverse(
        "documents:document_list",
        kwargs={"app_label": obj._meta.app_label, "model_name": obj._meta.model_name, "object_id": obj.pk},
    )
