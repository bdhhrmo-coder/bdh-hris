from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from employees.models import Employee

from .models import DutyExchangeRequest
from .rules import exceeds_monthly_cap, violates_minimum_notice


def make_employee(username, employee_id, role=None):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test",
        date_hired=date(2024, 1, 1), employment_status="REGULAR",
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    return employee


class MinimumNoticeTests(TestCase):
    """CLAUDE.md §8: both timing buckets (1 week for a normal request, 24-72
    hours for a schedule modification) share a 24-hour floor — the only bar
    this system enforces without a duty-schedule module (confirmed
    2026-09-27); a documented emergency may be filed same-day."""

    def test_same_day_filing_without_emergency_violates(self):
        today = date(2026, 5, 10)
        self.assertTrue(violates_minimum_notice(today, today, is_emergency=False))

    def test_same_day_filing_as_emergency_is_allowed(self):
        today = date(2026, 5, 10)
        self.assertFalse(violates_minimum_notice(today, today, is_emergency=True))

    def test_24_hours_notice_is_allowed(self):
        filed = date(2026, 5, 10)
        exchange_date = filed + timedelta(days=1)
        self.assertFalse(violates_minimum_notice(exchange_date, filed, is_emergency=False))

    def test_one_week_notice_is_allowed(self):
        filed = date(2026, 5, 1)
        exchange_date = filed + timedelta(days=7)
        self.assertFalse(violates_minimum_notice(exchange_date, filed, is_emergency=False))


class MonthlyCapTests(TestCase):
    """CLAUDE.md §8: 'Max 3 requests/month' — confirmed 2026-09-27 as
    per employee, per calendar month of filing."""

    def setUp(self):
        self.alice = make_employee("alice_ex", "EMP-EXA1")
        self.bob = make_employee("bob_ex", "EMP-EXB1")
        self.carol = make_employee("carol_ex", "EMP-EXC1")

    def _file(self, a, b, filed_on=date(2026, 6, 15), status=DutyExchangeRequest.SUBMITTED):
        from django.utils import timezone

        req = DutyExchangeRequest.objects.create(
            employee_a=a, date_a=date(2026, 6, 1), employee_b=b, date_b=date(2026, 6, 2), status=status,
        )
        # filed_at is auto_now_add (set to the actual test-run time); tests
        # need to control which calendar month a request was filed in.
        req.filed_at = timezone.make_aware(
            timezone.datetime.combine(filed_on, timezone.datetime.min.time())
        )
        req.save(update_fields=["filed_at"])
        return req

    def test_third_request_is_still_allowed(self):
        self._file(self.alice, self.bob)
        self._file(self.alice, self.carol)
        self.assertFalse(exceeds_monthly_cap(self.alice, filed_date=date(2026, 6, 15)))

    def test_fourth_request_is_blocked(self):
        self._file(self.alice, self.bob)
        self._file(self.alice, self.carol)
        self._file(self.alice, self.bob)
        self.assertTrue(exceeds_monthly_cap(self.alice, filed_date=date(2026, 6, 15)))

    def test_declined_requests_do_not_count(self):
        self._file(self.alice, self.bob, status=DutyExchangeRequest.CONSENT_DECLINED)
        self._file(self.alice, self.carol, status=DutyExchangeRequest.CONSENT_DECLINED)
        self._file(self.alice, self.bob, status=DutyExchangeRequest.CONSENT_DECLINED)
        self.assertFalse(exceeds_monthly_cap(self.alice, filed_date=date(2026, 6, 15)))

    def test_counts_employee_whether_a_or_b(self):
        self._file(self.alice, self.bob)
        self._file(self.carol, self.bob)
        self._file(self.bob, self.alice)
        self.assertTrue(exceeds_monthly_cap(self.bob, filed_date=date(2026, 6, 15)))

    def test_cap_is_per_calendar_month(self):
        self._file(self.alice, self.bob, filed_on=date(2026, 5, 15))
        self.assertFalse(exceeds_monthly_cap(self.alice, filed_date=date(2026, 6, 1)))


