from datetime import date, time
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from employees.models import Employee

from .models import OfficialRequest
from .routing import next_status, routing_steps


def make_employee(username, employee_id, role=None):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test",
        date_hired=date(2024, 1, 1), employment_status="REGULAR",
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    return employee


class RoutingStepsTests(TestCase):
    """CLAUDE.md §6.2's four chains, generalized via ROUTING_CONFIG."""

    def test_official_business_full_chain(self):
        self.assertEqual(
            routing_steps(OfficialRequest.OFFICIAL_BUSINESS),
            [
                OfficialRequest.SUBMITTED, OfficialRequest.ENDORSED_BY_SUPERVISOR,
                OfficialRequest.PROCESSED_BY_HR, OfficialRequest.RECOMMENDED_BY_AO, OfficialRequest.APPROVED,
            ],
        )

    def test_official_time_skips_supervisor(self):
        steps = routing_steps(OfficialRequest.OFFICIAL_TIME)
        self.assertNotIn(OfficialRequest.ENDORSED_BY_SUPERVISOR, steps)
        self.assertIn(OfficialRequest.PROCESSED_BY_HR, steps)

    def test_travel_skips_hr(self):
        steps = routing_steps(OfficialRequest.TRAVEL)
        self.assertIn(OfficialRequest.ENDORSED_BY_SUPERVISOR, steps)
        self.assertNotIn(OfficialRequest.PROCESSED_BY_HR, steps)

    def test_ot_restday_holiday_full_chain(self):
        self.assertEqual(
            routing_steps(OfficialRequest.OT_RESTDAY_HOLIDAY), routing_steps(OfficialRequest.OFFICIAL_BUSINESS)
        )

    def test_next_status_terminal_at_approved(self):
        self.assertIsNone(next_status(OfficialRequest.OFFICIAL_BUSINESS, OfficialRequest.APPROVED))

    def test_next_status_official_time_from_submitted_skips_to_hr(self):
        self.assertEqual(
            next_status(OfficialRequest.OFFICIAL_TIME, OfficialRequest.SUBMITTED), OfficialRequest.PROCESSED_BY_HR
        )

    def test_next_status_travel_from_supervisor_skips_to_ao(self):
        self.assertEqual(
            next_status(OfficialRequest.TRAVEL, OfficialRequest.ENDORSED_BY_SUPERVISOR),
            OfficialRequest.RECOMMENDED_BY_AO,
        )


class ModelValidationTests(TestCase):
    def setUp(self):
        self.employee = make_employee("val1", "EMP-OFVAL1")

    def test_ot_request_requires_hours(self):
        req = OfficialRequest(
            request_type=OfficialRequest.OT_RESTDAY_HOLIDAY, employee=self.employee,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 1), purpose="Emergency duty coverage",
        )
        with self.assertRaises(Exception):
            req.full_clean()

    def test_travel_requires_destination(self):
        req = OfficialRequest(
            request_type=OfficialRequest.TRAVEL, employee=self.employee,
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 2), purpose="Conference",
        )
        with self.assertRaises(Exception):
            req.full_clean()

    def test_end_before_start_is_invalid(self):
        req = OfficialRequest(
            request_type=OfficialRequest.OFFICIAL_BUSINESS, employee=self.employee,
            start_date=date(2026, 6, 5), end_date=date(2026, 6, 1), purpose="Errand",
        )
        with self.assertRaises(Exception):
            req.full_clean()


class OfficialBusinessRoutingTests(TestCase):
    """Full chain: Employee -> Supervisor -> HR -> AO -> COH."""

    def setUp(self):
        self.employee = make_employee("ob1", "EMP-OB1")
        self.supervisor = make_employee("obsup1", "EMP-OBSUP1", role=RoleAssignment.SUPERVISOR)
        self.hr = make_employee("obhr1", "EMP-OBHR1", role=RoleAssignment.HR_PROCESSOR)
        self.ao = make_employee("obao1", "EMP-OBAO1", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("obcoh1", "EMP-OBCOH1", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.client = Client()

    def _file(self):
        self.client.force_login(self.employee.user)
        self.client.post(reverse("official_requests:request_apply"), {
            "request_type": OfficialRequest.OFFICIAL_BUSINESS,
            "start_date": "2026-07-01", "end_date": "2026-07-02", "purpose": "Attend seminar",
        })
        return OfficialRequest.objects.get()

    def test_full_chain_to_approval(self):
        req = self._file()
        self.client.force_login(self.supervisor.user)
        self.client.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "endorse"})
        self.client.force_login(self.hr.user)
        self.client.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "process"})
        self.client.force_login(self.ao.user)
        self.client.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "recommend"})
        self.client.force_login(self.coh.user)
        self.client.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "approve"})
        req.refresh_from_db()
        self.assertEqual(req.status, OfficialRequest.APPROVED)

    def test_hr_cannot_skip_ahead_of_supervisor(self):
        req = self._file()
        self.client.force_login(self.hr.user)
        response = self.client.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "process"})
        self.assertEqual(response.status_code, 403)

    def test_wrong_action_name_is_refused(self):
        req = self._file()
        self.client.force_login(self.supervisor.user)
        response = self.client.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "approve"})
        self.assertEqual(response.status_code, 403)
        req.refresh_from_db()
        self.assertEqual(req.status, OfficialRequest.SUBMITTED)


