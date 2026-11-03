from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from employees.models import Employee
from orgstructure.models import Section

from .balances import compute_available_balance, contract_years_started, months_of_service
from .models import LeaveApplication, LeaveCreditTransaction, LeaveType
from .permissions import visible_applications_for


def make_employee(username, employee_id, date_hired=None, employment_status="REGULAR", role=None):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user,
        employee_id=employee_id,
        surname=username.title(),
        first_name="Test",
        date_hired=date_hired,
        employment_status=employment_status,
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    return employee


class MonthsAndYearsHelperTests(TestCase):
    def test_months_of_service_exact_boundary(self):
        hired = date(2025, 1, 15)
        self.assertEqual(months_of_service(hired, date(2025, 2, 14)), 0)
        self.assertEqual(months_of_service(hired, date(2025, 2, 15)), 1)
        self.assertEqual(months_of_service(hired, date(2026, 1, 15)), 12)

    def test_months_of_service_before_hire_is_zero(self):
        self.assertEqual(months_of_service(date(2026, 1, 1), date(2025, 1, 1)), 0)

    def test_months_of_service_no_hire_date_is_zero(self):
        self.assertEqual(months_of_service(None, date(2026, 1, 1)), 0)

    def test_contract_years_started_counts_first_year_on_hire_date(self):
        hired = date(2025, 3, 1)
        self.assertEqual(contract_years_started(hired, date(2025, 3, 1)), 1)
        self.assertEqual(contract_years_started(hired, date(2026, 2, 28)), 1)
        self.assertEqual(contract_years_started(hired, date(2026, 3, 1)), 2)


class AccruedBalanceTests(TestCase):
    """VL/SL: 1.25 days/month, cumulative."""

    def setUp(self):
        self.vl = LeaveType.objects.get(code="VL")
        self.employee = make_employee("accrue1", "EMP-A1", date_hired=date(2025, 1, 1))
        self.hr_user = User.objects.create_user(username="hr_ledger", password="x")

    def test_no_accrual_before_first_full_month(self):
        balance = compute_available_balance(self.employee, self.vl, as_of_date=date(2025, 1, 15))
        self.assertEqual(balance, Decimal("0"))

    def test_accrues_1_25_per_completed_month(self):
        balance = compute_available_balance(self.employee, self.vl, as_of_date=date(2025, 7, 1))
        # 6 full months completed (Jan 1 -> Jul 1)
        self.assertEqual(balance, Decimal("7.50"))

    def test_usage_reduces_balance(self):
        LeaveCreditTransaction.objects.create(
            employee=self.employee,
            leave_type=self.vl,
            transaction_type=LeaveCreditTransaction.USED,
            days=Decimal("-3.00"),
            transaction_date=date(2025, 5, 1),
            created_by=self.hr_user,
        )
        balance = compute_available_balance(self.employee, self.vl, as_of_date=date(2025, 7, 1))
        self.assertEqual(balance, Decimal("4.50"))  # 7.50 earned - 3.00 used

    def test_future_dated_usage_not_counted_yet(self):
        LeaveCreditTransaction.objects.create(
            employee=self.employee,
            leave_type=self.vl,
            transaction_type=LeaveCreditTransaction.USED,
            days=Decimal("-3.00"),
            transaction_date=date(2025, 8, 1),
            created_by=self.hr_user,
        )
        balance = compute_available_balance(self.employee, self.vl, as_of_date=date(2025, 7, 1))
        self.assertEqual(balance, Decimal("7.50"))