class ModelValidationTests(TestCase):
    def setUp(self):
        self.alice = make_employee("alice_val", "EMP-VALA1")
        self.bob = make_employee("bob_val", "EMP-VALB1")

    def test_cannot_exchange_with_self(self):
        req = DutyExchangeRequest(
            employee_a=self.alice, date_a=date(2026, 6, 1), employee_b=self.alice, date_b=date(2026, 6, 2),
        )
        with self.assertRaises(Exception):
            req.full_clean()

    def test_dates_must_differ(self):
        req = DutyExchangeRequest(
            employee_a=self.alice, date_a=date(2026, 6, 1), employee_b=self.bob, date_b=date(2026, 6, 1),
        )
        with self.assertRaises(Exception):
            req.full_clean()

    def test_earliest_affected_date(self):
        req = DutyExchangeRequest.objects.create(
            employee_a=self.alice, date_a=date(2026, 6, 10), employee_b=self.bob, date_b=date(2026, 6, 5),
        )
        self.assertEqual(req.earliest_affected_date, date(2026, 6, 5))


class ConsentFlowTests(TestCase):
    """CLAUDE.md §8: 'Requires mutual written consent between the two
    employees' — confirmed 2026-09-27 as a real in-system consent step,
    gating entry into the Supervisor/HR/AO/COH chain."""

    def setUp(self):
        self.alice = make_employee("alice_con", "EMP-CONA1")
        self.bob = make_employee("bob_con", "EMP-CONB1")
        self.request = DutyExchangeRequest.objects.create(
            employee_a=self.alice, date_a=date(2026, 8, 1), employee_b=self.bob, date_b=date(2026, 8, 2),
            status=DutyExchangeRequest.PENDING_CONSENT,
        )
        self.client = Client()

    def test_only_employee_b_can_consent(self):
        self.client.force_login(self.alice.user)
        response = self.client.post(
            reverse("exchange:exchange_consent", args=[self.request.pk]), {"decision": "consent"}
        )
        self.assertEqual(response.status_code, 403)

    def test_employee_b_consenting_moves_to_submitted(self):
        self.client.force_login(self.bob.user)
        self.client.post(reverse("exchange:exchange_consent", args=[self.request.pk]), {"decision": "consent"})
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, DutyExchangeRequest.SUBMITTED)
        self.assertIsNotNone(self.request.consented_at)

    def test_employee_b_declining_is_terminal(self):
        self.client.force_login(self.bob.user)
        self.client.post(reverse("exchange:exchange_consent", args=[self.request.pk]), {"decision": "decline"})
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, DutyExchangeRequest.CONSENT_DECLINED)

    def test_cannot_consent_twice(self):
        self.client.force_login(self.bob.user)
        self.client.post(reverse("exchange:exchange_consent", args=[self.request.pk]), {"decision": "consent"})
        response = self.client.post(
            reverse("exchange:exchange_consent", args=[self.request.pk]), {"decision": "consent"}
        )
        self.assertEqual(response.status_code, 403)


