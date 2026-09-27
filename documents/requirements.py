"""
Maps a transaction instance to the "sub-type value" DocumentRequirement
rows can be scoped to (see models.py's DocumentRequirement docstring), and
computes which configured requirements are met/missing for it.

Adding document requirements for a new transaction model later only
needs one line here, not a change to that app.
"""

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

SUBTYPE_EXTRACTORS = {
    "cto.ctocreditentry": lambda obj: obj.duty_type,
    "leave.leaveapplication": lambda obj: obj.leave_type.code,
    "official_requests.officialrequest": lambda obj: obj.request_type,
    "attendance.attendancecorrectionrequest": lambda obj: obj.correction_type,
    # cto.CTOUsageApplication and exchange.DutyExchangeRequest have no
    # sub-type concept — any requirement for them must use a blank
    # sub_type_value (applies to every instance).
}


def get_subtype_value(obj):
    key = f"{obj._meta.app_label}.{obj._meta.model_name}"
    extractor = SUBTYPE_EXTRACTORS.get(key)
    if extractor is None:
        return None
    try:
        return extractor(obj)
    except Exception:  # noqa: BLE001 — e.g. a FK not yet set on an unsaved instance
        return None


def required_documents_for(obj):
    """DocumentRequirement rows that apply to this specific instance —
    either scoped to every instance of its model, or matching its own
    sub-type value."""
    from .models import DocumentRequirement

    content_type = ContentType.objects.get_for_model(obj)
    subtype = get_subtype_value(obj) or ""
    return DocumentRequirement.objects.filter(content_type=content_type).filter(
        Q(sub_type_value="") | Q(sub_type_value=subtype)
    )


def requirement_status_for(obj):
    """
    Returns a list of dicts describing each requirement (or alternative
    group) that applies to this transaction and whether it's currently
    satisfied by an active uploaded document:
        [{"label": "...", "is_mandatory": bool, "satisfied": bool}, ...]
    Alternative-group members are collapsed into one row (satisfied if
    ANY member of the group has an active upload).
    """
    from .models import UploadedDocument

    content_type = ContentType.objects.get_for_model(obj)
    requirements = list(required_documents_for(obj))
    uploaded_requirement_ids = set(
        UploadedDocument.objects.filter(
            content_type=content_type, object_id=obj.pk, is_active=True, requirement__isnull=False
        ).values_list("requirement_id", flat=True)
    )

    rows = []
    seen_groups = set()
    for req in requirements:
        if req.alternative_group:
            if req.alternative_group in seen_groups:
                continue
            seen_groups.add(req.alternative_group)
            group_members = [r for r in requirements if r.alternative_group == req.alternative_group]
            label = " OR ".join(r.label for r in group_members)
            satisfied = any(r.pk in uploaded_requirement_ids for r in group_members)
            is_mandatory = any(r.is_mandatory for r in group_members)
        else:
            label = req.label
            satisfied = req.pk in uploaded_requirement_ids
            is_mandatory = req.is_mandatory
        rows.append({"label": label, "is_mandatory": is_mandatory, "satisfied": satisfied})
    return rows
