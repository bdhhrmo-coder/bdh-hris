"""F59 printout for a Local Official Errand (owner request 2026-11-03)."""

from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment

from .models import OfficialRequest
from .tests import make_employee


def _text(document):
    return "\n".join(p.text for p in document.paragraphs) + "\n" + "\n".join(
        c.text for t in document.tables for row in t.rows for c in row.cells)


class F59Tests(TestCase):
    def setUp(self):
        self.employee = make_employee("f59emp", "EMP-F59")
        self.employee.position = "Administrative Aide IV"
        self.employee.save()
        self.supervisor = make_employee("f59sup", "EMP-F59S", role=RoleAssignment.SUPERVISOR)
        self.ao = make_employee("f59ao", "EMP-F59A", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.client = Client()
        self.client.force_login(self.employee.user)
        self.client.post(reverse("official_requests:request_apply"), {
            "request_type": OfficialRequest.TRAVEL, "start_date": "2026-11-05", "end_date": "2026-11-05",
            "time_from": "09:00", "time_to": "11:30", "destination": "Municipal Hall, Bataraza",
            "vehicle": "Service Vehicle", "purpose": "Submit payroll documents"})
        self.req = OfficialRequest.objects.get()
        self.url = reverse("official_requests:print_f59", args=[self.req.pk])

    def act(self, who, action):
        self.client.force_login(who.user)
        self.client.post(reverse("official_requests:request_action", args=[self.req.pk]), {"action": action})
        self.req.refresh_from_db()

    def test_vehicle_is_kept_for_the_errand(self):
        self.assertEqual(self.req.transportation_allowed, "Service Vehicle")

    def test_prints_only_once_approved_and_is_filled_from_the_request(self):
        self.client.force_login(self.employee.user)
        self.assertEqual(self.client.get(self.url).status_code, 302)   # not approved yet
        self.act(self.supervisor, "endorse")
        self.act(self.ao, "approve")
        self.assertEqual(self.req.status, OfficialRequest.APPROVED)
        seen = {}

        def fake(document, label):
            seen["doc"] = document
            return b"%PDF-1.4"
        self.client.force_login(self.employee.user)
        with patch("official_requests.f59.docx_to_pdf", side_effect=fake):
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        text = _text(seen["doc"])
        for expected in ("BDH-ADM-AO-01F59", "Municipal Hall, Bataraza", "9:00 AM", "11:30 AM",
                         "Submit payroll documents", "Administrative Aide IV", "Nov 5, 2026",
                         "DIGITALLY FILED", "DIGITALLY NOTED", "DIGITALLY APPROVED",
                         self.supervisor.full_name.upper(), self.ao.full_name.upper()):
            self.assertIn(expected, text)
        xml = seen["doc"].element.xml
        self.assertEqual(xml.count("\u2612"), 2)    # Service Vehicle ticked on both copies
        self.assertEqual(xml.count("\u2610"), 2)    # Ambulance left blank
        self.assertEqual(len(seen["doc"].tables), 2)     # both copies kept

    def test_other_types_do_not_print_f59(self):
        self.req.request_type = OfficialRequest.OFFICIAL_BUSINESS
        self.req.save()
        self.client.force_login(self.employee.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
