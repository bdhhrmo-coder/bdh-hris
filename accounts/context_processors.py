"""Pilot Item 5: a banner on every page while the person is an OIC."""


def oic_banner(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    from .models import RoleAssignment as R

    lines = list(R.objects.filter(R.in_force_q(), is_oic=True, employee__user=user)
                 .select_related("delegated_by", "section", "unit").order_by("end_date"))
    if not lines:
        return {}
    labels = {R.ADMINISTRATIVE_OFFICER: "the AO", R.CHIEF_OF_HOSPITAL: "the COH"}
    items = []
    for ra in lines:
        who = labels.get(ra.role) or f"{ra.unit or ra.section}"
        until = f" until {ra.end_date:%b %d, %Y}" if ra.end_date else ""
        by = f" ({ra.delegated_by.full_name})" if ra.delegated_by_id else ""
        items.append(f"OIC for {who}{by}{until}")
    waits = any(ra.role in labels for ra in lines)
    return {"oic_banner": items, "oic_banner_waits": waits}
