from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment
from cto.models import CTOUsageApplication
from employees.models import Employee
from leave.models import LeaveApplication, LeaveType
from orgstructure.models import Section

from . import stats
from .period import resolve_period
from .permissions import can_view_dashboard


def make_employee(username, employee_id, role=None, sections=None, employment_status="REGULAR"):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test",
        date_hired=date(2024, 1, 1), employment_status=employment_status,
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    if sections:
        employee.sections.set(sections)
    return employee


def make_leave_application(employee, status, submitted_on, leave_type=None):
    leave_type = leave_type or LeaveType.objects.create(
        name="Vacation Leave", code=f"VL-{employee.pk}", applicable_to=LeaveType.APPLICABLE_BOTH,
        balance_tracking=LeaveType.TRACKING_UNTRACKED, requires_full_routing=True,
    )
    application = LeaveApplication.objects.create(
        employee=employee, leave_type=leave_type, start_date=submitted_on, end_date=submitted_on,
        number_of_days=1, status=status,
    )
    application.submitted_at = timezone.make_aware(timezone.datetime.combine(submitted_on, timezone.datetime.min.time()))
    application.save(update_fields=["submitted_at"])
    return application


class PeriodResolutionTests(TestCase):
    def test_defaults_to_current_month(self):
        today = date.today()
        start, end, label = resolve_period({})
        self.assertEqual(start, date(today.year, today.month, 1))
        self.assertEqual(end.month, today.month)
        self.assertIn(str(today.year), label)

    def test_explicit_month(self):
        start, end, label = resolve_period({"period": "month", "year": "2026", "month": "2"})
        self.assertEqual(start, date(2026, 2, 1))
        self.assertEqual(end, date(2026, 2, 28))
        self.assertEqual(label, "February 2026")

    def test_quarter(self):
        start, end, label = resolve_period({"period": "quarter", "year": "2026", "quarter": "3"})
        self.assertEqual(start, date(2026, 7, 1))
        self.assertEqual(end, date(2026, 9, 30))
        self.assertEqual(label, "Q3 2026")

    def test_year(self):
        start, end, label = resolve_period({"period": "year", "year": "2026"})
        self.assertEqual(start, date(2026, 1, 1))
        self.assertEqual(end, date(2026, 12, 31))
        self.assertEqual(label, "2026")

    def test_garbage_input_falls_back_to_defaults(self):
        start, end, label = resolve_period({"period": "bogus", "year": "notanumber"})
        today = date.today()
        self.assertEqual(start.year, today.year)
        self.assertEqual(start.month, today.month)


class PermissionsTests(TestCase):
    def test_hr_can_view(self):
        hr = make_employee("dashperm_hr", "EMP-DP-1", role=RoleAssignment.HR_PROCESSOR)
        self.assertTrue(can_view_dashboard(hr))

    def test_plain_employee_cannot_view(self):
        plain = make_employee("dashperm_plain", "EMP-DP-2")
        self.assertFalse(can_view_dashboard(plain))

    def test_supervisor_alone_cannot_view(self):
        supervisor = make_employee("dashperm_sup", "EMP-DP-3", role=RoleAssignment.SUPERVISOR)
        self.assertFalse(can_view_dashboard(supervisor))

    def test_none_cannot_view(self):
        self.assertFalse(can_view_dashboard(None))