class AnnualCapNonCumulativeTests(TestCase):
    """Wellness Leave: 5 days/year, resets each calendar year, no accrual by tenure."""

    def setUp(self):
        self.wellness = LeaveType.objects.get(code="WELLNESS")
        self.employee = make_employee("wellness1", "EMP-W1", date_hired=date(2020, 6, 1))
        self.hr_user = User.objects.create_user(username="hr_ledger2", password="x")

    def test_full_5_days_available_regardless_of_tenure(self):
        balance = compute_available_balance(self.employee, self.wellness, as_of_date=date(2026, 1, 2))
        self.assertEqual(balance, Decimal("5"))

    def test_usage_this_year_reduces_balance(self):
        LeaveCreditTransaction.objects.create(
            employee=self.employee,
            leave_type=self.wellness,
            transaction_type=LeaveCreditTransaction.USED,
            days=Decimal("-2"),
            transaction_date=date(2026, 3, 1),
            created_by=self.hr_user,
        )
        balance = compute_available_balance(self.employee, self.wellness, as_of_date=date(2026, 6, 1))
        self.assertEqual(balance, Decimal("3"))

    def test_last_years_usage_does_not_carry_into_new_year(self):
        LeaveCreditTransaction.objects.create(
            employee=self.employee,
            leave_type=self.wellness,
            transaction_type=LeaveCreditTransaction.USED,
            days=Decimal("-5"),
            transaction_date=date(2025, 11, 1),
            created_by=self.hr_user,
        )
        balance = compute_available_balance(self.employee, self.wellness, as_of_date=date(2026, 1, 15))
        self.assertEqual(balance, Decimal("5"), "Wellness Leave is non-cumulative — must reset for the new year")


class AnnualCapCumulativeTests(TestCase):
    """COSP Leave: 20 days/year per contract, cumulative (carries over)."""

    def setUp(self):
        self.cosp_leave = LeaveType.objects.get(code="COSP_LEAVE")
        self.employee = make_employee(
            "cosp1", "EMP-C1", date_hired=date(2025, 1, 1), employment_status="COSP"
        )
        self.hr_user = User.objects.create_user(username="hr_ledger3", password="x")

    def test_first_contract_year_grants_full_20(self):
        balance = compute_available_balance(self.employee, self.cosp_leave, as_of_date=date(2025, 6, 1))
        self.assertEqual(balance, Decimal("20"))

    def test_unused_days_carry_into_second_contract_year(self):
        LeaveCreditTransaction.objects.create(
            employee=self.employee,
            leave_type=self.cosp_leave,
            transaction_type=LeaveCreditTransaction.USED,
            days=Decimal("-5"),
            transaction_date=date(2025, 6, 1),
            created_by=self.hr_user,
        )
        # Second contract year starts 2026-01-01: 20 (year 1) + 20 (year 2) = 40 earned, minus 5 used = 35
        balance = compute_available_balance(self.employee, self.cosp_leave, as_of_date=date(2026, 2, 1))
        self.assertEqual(balance, Decimal("35"))


class UntrackedLeaveTypeTests(TestCase):
    def test_untracked_types_return_none(self):
        maternity = LeaveType.objects.get(code="MATERNITY")
        employee = make_employee("mat1", "EMP-M1", date_hired=date(2020, 1, 1))
        self.assertIsNone(compute_available_balance(employee, maternity))

    def test_emergency_leave_is_untracked_but_requires_justification_flag_is_set(self):
        emergency = LeaveType.objects.get(code="EMERGENCY")
        self.assertTrue(emergency.requires_justification)
        employee = make_employee("emerg1", "EMP-E1", date_hired=date(2020, 1, 1))
        self.assertIsNone(compute_available_balance(employee, emergency))


