"""Batch 6: grouped sidebar menu built from employees/navigation.py."""

from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment as R
from employees.models import Employee
from orgstructure.models import Section


def person(username, role=None, status="REGULAR", section=None):
    user = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(), first_name="T",
                                date_hired=date(2024, 1, 1), employment_status=status)
    if role:
        R.objects.create(employee=e, role=role, section=section)
    return e


class MenuTests(TestCase):
    def menu(self, employee, path="/hris/home/"):
        self.client.force_login(employee.user)
        resp = self.client.get(path)
        groups = resp.context["nav_groups"]
        return {g["label"]: [i["label"] for i in g["items"]] for g in groups}, groups

    def test_employee_sees_only_home_and_my_requests(self):
        menu, _ = self.menu(person("navemp"))
        self.assertEqual(list(menu), ["Home", "My Requests"])
        self.assertNotIn("File for a Group", menu["My Requests"])
        self.assertIn("My Schedule", menu["My Requests"])

    def test_no_duplicate_links_and_notifications_only_on_bell(self):
        menu, groups = self.menu(person("navhr", R.HR_ADMINISTRATOR))
        urls = [i["url"] for g in groups for i in g["items"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertNotIn(reverse("notifications:notification_list"), urls)
        self.assertEqual(list(menu), ["Home", "My Requests", "Approvals", "Workforce", "Reports", "HR Administration"])

    def test_ictu_sees_only_the_correction_queue(self):
        menu, _ = self.menu(person("navictu", R.ICTU_STAFF))
        self.assertEqual(menu["Approvals"], ["Attendance Correction"])
        self.assertNotIn("Workforce", menu)

    def test_system_admin_gets_accounts_link_but_no_approvals(self):
        menu, _ = self.menu(person("navsys", R.SYSTEM_ADMINISTRATOR))
        self.assertIn("User Accounts and Roles", menu["HR Administration"])
        self.assertNotIn("Approvals", menu)

    def test_supervisor_has_group_filing_and_schedules(self):
        lab = Section.objects.get(name="Laboratory Section")
        menu, _ = self.menu(person("navsup", R.SUPERVISOR, section=lab))
        self.assertIn("File for a Group", menu["My Requests"])
        self.assertEqual(menu["Workforce"], ["Duty Schedules"])
        self.assertNotIn("HR Administration", menu)

    def test_current_page_marked_and_its_group_open(self):
        _, groups = self.menu(person("navcur"), path=reverse("leave:leave_apply"))
        mine = next(g for g in groups if g["key"] == "mine")
        self.assertTrue(mine["open"])
        self.assertEqual([i["label"] for i in mine["items"] if i["current"]], ["Leave"])

    def test_every_menu_link_opens_for_that_role(self):
        lab = Section.objects.get(name="Laboratory Section")
        for role in (None, R.SUPERVISOR, R.HR_PROCESSOR, R.HR_ADMINISTRATOR, R.ADMINISTRATIVE_OFFICER,
                     R.CHIEF_OF_HOSPITAL, R.ICTU_STAFF, R.SYSTEM_ADMINISTRATOR):
            e = person(f"navall{role or 'emp'}".lower()[:30], role, section=lab if role == R.SUPERVISOR else None)
            _, groups = self.menu(e)
            for g in groups:
                for item in g["items"]:
                    if item["url"].startswith("/admin/"):
                        continue  # Django admin uses its own staff login
                    code = self.client.get(item["url"]).status_code
                    self.assertIn(code, (200, 302), f"{role} {item['label']} -> {code}")
                    if code == 302:
                        self.assertNotIn("login", self.client.get(item["url"])["Location"])


class DirectUrlStillDeniedTests(TestCase):
    """Hiding a menu item is not the security: the pages still refuse."""

    def test_employee_direct_urls_refused(self):
        e = person("navdirect")
        self.client.force_login(e.user)
        for name in ("dashboard:dashboard_home", "auditlog:audit_log", "dataimport:home", "announcements:manage",
                     "schedules:shift_codes", "employees:employee_list", "schedules:new"):
            self.assertIn(self.client.get(reverse(name)).status_code, (302, 403, 404), name)
