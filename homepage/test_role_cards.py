"""Batch 6 Step 3: Home cards per role (max 5, actions first) and the
Dashboard's top row + folding sections."""

import re
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


def card_labels(html):
    section = html.split('aria-label="Key figures"')[1].split("</section>")[0]
    return re.findall(r'<p class="kpi-label">([^<]+)</p>', section)


class HomeCardsTests(TestCase):
    def labels(self, employee):
        self.client.force_login(employee.user)
        return card_labels(self.client.get(reverse("homepage:home")).content.decode())

    def test_employee(self):
        self.assertEqual(self.labels(person("hcemp")),
                         ["My pending requests", "Absent today", "My balances", "My schedule this week"])

    def test_cosp_balances_show_cosp_leave_not_vacation(self):
        e = person("hccosp", status="COSP")
        self.client.force_login(e.user)
        html = self.client.get(reverse("homepage:home")).content.decode()
        self.assertIn("COSP Leave", html)
        self.assertNotIn("Vacation Leave", html)

    def test_supervisor(self):
        lab = Section.objects.get(name="Laboratory Section")
        self.assertEqual(self.labels(person("hcsup", R.SUPERVISOR, section=lab)),
                         ["Waiting for my action", "Absent today", "Pending in my sections"])

    def test_hr_ao_coh(self):
        for role in (R.HR_ADMINISTRATOR, R.ADMINISTRATIVE_OFFICER, R.CHIEF_OF_HOSPITAL):
            self.assertEqual(self.labels(person(f"hc{role[:8].lower()}", role)),
                             ["Waiting for my action", "Absent today", "Pending applications", "Headcount"])

    def test_ictu_and_system_admin_max_five(self):
        ictu = self.labels(person("hcictu", R.ICTU_STAFF))
        self.assertEqual(ictu[0], "Waiting for my action")
        sysadmin = self.labels(person("hcsys", R.SYSTEM_ADMINISTRATOR))
        self.assertEqual(sysadmin[0], "User accounts and roles")
        self.assertNotIn("Waiting for my action", sysadmin)
        self.assertLessEqual(max(len(ictu), len(sysadmin)), 5)


class DashboardLayoutTests(TestCase):
    def test_five_top_tiles_and_every_tile_kept(self):
        hr = person("dlhr", R.HR_ADMINISTRATOR)
        self.client.force_login(hr.user)
        html = self.client.get(reverse("dashboard:dashboard_home")).content.decode()
        top = html.split('class="kpi-grid dash-top"')[1].split("</div>\n</div>")[0]
        self.assertEqual(top.count('class="kpi-tile'), 5)
        self.assertEqual(html.count('class="kpi-tile'), 13)  # nothing removed or duplicated
        self.assertEqual(html.count('<details class="dash-section"'), 4)
