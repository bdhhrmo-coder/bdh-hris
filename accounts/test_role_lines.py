"""
Owner decisions 2026-11-03 (multi-role staff, Option B):
  - adding a Supervisor line never removes another role (HR menu stays);
  - changing the role on an EXISTING line needs a ticked confirmation;
  - each employee's page lists all their role lines (and can add one);
  - the System Administration pages use the BDH HRIS look.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import RoleAssignment
from accounts.test_self_approval import NURSING, R, person, section, unit

HRMS = "Human Resources Management Section"


class RoleLineTests(TestCase):
    def setUp(self):
        self.sysad_user = User.objects.create_superuser("sysad9", password="pw-12345678")
        self.c = Client()
        self.c.force_login(self.sysad_user)
        self.owner = person("owner9", [HRMS], roles=[R.HR_ADMINISTRATOR])
        self.hr_line = RoleAssignment.objects.get(employee=self.owner, role=R.HR_ADMINISTRATOR)

    def menu_has_hr_admin(self):
        c = Client()
        c.force_login(self.owner.user)
        return "HR Administration" in c.get("/hris/home/").content.decode()

    def add_line(self, **extra):
        data = {"employee": self.owner.pk, "is_active": "on", "section": "", "unit": "",
                "start_date": "", "end_date": "", "delegated_by": ""}
        data.update(extra)
        return self.c.post(reverse("admin:accounts_roleassignment_add"), data)

    def test_adding_supervisor_lines_keeps_hr_administrator(self):
        self.assertTrue(self.menu_has_hr_admin())
        self.assertEqual(self.add_line(role=R.SUPERVISOR, section=section(HRMS).pk).status_code, 302)
        self.assertEqual(self.add_line(role=R.SUPERVISOR, unit=unit(NURSING, "ER").pk).status_code, 302)
        roles = sorted(self.owner.role_assignments.values_list("role", flat=True))
        self.assertEqual(roles, sorted([R.EMPLOYEE, R.HR_ADMINISTRATOR, R.SUPERVISOR, R.SUPERVISOR]))
        self.assertTrue(self.menu_has_hr_admin())

    def change_line(self, **extra):
        data = {"employee": self.owner.pk, "role": R.SUPERVISOR, "section": section(HRMS).pk, "unit": "",
                "is_active": "on", "start_date": "", "end_date": "", "delegated_by": ""}
        data.update(extra)
        return self.c.post(reverse("admin:accounts_roleassignment_change", args=[self.hr_line.pk]), data)

    def test_changing_the_role_on_a_line_needs_confirmation(self):
        r = self.change_line()
        self.assertEqual(r.status_code, 200)   # form shown again with the warning
        self.assertContains(r, "This replaces HR Administrator with Supervisor")
        self.hr_line.refresh_from_db()
        self.assertEqual(self.hr_line.role, R.HR_ADMINISTRATOR)
        r = self.change_line(confirm_role_change="on")
        self.assertEqual(r.status_code, 302)
        self.hr_line.refresh_from_db()
        self.assertEqual(self.hr_line.role, R.SUPERVISOR)

    def test_editing_dates_on_a_line_needs_no_confirmation(self):
        data = {"employee": self.owner.pk, "role": R.HR_ADMINISTRATOR, "section": "", "unit": "",
                "is_active": "on", "start_date": "2026-11-01", "end_date": "", "delegated_by": ""}
        r = self.c.post(reverse("admin:accounts_roleassignment_change", args=[self.hr_line.pk]), data)
        self.assertEqual(r.status_code, 302)

    def test_new_line_page_has_no_confirm_box(self):
        r = self.c.get(reverse("admin:accounts_roleassignment_add"))
        self.assertNotContains(r, "confirm_role_change")
        r = self.c.get(reverse("admin:accounts_roleassignment_change", args=[self.hr_line.pk]))
        self.assertContains(r, "confirm_role_change")

    def test_employee_page_lists_all_role_lines(self):
        RoleAssignment.objects.create(employee=self.owner, role=R.SUPERVISOR, section=section(HRMS))
        r = self.c.get(reverse("admin:employees_employee_change", args=[self.owner.pk]))
        self.assertContains(r, "one role per line")
        self.assertContains(r, 'name="role_assignments-TOTAL_FORMS" value="3"', html=False)

    def test_admin_pages_use_the_hris_look(self):
        r = self.c.get(reverse("admin:index"))
        self.assertContains(r, "bdh-logo")
        self.assertContains(r, "Back to the HRIS")
        self.assertContains(r, "--header-bg: #173d72")
        self.assertNotContains(r, "TEST SYSTEM")
        with override_settings(TEST_SYSTEM=True):
            r = self.c.get(reverse("admin:index"))
        self.assertContains(r, "TEST SYSTEM")