class RoutingTests(TestCase):
    """
    §6.2's critical rule: Regular Leave (and every other CSC-listed type)
    must NEVER be routed through the internal approval engine — only COSP
    Leave does. These tests check that structurally, not just by intent.
    """

    def setUp(self):
        self.client = Client()
        self.section = Section.objects.get(name="Laboratory Section")

        self.employee = make_employee("leaveemp1", "EMP-L1", date_hired=date(2024, 1, 1))
        self.employee.sections.add(self.section)

        self.supervisor = make_employee("supervisor1", "EMP-L2", date_hired=date(2020, 1, 1))
        RoleAssignment.objects.create(
            employee=self.supervisor, role=RoleAssignment.SUPERVISOR, section=self.section
        )

        self.hr = make_employee("hrleave1", "EMP-L3", date_hired=date(2020, 1, 1), role=RoleAssignment.HR_ADMINISTRATOR)
        self.ao = make_employee("aoleave1", "EMP-L4", date_hired=date(2020, 1, 1), role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("cohleave1", "EMP-L5", date_hired=date(2020, 1, 1), role=RoleAssignment.CHIEF_OF_HOSPITAL)

        self.vl = LeaveType.objects.get(code="VL")
        self.cosp_leave = LeaveType.objects.get(code="COSP_LEAVE")

    def _submit(self, employee, leave_type, days=Decimal("2"), **overrides):
        defaults = dict(
            employee=employee,
            leave_type=leave_type,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 3),
            number_of_days=days,
        )
        defaults.update(overrides)
        return LeaveApplication.objects.create(**defaults)

    # Regular leave: internal chain ending "For Capitol approval" (dry-run
    # item 2, owner decisions 2026-11-03; replaces "data entry only").
    def _act(self, username, application, action, notes=""):
        self.client.login(username=username, password="testpass123")
        r = self.client.post(reverse("leave:leave_action", args=[application.pk]),
                             {"action": action, "notes": notes})
        application.refresh_from_db()
        return r

    def _used(self, application):
        from django.db.models import Sum
        return LeaveCreditTransaction.objects.filter(leave_application=application).aggregate(
            t=Sum("days"))["t"] or Decimal("0")

    def _to_hr(self, application):
        self._act("supervisor1", application, "endorse")
        self.assertEqual(application.status, LeaveApplication.ENDORSED_BY_SUPERVISOR)

    def test_regular_leave_goes_through_the_internal_chain_to_the_capitol(self):
        application = self._submit(self.employee, self.vl)
        self.assertEqual(application.route, LeaveApplication.ROUTE_REGULAR)
        self.assertIn(application, visible_applications_for(self.supervisor))
        self.assertNotIn(application, visible_applications_for(self.hr))
        self._to_hr(application)
        self.assertIn(application, visible_applications_for(self.hr))
        self._act("hrleave1", application, "record")
        self.assertEqual(application.status, LeaveApplication.PROCESSED_BY_HR)
        self.assertEqual(application.status_label, "Recorded and verified by HR")
        self.assertEqual(self._used(application), Decimal("-2"))  # days taken at HR
        self._act("aoleave1", application, "review")
        self.assertEqual(application.status, LeaveApplication.RECOMMENDED_BY_AO)
        self.assertEqual(self._act("cohleave1", application, "approve").status_code, 403)  # never "approved"
        self._act("cohleave1", application, "recommend_capitol")
        self.assertEqual(application.status, LeaveApplication.FOR_CAPITOL_APPROVAL)
        self.assertEqual(self._used(application), Decimal("-2"))
        self.assertNotIn(application, visible_applications_for(self.hr))
        self.assertTrue(LeaveApplication.objects.filter(LeaveApplication.counted_q(), pk=application.pk).exists())

    def test_hr_cannot_reject_regular_leave_but_may_return_it(self):
        application = self._submit(self.employee, self.vl)
        self._to_hr(application)
        self.assertEqual(self._act("hrleave1", application, "reject", "no").status_code, 403)
        self.assertEqual(application.status, LeaveApplication.ENDORSED_BY_SUPERVISOR)
        self._act("hrleave1", application, "return", "Attach the certificate")
        self.assertEqual(application.status, LeaveApplication.RETURNED)

    def test_ao_or_coh_rejection_gives_the_days_back(self):
        for step_user, action_before in (("aoleave1", None), ("cohleave1", "review")):
            application = self._submit(self.employee, self.vl)
            self._to_hr(application)
            self._act("hrleave1", application, "record")
            if action_before:
                self._act("aoleave1", application, action_before)
            self._act(step_user, application, "reject", "Not justified")
            self.assertEqual(application.status, LeaveApplication.REJECTED)
            self.assertEqual(self._used(application), Decimal("0"))

    def test_hr_records_capitol_return_and_rejection_and_days_come_back(self):
        for action, status in (("capitol_return", LeaveApplication.RETURNED),
                               ("capitol_reject", LeaveApplication.REJECTED)):
            application = self._submit(self.employee, self.vl)
            self._to_hr(application)
            self._act("hrleave1", application, "record")
            self._act("aoleave1", application, "review")
            self._act("cohleave1", application, "recommend_capitol")
            self._act("hrleave1", application, action)  # no remark: refused
            self.assertEqual(application.status, LeaveApplication.FOR_CAPITOL_APPROVAL)
            self._act("hrleave1", application, action, "Capitol: incomplete papers")
            self.assertEqual(application.status, status)
            self.assertEqual(self._used(application), Decimal("0"))
            self.assertEqual(application.actions.last().action, action)

    def test_resubmitted_after_capitol_return_takes_the_days_again(self):
        application = self._submit(self.employee, self.vl)
        self._to_hr(application)
        self._act("hrleave1", application, "record")
        self._act("aoleave1", application, "review")
        self._act("cohleave1", application, "recommend_capitol")
        self._act("hrleave1", application, "capitol_return", "Wrong dates")
        application.status = LeaveApplication.SUBMITTED
        application.save()
        self._to_hr(application)
        self._act("hrleave1", application, "record")
        self.assertEqual(self._used(application), Decimal("-2"))

    def test_hr_cannot_record_beyond_available_balance(self):
        application = self._submit(self.employee, self.vl, days=Decimal("999"))
        self._to_hr(application)
        self._act("hrleave1", application, "record")
        self.assertEqual(application.status, LeaveApplication.ENDORSED_BY_SUPERVISOR, "Must not record beyond balance")

    def test_coh_own_regular_leave_is_recorded_by_hr_then_goes_to_the_capitol(self):
        application = self._submit(self.coh, self.vl)
        self.assertIn(application, visible_applications_for(self.hr))
        self._act("hrleave1", application, "record")
        self.assertEqual(application.status, LeaveApplication.FOR_CAPITOL_APPROVAL)

    def test_cosp_wellness_goes_through_the_full_chain(self):
        cosp_employee = make_employee("cospwell", "EMP-LW", date_hired=date(2024, 1, 1), employment_status="COSP")
        cosp_employee.sections.add(self.section)
        wellness = LeaveType.objects.get(code="WELLNESS")
        application = self._submit(cosp_employee, wellness, days=Decimal("1"), wellness_reason="GENERAL_BREAK")
        self.assertEqual(application.route, LeaveApplication.ROUTE_COSP)
        self.assertIn(application, visible_applications_for(self.supervisor))
        self._act("supervisor1", application, "endorse")
        self._act("hrleave1", application, "process")
        self._act("aoleave1", application, "recommend")
        self._act("cohleave1", application, "approve")
        self.assertEqual(application.status, LeaveApplication.APPROVED)
        self.assertEqual(self._used(application), Decimal("-1"))

    def test_cosp_leave_goes_through_full_chain(self):
        cosp_employee = make_employee(
            "cospemp1", "EMP-L6", date_hired=date(2024, 1, 1), employment_status="COSP"
        )
        cosp_employee.sections.add(self.section)
        application = self._submit(cosp_employee, self.cosp_leave, days=Decimal("2"))

        # Supervisor endorses
        self.client.login(username="supervisor1", password="testpass123")
        self.client.post(reverse("leave:leave_action", args=[application.pk]), {"action": "endorse"})
        application.refresh_from_db()
        self.assertEqual(application.status, LeaveApplication.ENDORSED_BY_SUPERVISOR)

        # HR processes
        self.client.login(username="hrleave1", password="testpass123")
        self.client.post(reverse("leave:leave_action", args=[application.pk]), {"action": "process"})
        application.refresh_from_db()
        self.assertEqual(application.status, LeaveApplication.PROCESSED_BY_HR)

        # AO recommends
        self.client.login(username="aoleave1", password="testpass123")
        self.client.post(reverse("leave:leave_action", args=[application.pk]), {"action": "recommend"})
        application.refresh_from_db()
        self.assertEqual(application.status, LeaveApplication.RECOMMENDED_BY_AO)

        # COH approves
        self.client.login(username="cohleave1", password="testpass123")
        self.client.post(reverse("leave:leave_action", args=[application.pk]), {"action": "approve"})
        application.refresh_from_db()
        self.assertEqual(application.status, LeaveApplication.APPROVED)
        self.assertTrue(
            LeaveCreditTransaction.objects.filter(
                employee=cosp_employee, leave_type=self.cosp_leave, leave_application=application
            ).exists()
        )

    def test_hr_cannot_skip_straight_to_recommend_on_cosp_leave(self):
        cosp_employee = make_employee(
            "cospemp2", "EMP-L7", date_hired=date(2024, 1, 1), employment_status="COSP"
        )
        application = self._submit(cosp_employee, self.cosp_leave, days=Decimal("2"))
        self.client.login(username="aoleave1", password="testpass123")
        response = self.client.post(
            reverse("leave:leave_action", args=[application.pk]), {"action": "recommend"}
        )
        self.assertEqual(response.status_code, 403)
        application.refresh_from_db()
        self.assertEqual(application.status, LeaveApplication.SUBMITTED)

    def test_supervisor_outside_section_cannot_endorse(self):
        other_section = Section.objects.get(name="Pharmacy Section")
        other_supervisor = make_employee("othersup", "EMP-L8", date_hired=date(2020, 1, 1))
        RoleAssignment.objects.create(
            employee=other_supervisor, role=RoleAssignment.SUPERVISOR, section=other_section
        )
        cosp_employee = make_employee(
            "cospemp3", "EMP-L9", date_hired=date(2024, 1, 1), employment_status="COSP"
        )
        cosp_employee.sections.add(self.section)
        application = self._submit(cosp_employee, self.cosp_leave, days=Decimal("2"))

        self.client.login(username="othersup", password="testpass123")
        response = self.client.post(
            reverse("leave:leave_action", args=[application.pk]), {"action": "endorse"}
        )
        self.assertEqual(response.status_code, 403)


