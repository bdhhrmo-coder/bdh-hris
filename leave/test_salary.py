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