class OfficialTimeRoutingTests(TestCase):
    """No Supervisor step: Employee -> HR -> AO -> COH."""

    def setUp(self):
        self.employee = make_employee("ot1", "EMP-OT1")
        self.supervisor = make_employee("otsup1", "EMP-OTSUP1", role=RoleAssignment.SUPERVISOR)
        self.hr = make_employee("othr1", "EMP-OTHR1", role=RoleAssignment.HR_PROCESSOR)
        self.ao = make_employee("otao1", "EMP-OTAO1", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("otcoh1", "EMP-OTCOH1", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.client = Client()
        self.client.force_login(self.employee.user)
        self.client.post(reverse("official_requests:request_apply"), {
            "request_type": OfficialRequest.OFFICIAL_TIME,
            "start_date": "2026-07-03", "end_date": "2026-07-03",
            "time_from": "13:00", "time_to": "17:00", "purpose": "Government transaction",
        })
        self.req = OfficialRequest.objects.get()

    def test_supervisor_cannot_act_on_official_time(self):
        self.client.force_login(self.supervisor.user)
        response = self.client.post(
            reverse("official_requests:request_action", args=[self.req.pk]), {"action": "endorse"}
        )
        self.assertEqual(response.status_code, 403)

    def test_hr_can_process_directly_from_submitted(self):
        self.client.force_login(self.hr.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "process"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, OfficialRequest.PROCESSED_BY_HR)

    def test_full_chain_to_approval(self):
        self.client.force_login(self.hr.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "process"})
        self.client.force_login(self.ao.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "recommend"})
        self.client.force_login(self.coh.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "approve"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, OfficialRequest.APPROVED)


class TravelRoutingTests(TestCase):
    """No HR step: Employee -> Supervisor -> AO -> COH."""

    def setUp(self):
        self.employee = make_employee("tr1", "EMP-TR1")
        self.supervisor = make_employee("trsup1", "EMP-TRSUP1", role=RoleAssignment.SUPERVISOR)
        self.hr = make_employee("trhr1", "EMP-TRHR1", role=RoleAssignment.HR_PROCESSOR)
        self.ao = make_employee("trao1", "EMP-TRAO1", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("trcoh1", "EMP-TRCOH1", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.client = Client()
        self.client.force_login(self.employee.user)
        self.client.post(reverse("official_requests:request_apply"), {
            "request_type": OfficialRequest.TRAVEL,
            "start_date": "2026-07-10", "end_date": "2026-07-12",
            "destination": "Puerto Princesa", "purpose": "Regional training",
        })
        self.req = OfficialRequest.objects.get()

    def test_supervisor_endorses_then_ao_recommends_directly(self):
        self.client.force_login(self.supervisor.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "endorse"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, OfficialRequest.ENDORSED_BY_SUPERVISOR)

        self.client.force_login(self.hr.user)
        response = self.client.post(
            reverse("official_requests:request_action", args=[self.req.pk]), {"action": "process"}
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.ao.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "recommend"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, OfficialRequest.RECOMMENDED_BY_AO)

        self.client.force_login(self.coh.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "approve"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, OfficialRequest.APPROVED)


class RejectReturnTests(TestCase):
    def setUp(self):
        self.employee = make_employee("rej1", "EMP-REJ1")
        self.supervisor = make_employee("rejsup1", "EMP-REJSUP1", role=RoleAssignment.SUPERVISOR)
        self.client = Client()
        self.client.force_login(self.employee.user)
        self.client.post(reverse("official_requests:request_apply"), {
            "request_type": OfficialRequest.OFFICIAL_BUSINESS,
            "start_date": "2026-07-01", "end_date": "2026-07-01", "purpose": "Errand",
        })
        self.req = OfficialRequest.objects.get()

    def test_supervisor_can_reject(self):
        self.client.force_login(self.supervisor.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "reject"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, OfficialRequest.REJECTED)

    def test_unrelated_employee_cannot_reject(self):
        outsider = make_employee("rejout1", "EMP-REJOUT1")
        self.client.force_login(outsider.user)
        response = self.client.post(
            reverse("official_requests:request_action", args=[self.req.pk]), {"action": "reject"}
        )
        self.assertEqual(response.status_code, 403)
