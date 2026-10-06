"""Tests for the workforce indicators (dashboard/workforce.py)."""

from datetime import date, time
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from attendance.models import AttendanceRecord
from employees.models import EducationHistory, Employee
from leave.models import LeaveApplication, LeaveType
from official_requests.models import OfficialRequest
from orgstructure.models import Section

from . import workforce

START, END = date(2026, 9, 1), date(2026, 9, 30)


def emp(eid, section=None, **extra):
    defaults = dict(surname=eid, first_name="T", date_hired=date(2020, 1, 1), employment_status="REGULAR")
    defaults.update(extra)
    e = Employee.objects.create(employee_id=eid, **defaults)
    if section:
        e.sections.add(section)
    return e


class WorkforceTests(TestCase):
    def setUp(self):
        self.hr_section = Section.objects.get(name="Human Resources Management Section")
        self.lab = Section.objects.get(name="Laboratory Section")
        self.recorder = User.objects.create_user("wf_recorder", password="x")

    def test_headcount_tiles(self):
        emp("A", appointment_type="Permanent")
        emp("B", appointment_type="Temporary")
        emp("C", employment_status="COSP", cosp_under="PGP")
        emp("D", employment_status="COSP", cosp_under="LGU")
        emp("E", employment_status="COSP")
        emp("F", is_active=False, separation_date=date(2026, 9, 15))
        emp("G", is_active=False, separation_date=date(2025, 1, 1))
        c = workforce.workforce_counts(START, END)
        self.assertEqual(c, {"active": 5, "separated": 1, "permanent": 1, "cosp": 3, "cosp_pgp": 1, "cosp_lgu": 1,
                             "cosp_unassigned": 1})

    def record(self, e, day, absent=False):
        AttendanceRecord.objects.create(employee=e, date=day, is_absent=absent,
                                        time_in=None if absent else time(8), time_out=None if absent else time(17),
                                        recorded_by=self.recorder)

    def test_absenteeism_counts_only_unplanned_absences(self):
        a = emp("ABS", self.hr_section)
        for d in range(1, 21):  # 20 scheduled days
            self.record(a, date(2026, 9, d), absent=d in (3, 4, 10))
        vl = LeaveType.objects.get(code="VL")
        LeaveApplication.objects.create(employee=a, leave_type=vl, start_date=date(2026, 9, 4),
                                        end_date=date(2026, 9, 4), number_of_days=1, status="RECORDED")
        OfficialRequest.objects.create(employee=a, request_type=OfficialRequest.OFFICIAL_BUSINESS, purpose="x",
                                       start_date=date(2026, 9, 10), end_date=date(2026, 9, 10), status="APPROVED")
        r = workforce.absenteeism(START, END)
        self.assertEqual((r["unplanned"], r["scheduled"], r["rate"]), (1, 20, Decimal("5.0")))
        self.assertFalse(r["over_target"])  # exactly 5% meets the target

    def test_absenteeism_over_target_and_no_data(self):
        a = emp("ABS2", self.lab)
        for d in range(1, 11):
            self.record(a, date(2026, 9, d), absent=d == 1)
        self.assertTrue(workforce.absenteeism(START, END)["over_target"])  # 10%
        self.assertIsNone(workforce.absenteeism(START, END, section=self.hr_section)["rate"])

    def test_leave_utilization_used_over_earned(self):
        a = emp("UTIL", self.hr_section)  # regular: VL 15 + SL 15 + Wellness 5 = 35 days a year
        vl = LeaveType.objects.get(code="VL")
        LeaveApplication.objects.create(employee=a, leave_type=vl, start_date=date(2026, 9, 7),
                                        end_date=date(2026, 9, 8), number_of_days=2, status="RECORDED")
        r = workforce.leave_utilization(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(r["used"], Decimal("2.00"))
        self.assertEqual(r["earned"], Decimal("35.00"))
        self.assertEqual(r["rate"], Decimal("5.7"))

    def test_sex_age_and_education(self):
        a = emp("SEX1", self.hr_section, sex_at_birth="F", date_of_birth=date(1990, 10, 7))
        emp("SEX2", self.lab, sex_at_birth="M", date_of_birth=date(1966, 1, 1))
        emp("SEX3", self.lab)
        self.assertEqual([r["count"] for r in workforce.sex_distribution()], [1, 1, 1])
        ages = {r["label"]: r["count"] for r in workforce.age_distribution(today=date(2026, 10, 6))}
        self.assertEqual((ages["35-44"], ages["60-64"], ages["Not recorded"]), (1, 1, 1))  # a is 35, b is 60
        EducationHistory.objects.create(employee=a, education_level="COLLEGE", school="X")
        EducationHistory.objects.create(employee=a, education_level="MASTERS", school="Y")
        rows, total = workforce.education_by_section()
        hr_row = next(r for r in rows if r["section"] == self.hr_section)
        counts = dict(zip(workforce.EDUCATION_COLUMNS, hr_row["counts"]))
        self.assertEqual(counts["Graduate Studies - Master's"], 1)
        self.assertEqual(counts["College"], 0)  # only the highest level counts
        self.assertEqual(dict(zip(workforce.EDUCATION_COLUMNS, total["counts"]))["No record"], 2)

    def test_dashboard_page_shows_new_indicators(self):
        hr = emp("DASH-HR", self.hr_section)
        hr.user = User.objects.create_user("wf_hr", password="x")
        hr.save()
        RoleAssignment.objects.create(employee=hr, role=RoleAssignment.HR_ADMINISTRATOR)
        self.client.force_login(hr.user)
        page = self.client.get(reverse("dashboard:dashboard_home")).content.decode()
        for text in ("Active employees", "Separated", "Permanent (active)", "COSP under PGP", "COSP under LGU",
                     "Absenteeism rate", "Leave utilization rate", "Sex distribution", "Age distribution",
                     "Highest educational attainment"):
            self.assertIn(text, page)