class RoutingPermissionTests(TestCase):
    """§8: full Employee -> Supervisor -> HR -> AO -> COH chain, reusing
    leave.permissions' role checks — consent is a gate before this, not a
    substitute for it."""

    def setUp(self):
        self.alice = make_employee("alice_route", "EMP-ROUTEA2")
        self.bob = make_employee("bob_route", "EMP-ROUTEB2")
        self.supervisor = make_employee("sup_route", "EMP-ROUTESUP2", role=RoleAssignment.SUPERVISOR)
        self.hr = make_employee("hr_route", "EMP-ROUTEHR2", role=RoleAssignment.HR_PROCESSOR)
        self.ao = make_employee("ao_route", "EMP-ROUTEAO2", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("coh_route", "EMP-ROUTECOH2", role=RoleAssignment.CHIEF_OF_HOSPITAL)

        self.request = DutyExchangeRequest.objects.create(
            employee_a=self.alice, date_a=date(2026, 8, 1), employee_b=self.bob, date_b=date(2026, 8, 2),
            status=DutyExchangeRequest.SUBMITTED,
        )
        self.client = Client()

    def test_supervisor_cannot_act_before_consent(self):
        pending = DutyExchangeRequest.objects.create(
            employee_a=self.alice, date_a=date(2026, 9, 1), employee_b=self.bob, date_b=date(2026, 9, 2),
            status=DutyExchangeRequest.PENDING_CONSENT,
        )
        self.client.force_login(self.supervisor.user)
        response = self.client.post(reverse("exchange:exchange_action", args=[pending.pk]), {"action": "endorse"})
        self.assertEqual(response.status_code, 403)

    def test_full_chain_to_approval(self):
        self.client.force_login(self.supervisor.user)
        self.client.post(reverse("exchange:exchange_action", args=[self.request.pk]), {"action": "endorse"})
        self.client.force_login(self.hr.user)
        self.client.post(reverse("exchange:exchange_action", args=[self.request.pk]), {"action": "process"})
        self.client.force_login(self.ao.user)
        self.client.post(reverse("exchange:exchange_action", args=[self.request.pk]), {"action": "recommend"})
        self.client.force_login(self.coh.user)
        self.client.post(reverse("exchange:exchange_action", args=[self.request.pk]), {"action": "approve"})

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, DutyExchangeRequest.APPROVED)

    def test_hr_cannot_act_before_supervisor_endorsement(self):
        self.client.force_login(self.hr.user)
        response = self.client.post(reverse("exchange:exchange_action", args=[self.request.pk]), {"action": "process"})
        self.assertEqual(response.status_code, 403)

    def test_unrelated_employee_cannot_endorse(self):
        outsider = make_employee("outsider_route", "EMP-ROUTEOUT2")
        self.client.force_login(outsider.user)
        response = self.client.post(reverse("exchange:exchange_action", args=[self.request.pk]), {"action": "endorse"})
        self.assertEqual(response.status_code, 403)


class ApplyFormTests(TestCase):
    """End-to-end form validation, including the monthly-cap and
    minimum-notice checks wired into DutyExchangeRequestForm."""

    def setUp(self):
        self.alice = make_employee("alice_form", "EMP-FORMA1")
        self.bob = make_employee("bob_form", "EMP-FORMB1")
        self.client = Client()

    def test_filing_creates_pending_consent_request(self):
        self.client.force_login(self.alice.user)
        far_future_a = date.today() + timedelta(days=30)
        far_future_b = date.today() + timedelta(days=31)
        response = self.client.post(reverse("exchange:exchange_apply"), {
            "employee_b": self.bob.pk, "date_a": far_future_a, "date_b": far_future_b, "reason": "test",
        })
        self.assertEqual(DutyExchangeRequest.objects.count(), 1)
        req = DutyExchangeRequest.objects.first()
        self.assertEqual(req.status, DutyExchangeRequest.PENDING_CONSENT)

    def test_same_day_filing_without_emergency_is_rejected(self):
        self.client.force_login(self.alice.user)
        today = date.today()
        response = self.client.post(reverse("exchange:exchange_apply"), {
            "employee_b": self.bob.pk, "date_a": today, "date_b": today + timedelta(days=1), "reason": "test",
        })
        self.assertEqual(DutyExchangeRequest.objects.count(), 0)

    def test_same_day_filing_as_documented_emergency_is_accepted(self):
        self.client.force_login(self.alice.user)
        today = date.today()
        response = self.client.post(reverse("exchange:exchange_apply"), {
            "employee_b": self.bob.pk, "date_a": today, "date_b": today + timedelta(days=1),
            "reason": "test", "is_emergency": "on", "emergency_justification": "Sudden family emergency.",
        })
        self.assertEqual(DutyExchangeRequest.objects.count(), 1)