class PrintCscForm6Tests(TestCase):
    """The 'print CSC Form 6' view: who may open it, and that COSP Leave refuses it."""

    def setUp(self):
        self.section = Section.objects.get(name="Nursing Service Section")
        self.vl = LeaveType.objects.get(code="VL")
        self.cosp_leave = LeaveType.objects.get(code="COSP_LEAVE")
        self.employee = make_employee("printemp1", "EMP-P1", date_hired=date(2020, 1, 1))
        self.employee.sections.add(self.section)
        self.employee.position = "Nurse II"
        self.employee.salary_grade = "15"
        self.employee.save()
        self.application = LeaveApplication.objects.create(
            employee=self.employee, leave_type=self.vl,
            start_date=date(2026, 10, 5), end_date=date(2026, 10, 9), number_of_days=Decimal("5"),
        )

    def test_applicant_can_print_their_own_application(self):
        self.client.login(username="printemp1", password="testpass123")
        response = self.client.get(reverse("leave:print_csc_form6", args=[self.application.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_unrelated_employee_cannot_print(self):
        make_employee("printemp2", "EMP-P2", date_hired=date(2020, 1, 1))
        self.client.login(username="printemp2", password="testpass123")
        response = self.client.get(reverse("leave:print_csc_form6", args=[self.application.pk]))
        self.assertEqual(response.status_code, 403)

    def test_hr_can_print_any_application(self):
        make_employee("printhr1", "EMP-P3", date_hired=date(2020, 1, 1), role=RoleAssignment.HR_PROCESSOR)
        self.client.login(username="printhr1", password="testpass123")
        response = self.client.get(reverse("leave:print_csc_form6", args=[self.application.pk]))
        self.assertEqual(response.status_code, 200)

    def test_cosp_leave_refuses_this_form(self):
        cosp_employee = make_employee(
            "printcosp1", "EMP-P4", date_hired=date(2024, 1, 1), employment_status="COSP"
        )
        application = LeaveApplication.objects.create(
            employee=cosp_employee, leave_type=self.cosp_leave,
            start_date=date(2026, 10, 5), end_date=date(2026, 10, 6), number_of_days=Decimal("2"),
        )
        self.client.login(username="printcosp1", password="testpass123")
        response = self.client.get(reverse("leave:print_csc_form6", args=[application.pk]))
        self.assertEqual(response.status_code, 403)


class PrintCospLeaveFormTests(TestCase):
    """The 'print COSP Leave form' view: who may open it, and that non-COSP types refuse it."""

    def setUp(self):
        self.section = Section.objects.get(name="Nursing Service Section")
        self.vl = LeaveType.objects.get(code="VL")
        self.cosp_leave = LeaveType.objects.get(code="COSP_LEAVE")
        self.employee = make_employee(
            "cospformemp1", "EMP-C1", date_hired=date(2024, 1, 1), employment_status="COSP"
        )
        self.employee.sections.add(self.section)
        self.application = LeaveApplication.objects.create(
            employee=self.employee, leave_type=self.cosp_leave,
            start_date=date(2026, 10, 5), end_date=date(2026, 10, 9), number_of_days=Decimal("5"),
        )

    def test_applicant_can_print_their_own_application(self):
        self.client.login(username="cospformemp1", password="testpass123")
        response = self.client.get(reverse("leave:print_cosp_leave_form", args=[self.application.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_unrelated_employee_cannot_print(self):
        make_employee("cospformemp2", "EMP-C2", date_hired=date(2020, 1, 1))
        self.client.login(username="cospformemp2", password="testpass123")
        response = self.client.get(reverse("leave:print_cosp_leave_form", args=[self.application.pk]))
        self.assertEqual(response.status_code, 403)

    def test_supervisor_of_applicant_can_print(self):
        make_employee(
            "cospformsup1", "EMP-C3", date_hired=date(2020, 1, 1), role=RoleAssignment.SUPERVISOR
        )
        sup = Employee.objects.get(employee_id="EMP-C3")
        sup.sections.add(self.section)
        self.client.login(username="cospformsup1", password="testpass123")
        response = self.client.get(reverse("leave:print_cosp_leave_form", args=[self.application.pk]))
        self.assertEqual(response.status_code, 200)

    def test_vl_refuses_this_form(self):
        vl_employee = make_employee("cospformvl1", "EMP-C4", date_hired=date(2020, 1, 1))
        application = LeaveApplication.objects.create(
            employee=vl_employee, leave_type=self.vl,
            start_date=date(2026, 10, 5), end_date=date(2026, 10, 6), number_of_days=Decimal("2"),
        )
        self.client.login(username="cospformvl1", password="testpass123")
        response = self.client.get(reverse("leave:print_cosp_leave_form", args=[application.pk]))
        self.assertEqual(response.status_code, 403)


class QueueOrderTests(TestCase):
    """Owner decision (2026-10-06): approval queues list the OLDEST request
    first - first filed, first acted on."""

    def test_leave_queue_is_oldest_first(self):
        from datetime import timedelta

        from django.utils import timezone

        hr = make_employee("queue_hr", "EMP-Q-0", role=RoleAssignment.HR_PROCESSOR)
        vl = LeaveType.objects.get(code="VL")
        made = []
        for i, name in enumerate(["queue_zed", "queue_amy", "queue_bob"]):
            emp = make_employee(name, f"EMP-Q-{i + 1}", date_hired=date(2020, 1, 1))
            app = LeaveApplication.objects.create(employee=emp, leave_type=vl, start_date=date(2026, 11, 2),
                                                  end_date=date(2026, 11, 2), number_of_days=Decimal("1"))
            LeaveApplication.objects.filter(pk=app.pk).update(
                submitted_at=timezone.now() - timedelta(days=10 - i), status=LeaveApplication.ENDORSED_BY_SUPERVISOR)
            made.append(app.pk)
        self.client.force_login(hr.user)
        listed = [a.pk for a in self.client.get(reverse("leave:leave_queue")).context["applications"]]
        self.assertEqual(listed, made)  # filed 10, 9, 8 days ago -> shown in that order, not by name


class BalanceCardTests(TestCase):
    """Batch 2, Item 5: 'My balances' cards on the Apply for Leave page."""

    TODAY = date(2026, 10, 7)

    def setUp(self):
        self.recorder = User.objects.create_user("bc_recorder", password="x")

    def cards(self, employee):
        from .balance_cards import balance_cards

        return {c["key"]: c for c in balance_cards(employee, today=self.TODAY)}

    def test_regular_gets_vl_sl_wellness_emergency_and_cto(self):
        emp = make_employee("bc_reg", "BC-1", date_hired=date(2026, 1, 7))  # 9 full months -> 11.25 VL
        vl = LeaveType.objects.get(code="VL")
        LeaveCreditTransaction.objects.create(employee=emp, leave_type=vl, transaction_type="USED", days=-2,
                                              transaction_date=date(2026, 5, 4), created_by=self.recorder)
        cards = self.cards(emp)
        self.assertEqual(list(cards), ["VL", "SL", "WELLNESS", "EMERGENCY", "CTO"])
        self.assertEqual((cards["VL"]["remaining"], cards["VL"]["used"], cards["VL"]["total"]),
                         (Decimal("9.25"), Decimal("2"), Decimal("11.25")))
        self.assertEqual(cards["VL"]["colour"], "green")
        self.assertEqual(cards["WELLNESS"]["note"], "Expires Dec 31")
        self.assertIsNone(cards["EMERGENCY"]["remaining"])
        self.assertEqual(cards["EMERGENCY"]["colour"], "neutral")
        self.assertEqual(cards["CTO"]["note"], "Use by Dec 15")

    def test_cosp_gets_contract_leave_not_vl_sl(self):
        emp = make_employee("bc_cosp", "BC-2", date_hired=date(2026, 1, 1), employment_status="COSP")
        self.assertEqual(list(self.cards(emp)), ["COSP_LEAVE", "WELLNESS", "EMERGENCY", "CTO"])

    def test_colours(self):
        from .balance_cards import status_colour

        self.assertEqual(status_colour(Decimal("0"), Decimal("5")), "red")
        self.assertEqual(status_colour(Decimal("1"), Decimal("20")), "red")
        self.assertEqual(status_colour(Decimal("5"), Decimal("20")), "yellow")  # 25%
        self.assertEqual(status_colour(Decimal("6"), Decimal("20")), "green")
        self.assertEqual(status_colour(None, None), "neutral")

    def test_cto_card_uses_existing_ledger_and_is_hidden_for_coh(self):
        from cto.models import CTOCreditAllocation, CTOUsageApplication
        from cto.opening import add_opening_line

        emp = make_employee("bc_cto", "BC-3", date_hired=date(2020, 1, 1))
        hr_admin = make_employee("bc_hradmin", "BC-9")
        RoleAssignment.objects.create(employee=hr_admin, role=RoleAssignment.HR_ADMINISTRATOR)
        # 3 days earned (opening line, CLAUDE.md 7.1), 1 day used on an approved CTO application.
        line = add_opening_line(hr_admin, emp, Decimal("3"), date(2026, 3, 1), "paper card", hr_admin.user,
                                date(2026, 3, 1))
        app = CTOUsageApplication.objects.create(employee=emp, start_date=date(2026, 4, 1), end_date=date(2026, 4, 1),
                                                 number_of_days=1, reason="rest", status=CTOUsageApplication.APPROVED)
        CTOCreditAllocation.objects.create(line=line, usage_application=app, days=1)
        cto = self.cards(emp)["CTO"]
        self.assertEqual((cto["remaining"], cto["used"], cto["total"]), (Decimal("2"), Decimal("1"), Decimal("3")))
        RoleAssignment.objects.create(employee=emp, role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.assertNotIn("CTO", self.cards(emp))

    def test_apply_page_shows_cards(self):
        emp = make_employee("bc_page", "BC-4", date_hired=date(2025, 1, 1))
        self.client.force_login(emp.user)
        page = self.client.get(reverse("leave:leave_apply")).content.decode()
        self.assertIn("My balances", page)
        self.assertEqual(page.count('class="balance-card '), 5)
        self.assertIn("No fixed limit", page)
