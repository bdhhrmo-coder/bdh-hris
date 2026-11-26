"""Pilot Item 3 (rebuilt 2026-11-26): the actual monthly salary on CSC Form No. 6.
Made-up figures only."""

from datetime import date
from decimal import Decimal

from accounts.models import RoleAssignment
from employees.forms import EmployeeForm
from orgstructure.models import Section

from .csc_form6 import fill_csc_form6, may_see_salary
from .models import LeaveApplication, LeaveType
from django.test import TestCase

from . import tests as lt
from .tests import make_employee

FAKE = Decimal("12345.67")


class SalaryTests(TestCase):
    _submit, _act = lt.RoutingTests._submit, lt.RoutingTests._act

    def setUp(self):
        lt.RoutingTests.setUp(self)
        self.employee.monthly_salary = FAKE
        self.employee.save()
        self.hrp = make_employee("hrpsal", "EMP-S1", date_hired=date(2020, 1, 1), role=RoleAssignment.HR_PROCESSOR)

    def test_only_the_hr_administrator_has_the_field(self):
        self.assertIn("monthly_salary", EmployeeForm(instance=self.employee, acting_employee=self.hr).fields)
        self.assertNotIn("monthly_salary", EmployeeForm(instance=self.employee, acting_employee=self.hrp).fields)

    def recorded(self):
        a = self._submit(self.employee, self.vl)
        self._act("supervisor1", a, "endorse")
        self._act("hrleave1", a, "record")
        self.assertEqual(a.status, LeaveApplication.PROCESSED_BY_HR)
        return a

    def test_salary_kept_at_recording_and_printed_for_the_right_people(self):
        a = self.recorded()
        self.assertEqual(a.salary_at_recording, FAKE)
        self.employee.monthly_salary = Decimal("99999.00")
        self.employee.save()
        a.refresh_from_db()
        self.assertEqual(fill_csc_form6(a, show_salary=True)["blank"]["AA12"].value, "12,345.67")
        self.assertIn(fill_csc_form6(a, show_salary=False)["blank"]["AA12"].value, ("", None))
        for who in (self.employee, self.hr, self.hrp, self.ao, self.coh):
            self.assertTrue(may_see_salary(who, a), who)
        self.assertFalse(may_see_salary(self.supervisor, a))

    def test_no_salary_on_record_prints_blank(self):
        self.employee.monthly_salary = None
        self.employee.save()
        a = self.recorded()
        self.assertIsNone(a.salary_at_recording)
        self.assertIn(fill_csc_form6(a, show_salary=True)["blank"]["AA12"].value, ("", None))


class SalaryPrivacyOnScreenTests(TestCase):
    """F1 (audit 2026-11-26): hidden from System Administration; the employee sees their own."""

    def setUp(self):
        lt.RoutingTests.setUp(self)
        self.employee.monthly_salary = FAKE
        self.employee.save()

    def test_system_administration_pages_never_show_the_salary(self):
        from django.contrib import admin
        from employees.models import Employee

        emp_admin = admin.site._registry[Employee]
        leave_admin = admin.site._registry[LeaveApplication]
        rf = __import__("django.test", fromlist=["RequestFactory"]).RequestFactory().get("/")
        rf.user = self.employee.user
        self.assertNotIn("monthly_salary", emp_admin.get_form(rf, self.employee).base_fields)
        self.assertNotIn("salary_at_recording", leave_admin.get_form(rf).base_fields)

    def test_employee_sees_own_salary_on_my_profile(self):
        self.client.force_login(self.employee.user)
        page = self.client.get("/employees/me/edit-request/").content.decode()
        self.assertIn("Monthly salary on record", page)
        self.assertIn("12,345.67", page)

    def test_no_salary_says_not_on_record(self):
        self.employee.monthly_salary = None
        self.employee.save()
        self.client.force_login(self.employee.user)
        page = self.client.get("/employees/me/edit-request/").content.decode()
        self.assertIn("Not on record yet.", page)
