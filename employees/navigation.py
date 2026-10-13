"""
The sidebar menu, in ONE place (Batch 6, owner decisions 2026-10-13).

base.html only draws what `sidebar_menu()` returns. To add, move or rename a
menu item, edit MENU below. Each item names the existing permission flag that
decides whether the person SEES it; every page still checks permission on
the server - hiding a link is never the security.

Groups (in this order; a group is shown only if the person has an item in it):
  Home | My Requests | Approvals | Workforce | Reports | HR Administration

Notifications are reached from the bell in the header (no duplicate menu
item). "File for a Group" stays under My Requests (owner decision, Batch 2).
"""

from django.urls import reverse

from accounts.models import RoleAssignment
from auditlog.permissions import can_view_audit_log
from dashboard.permissions import can_view_dashboard

from accounts.oic import can_designate_oic

from .context_processors import nav_links
from .permissions import can_review_profile_requests, get_acting_employee

# (group key, group label, group icon, [ (label, url name, icon, flag) ])
# flag: "all", or a key of the flags dict built in _flags().
MENU = [
    ("home", "Home", "house", [
        ("Home", "homepage:home", "house", "all"),
        ("Announcements", "announcements:list", "megaphone", "all"),
    ]),
    ("mine", "My Requests", "folder-open", [
        ("Leave", "leave:my_applications", "file-text", "all"),
        ("CTO", "cto:my_cto", "clock-3", "all"),
        ("Exchange of Duty", "exchange:my_exchanges", "repeat", "all"),
        ("Attendance", "attendance:my_attendance", "fingerprint", "all"),
        ("Official Business / Time / Travel / OT", "official_requests:my_requests", "briefcase", "all"),
        ("File for a Group", "official_requests:batch_file", "users-round", "can_file_batch"),
        ("My Schedule", "schedules:mine", "calendar-days", "all"),
        ("My Profile", "employees:my_profile_edit_request", "user-round", "has_employee"),
    ]),
    ("approvals", "Approvals", "inbox", [
        ("Leave", "leave:leave_queue", "file-text", "approver"),
        ("CTO", "cto:cto_queue", "clock-3", "approver"),
        ("Exchange of Duty", "exchange:exchange_queue", "repeat", "approver"),
        ("Attendance Correction", "attendance:correction_queue", "fingerprint", "correction_validator"),
        ("Official Business / Time / Travel / OT", "official_requests:request_queue", "briefcase", "approver"),
        ("Profile Changes", "employees:profile_edit_request_queue", "clipboard-check", "can_review_profile_requests"),
        ("Officer-in-Charge (OIC)", "oic_home", "calendar-clock", "can_designate_oic"),
    ]),
    ("workforce", "Workforce", "users", [
        ("Employees", "employees:employee_list", "user-round", "can_list_employees"),
        ("Duty Schedules", "schedules:list", "calendar-range", "can_view_schedules"),
    ]),
    ("reports", "Reports", "chart-column", [
        ("Dashboard", "dashboard:dashboard_home", "chart-column", "can_view_dashboard"),
        ("Audit Log", "auditlog:audit_log", "shield-check", "can_view_audit_log"),
    ]),
    ("admin", "HR Administration", "settings", [
        ("Data Import", "dataimport:home", "upload", "can_import_data"),
        ("Import Biometric Attendance", "attendance:biometric_import", "fingerprint", "is_hr"),
        ("Minor Attendance Correction", "attendance:minor_correction_create", "clipboard-check", "is_hr"),
        ("Manage Announcements", "announcements:manage", "megaphone", "can_manage_announcements"),
        ("Shift Codes", "schedules:shift_codes", "list", "can_manage_announcements"),
        ("User Accounts and Roles", "admin:index", "user-cog", "is_system_admin"),
    ]),
]

# Queue url name -> label used by homepage.kpis.waiting_for_me (for the counts).
QUEUE_COUNT_URLS = {"leave:leave_queue", "cto:cto_queue", "exchange:exchange_queue",
                    "attendance:correction_queue", "official_requests:request_queue"}


def _flags(request, acting):
    f = dict(nav_links(request))
    f["can_view_dashboard"] = can_view_dashboard(acting)
    f["can_view_audit_log"] = can_view_audit_log(acting)
    f["approver"] = f.get("has_approval_role", False)
    f["correction_validator"] = f.get("has_approval_role", False) or f.get("is_ictu_staff", False)
    f["is_system_admin"] = acting is not None and acting.has_role(RoleAssignment.SYSTEM_ADMINISTRATOR)
    # Batch 7: pages that had no menu link before.
    f["has_employee"] = acting is not None
    f["is_hr"] = acting is not None and (acting.has_role(RoleAssignment.HR_PROCESSOR)
                                         or acting.has_role(RoleAssignment.HR_ADMINISTRATOR))
    f["can_review_profile_requests"] = can_review_profile_requests(acting)
    f["can_designate_oic"] = can_designate_oic(acting)
    f["all"] = True
    return f


def _queue_counts(acting, user):
    from homepage.kpis import waiting_for_me

    return {url: count for _, count, url in waiting_for_me(acting, user)}


def sidebar_menu(request):
    """Context processor: `nav_groups` for base.html."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    acting = get_acting_employee(user)
    flags = _flags(request, acting)
    counts = _queue_counts(acting, user) if (flags["approver"] or flags["correction_validator"]) else {}
    path = request.path

    groups, best = [], (None, -1)
    for key, label, icon, entries in MENU:
        items = []
        for item_label, url_name, item_icon, flag in entries:
            if not flags.get(flag):
                continue
            url = reverse(url_name)
            item = {"label": item_label, "url": url, "icon": item_icon, "count": counts.get(url_name, 0),
                    "current": False}
            items.append(item)
            if url != "/" and path.startswith(url) and len(url) > best[1]:
                best = (item, len(url))
        if items:
            groups.append({"key": key, "label": label, "icon": icon, "items": items, "open": False,
                           "count": sum(i["count"] for i in items)})
    if best[0] is None:  # e.g. /leave/apply/ -> the Leave item of the same module
        seg = "/" + path.strip("/").split("/")[0] + "/"
        for g in groups:
            for item in g["items"]:
                if seg != "/" and item["url"].startswith(seg):
                    best = (item, 0)
                    break
            if best[0] is not None:
                break
    if best[0] is not None:
        best[0]["current"] = True
        for g in groups:
            if best[0] in g["items"]:
                g["open"] = True
    if groups and not any(g["open"] for g in groups):
        groups[0]["open"] = True
    return {"nav_groups": groups}