class ApplicationSummaryStatsTests(TestCase):
    def test_buckets_leave_applications_by_outcome(self):
        employee = make_employee("statsleave_emp", "EMP-SL-1")
        target_date = date(2026, 3, 15)
        leave_type = LeaveType.objects.create(
            name="Vacation Leave", code="VL-BUCKET-TEST", applicable_to=LeaveType.APPLICABLE_BOTH,
            balance_tracking=LeaveType.TRACKING_UNTRACKED, requires_full_routing=True,
        )
        make_leave_application(employee, LeaveApplication.SUBMITTED, target_date, leave_type)
        make_leave_application(employee, LeaveApplication.APPROVED, target_date, leave_type)
        make_leave_application(employee, LeaveApplication.REJECTED, target_date, leave_type)

        counts = stats.leave_counts(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(counts, {"pending": 1, "approved": 1, "not_approved": 1})

    def test_excludes_applications_outside_period(self):
        employee = make_employee("statsleave_outside", "EMP-SL-2")
        make_leave_application(employee, LeaveApplication.SUBMITTED, date(2026, 1, 1))
        counts = stats.leave_counts(date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(counts, {"pending": 0, "approved": 0, "not_approved": 0})

    def test_application_summary_combines_all_types(self):
        employee = make_employee("statscombine_emp", "EMP-SC-1")
        make_leave_application(employee, LeaveApplication.APPROVED, date(2026, 4, 10))
        rows, totals = stats.application_summary(date(2026, 4, 1), date(2026, 4, 30))
        self.assertEqual(totals["approved"], 1)
        leave_row = next(r for r in rows if r["label"] == "Leave")
        self.assertEqual(leave_row["approved"], 1)

    def test_section_filter_scopes_to_department(self):
        section_a = Section.objects.create(name="Dashboard Test Section A")
        section_b = Section.objects.create(name="Dashboard Test Section B")
        emp_a = make_employee("statssection_a", "EMP-SS-1", sections=[section_a])
        emp_b = make_employee("statssection_b", "EMP-SS-2", sections=[section_b])
        make_leave_application(emp_a, LeaveApplication.APPROVED, date(2026, 5, 5))
        make_leave_application(emp_b, LeaveApplication.APPROVED, date(2026, 5, 5))

        counts_a = stats.leave_counts(date(2026, 5, 1), date(2026, 5, 31), section=section_a)
        self.assertEqual(counts_a["approved"], 1)


class HeadcountAndTodayStatsTests(TestCase):
    def test_headcount_counts_only_active_employees(self):
        active = make_employee("headcount_active", "EMP-HC-1")
        inactive = make_employee("headcount_inactive", "EMP-HC-2")
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        self.assertGreaterEqual(stats.headcount(), 1)
        self.assertTrue(Employee.objects.filter(pk=active.pk, is_active=True).exists())

    def test_on_leave_today_counts_approved_covering_today(self):
        employee = make_employee("onleave_emp", "EMP-OL-1")
        today = date.today()
        leave_type = LeaveType.objects.create(
            name="Vacation Leave", code="VL-ONLEAVE", applicable_to=LeaveType.APPLICABLE_BOTH,
            balance_tracking=LeaveType.TRACKING_UNTRACKED, requires_full_routing=True,
        )
        LeaveApplication.objects.create(
            employee=employee, leave_type=leave_type, start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1), number_of_days=3, status=LeaveApplication.APPROVED,
        )
        self.assertEqual(stats.on_leave_today(), 1)

    def test_on_cto_today_ignores_non_approved(self):
        employee = make_employee("oncto_emp", "EMP-OC-1")
        today = date.today()
        CTOUsageApplication.objects.create(
            employee=employee, start_date=today, end_date=today, number_of_days=1,
            reason="Test", status=CTOUsageApplication.SUBMITTED,
        )
        self.assertEqual(stats.on_cto_today(), 0)


class LeaveBalanceRowsTests(TestCase):
    def test_only_tracked_leave_types_are_columns(self):
        LeaveType.objects.filter(code="VL-BALROWS-UNTRACKED").delete()
        tracked = LeaveType.objects.create(
            name="Tracked Leave", code="VL-BALROWS-TRACKED", applicable_to=LeaveType.APPLICABLE_BOTH,
            balance_tracking=LeaveType.TRACKING_ACCRUED, monthly_accrual_days="1.25", requires_full_routing=False,
        )
        LeaveType.objects.create(
            name="Untracked Leave", code="VL-BALROWS-UNTRACKED", applicable_to=LeaveType.APPLICABLE_BOTH,
            balance_tracking=LeaveType.TRACKING_UNTRACKED, requires_full_routing=False,
        )
        make_employee("balrows_emp", "EMP-BR-1")
        leave_types, rows = stats.leave_balance_rows()
        self.assertIn(tracked, leave_types)
        self.assertTrue(all(lt.balance_tracking != LeaveType.TRACKING_UNTRACKED for lt in leave_types))
        self.assertTrue(len(rows) >= 1)


class DashboardViewAccessTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_hr_can_load_dashboard(self):
        make_employee("dashview_hr", "EMP-DV-1", role=RoleAssignment.HR_PROCESSOR)
        self.client.login(username="dashview_hr", password="testpass123")
        response = self.client.get(reverse("dashboard:dashboard_home"))
        self.assertEqual(response.status_code, 200)

    def test_plain_employee_gets_403(self):
        make_employee("dashview_plain", "EMP-DV-2")
        self.client.login(username="dashview_plain", password="testpass123")
        response = self.client.get(reverse("dashboard:dashboard_home"))
        self.assertEqual(response.status_code, 403)

    def test_leave_balance_report_excel_download(self):
        make_employee("dashview_hr2", "EMP-DV-3", role=RoleAssignment.HR_ADMINISTRATOR)
        self.client.login(username="dashview_hr2", password="testpass123")
        response = self.client.get(reverse("dashboard:leave_balance_report"), {"format": "xlsx"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_application_summary_pdf_download(self):
        make_employee("dashview_hr3", "EMP-DV-4", role=RoleAssignment.HR_ADMINISTRATOR)
        self.client.login(username="dashview_hr3", password="testpass123")
        response = self.client.get(reverse("dashboard:application_summary_report"), {"format": "pdf"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
