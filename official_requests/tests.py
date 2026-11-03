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
    """Routes after the owner decisions of 2026-11-03."""

    def test_official_business_travel_order_chain(self):
        self.assertEqual(
            routing_steps(OfficialRequest.OFFICIAL_BUSINESS),
            [
                OfficialRequest.SUBMITTED, OfficialRequest.ENDORSED_BY_SUPERVISOR,
                OfficialRequest.PROCESSED_BY_HR, OfficialRequest.RECOMMENDED_BY_AO,
                OfficialRequest.FORWARDED_TO_CAPITOL, OfficialRequest.APPROVED,
            ],
        )

    def test_local_errand_supervisor_then_ao_approves(self):
        self.assertEqual(
            routing_steps(OfficialRequest.TRAVEL),
            [OfficialRequest.SUBMITTED, OfficialRequest.ENDORSED_BY_SUPERVISOR, OfficialRequest.APPROVED],
        )

    def test_ot_restday_holiday_chain_unchanged(self):
        self.assertEqual(
            routing_steps(OfficialRequest.OT_RESTDAY_HOLIDAY),
            [
                OfficialRequest.SUBMITTED, OfficialRequest.ENDORSED_BY_SUPERVISOR,
                OfficialRequest.PROCESSED_BY_HR, OfficialRequest.RECOMMENDED_BY_AO, OfficialRequest.APPROVED,
            ],
        )

    def test_next_status_terminal_at_approved(self):
        self.assertIsNone(next_status(OfficialRequest.OFFICIAL_BUSINESS, OfficialRequest.APPROVED))

    def test_official_time_is_not_offered(self):
        from .forms import OfficialRequestForm

        codes = [c for c, _ in OfficialRequestForm().fields["request_type"].choices]
        self.assertNotIn(OfficialRequest.OFFICIAL_TIME, codes)
        self.assertIn(OfficialRequest.OFFICIAL_BUSINESS, codes)
        self.assertEqual(dict(OfficialRequest.REQUEST_TYPE_CHOICES)[OfficialRequest.OFFICIAL_BUSINESS],
                         "Official Business (Travel Order)")
        self.assertEqual(dict(OfficialRequest.REQUEST_TYPE_CHOICES)[OfficialRequest.TRAVEL], "Local Official Errand")


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


OB_POST = {
    "request_type": OfficialRequest.OFFICIAL_BUSINESS, "start_date": "2026-07-01", "end_date": "2026-07-02",
    "destination": "Puerto Princesa City", "expenses_authorized": OfficialRequest.PER_DIEM,
    "transportation_allowed": "Government Vehicle", "purpose": "Attend seminar",
}


