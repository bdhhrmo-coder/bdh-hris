"""Home card "Performance tasks" (menu plan approved 2026-11-25): shown only
while something waits for the person. Never for ICTU Staff or the System
Administrator (owner decision 2026-11-26)."""

from accounts.models import RoleAssignment as R


def todo(acting):
    if acting is None or acting.has_role(R.ICTU_STAFF) or acting.has_role(R.SYSTEM_ADMINISTRATOR):
        return []
    from . import raters, results
    from .models import DevPlan

    items = []
    n = raters.tasks_for(acting).count()
    if n:
        items.append((f"{n} rating form(s) to fill in", "/performance/tasks/"))
    peers = raters.peers_to_finalize(acting)
    if peers:
        items.append((f"{len(peers)} employee(s) waiting for their peers to be picked", "/performance/tasks/"))
    forms = results.cosp_forms_waiting(acting)
    if forms:
        items.append((f"{len(forms)} COSP Performance Form(s)", "/performance/tasks/"))
    plans = DevPlan.objects.filter(period__employee=acting, status=DevPlan.DRAFT, sent_at__isnull=False)
    if plans.exists():
        items.append(("Development plan to answer", f"/performance/plans/{plans.first().period_id}/"))
    return items