class OfficialBusinessRoutingTests(TestCase):
    """Travel Order: Supervisor -> HR -> AO -> COH (recommending approval,
    forwarded to the Capitol) -> HR records the Governor's decision."""

    def setUp(self):
        self.employee = make_employee("ob1", "EMP-OB1")
        self.supervisor = make_employee("obsup1", "EMP-OBSUP1", role=RoleAssignment.SUPERVISOR)
        self.hr = make_employee("obhr1", "EMP-OBHR1", role=RoleAssignment.HR_PROCESSOR)
        self.ao = make_employee("obao1", "EMP-OBAO1", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("obcoh1", "EMP-OBCOH1", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.client = Client()

    def _file(self, **extra):
        self.client.force_login(self.employee.user)
        self.client.post(reverse("official_requests:request_apply"), dict(OB_POST, **extra))
        return OfficialRequest.objects.get()

    def _act(self, who, req, action, notes=""):
        self.client.force_login(who.user)
        r = self.client.post(reverse("official_requests:request_action", args=[req.pk]),
                             {"action": action, "notes": notes})
        req.refresh_from_db()
        return r

    def _to_capitol(self):
        req = self._file()
        self._act(self.supervisor, req, "endorse")
        self._act(self.hr, req, "process")
        self._act(self.ao, req, "recommend")
        self._act(self.coh, req, "forward")
        return req

    def test_travel_order_fields_are_required(self):
        self.client.force_login(self.employee.user)
        r = self.client.post(reverse("official_requests:request_apply"),
                             dict(OB_POST, expenses_authorized="", transportation_allowed="", destination=""))
        self.assertFalse(OfficialRequest.objects.exists())
        self.assertContains(r, "Choose Per Diem or Official Time")

    def test_coh_recommends_and_hr_records_the_governors_approval(self):
        req = self._to_capitol()
        self.assertEqual(req.status, OfficialRequest.FORWARDED_TO_CAPITOL)
        self.assertEqual(self._act(self.coh, req, "approve").status_code, 403)   # the COH does not approve
        self.assertEqual(self._act(self.hr, req, "return", "x").status_code, 302)  # no return at the Capitol
        self.assertEqual(req.status, OfficialRequest.FORWARDED_TO_CAPITOL)
        self._act(self.hr, req, "record_approval")
        self.assertEqual(req.status, OfficialRequest.APPROVED)
        self.assertEqual(req.status_label, "Approved by the Governor")

    def test_hr_records_the_governors_disapproval_with_a_remark(self):
        req = self._to_capitol()
        self._act(self.hr, req, "reject", "Disapproved: no funds")
        self.assertEqual(req.status, OfficialRequest.REJECTED)
        self.assertEqual(req.status_label, "Disapproved by the Governor")

    def test_travel_order_prints_only_after_the_coh_recommends(self):
        from unittest.mock import patch

        req = self._file()
        self.client.force_login(self.employee.user)
        url = reverse("official_requests:print_travel_order", args=[req.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self._act(self.supervisor, req, "endorse")
        self._act(self.hr, req, "process")
        self._act(self.ao, req, "recommend")
        self._act(self.coh, req, "forward")
        seen = {}

        def fake(document, label):
            seen["text"] = "\n".join(p.text for p in document.paragraphs) + "\n".join(
                c.text for t in document.tables for row in t.rows for c in row.cells)
            return b"%PDF-1.4"
        self.client.force_login(self.employee.user)
        with patch("official_requests.travel_order.docx_to_pdf", side_effect=fake):
            r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("RECOMMENDING APPROVAL", seen["text"])
        self.assertIn("Certified as to Availability of Funds", seen["text"])   # per diem
        self.assertIn("Puerto Princesa City", seen["text"])

    def test_no_budget_officer_block_without_per_diem(self):
        from .travel_order import build_document

        data = dict(people=[("JUAN A. CRUZ", "NURSE II")], period="July 01, 2026", destination="X", purpose="Y",
                    transport="Government Vehicle", expenses=OfficialRequest.EXPENSES_OFFICIAL_TIME, coh_own=True,
                    date=date(2026, 6, 20))
        doc = build_document(data)
        text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
            c.text for t in doc.tables for row in t.rows for c in row.cells)
        self.assertNotIn("Certified as to Availability of Funds", text)
        self.assertNotIn("RECOMMENDING APPROVAL", text)   # the COH's own Travel Order
        self.assertIn("APPROVED BY", text)
        self.assertIn("Official Time", text)

    def test_hr_cannot_skip_ahead_of_supervisor(self):
        req = self._file()
        self.assertEqual(self._act(self.hr, req, "process").status_code, 403)

    def test_wrong_action_name_is_refused(self):
        req = self._file()
        self.assertEqual(self._act(self.supervisor, req, "approve").status_code, 403)
        self.assertEqual(req.status, OfficialRequest.SUBMITTED)


class LocalErrandRoutingTests(TestCase):
    """Local Official Errand: Supervisor endorses, the AO approves (F59)."""

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
            "start_date": "2026-07-10", "end_date": "2026-07-10", "time_from": "09:00", "time_to": "11:00",
            "destination": "Municipal Hall", "purpose": "Submit permit documents",
        })
        self.req = OfficialRequest.objects.get()

    def test_supervisor_endorses_then_ao_approves(self):
        self.client.force_login(self.supervisor.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "endorse"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, OfficialRequest.ENDORSED_BY_SUPERVISOR)
        for who in (self.hr, self.coh):
            self.client.force_login(who.user)
            r = self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "approve"})
            self.assertEqual(r.status_code, 403)
        self.client.force_login(self.ao.user)
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
            "destination": "Puerto Princesa City", "expenses_authorized": OfficialRequest.PER_DIEM,
            "transportation_allowed": "Government Vehicle",
        })
        self.req = OfficialRequest.objects.get()

    def test_supervisor_can_reject(self):
        self.client.force_login(self.supervisor.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": "reject", "notes": "Not needed"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, OfficialRequest.REJECTED)

    def test_unrelated_employee_cannot_reject(self):
        outsider = make_employee("rejout1", "EMP-REJOUT1")
        self.client.force_login(outsider.user)
        response = self.client.post(
            reverse("official_requests:request_action", args=[self.req.pk]), {"action": "reject", "notes": "Not needed"}
        )
        self.assertEqual(response.status_code, 403)
