"""Pilot Item 2 (rebuilt 2026-11-26): the date and time a request was filed,
and a resubmission beside it. Made-up data only."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse

from leave.models import LeaveApplication, LeaveApplicationAction, LeaveType
from leave.tests import make_employee

MNL = ZoneInfo("Asia/Manila")


class FilingTimeTests(TestCase):
    def setUp(self):
        self.emp = make_employee("filer1", "F-1", date_hired=date(2024, 1, 1))
        self.a = LeaveApplication.objects.create(employee=self.emp, leave_type=LeaveType.objects.get(code="VL"),
                                                 start_date=date(2026, 12, 2), end_date=date(2026, 12, 2),
                                                 number_of_days=Decimal("1"))
        LeaveApplication.objects.filter(pk=self.a.pk).update(submitted_at=datetime(2026, 11, 20, 14, 5, tzinfo=MNL))
        self.a.refresh_from_db()

    def render(self, short=False):
        return Template("{% load ui %}{% filed a" + (" short=1" if short else "") + " %}").render(Context({"a": self.a}))

    def test_first_filing_in_philippine_time(self):
        self.assertIn("Nov 20, 2026 2:05 PM", self.render())
        self.assertNotIn("Resubmitted", self.render())

    def test_resubmission_shown_beside_the_first_filing(self):
        act = LeaveApplicationAction.objects.create(application=self.a, action="resubmit", resulting_status="SUBMITTED", acted_by=self.emp.user)
        LeaveApplicationAction.objects.filter(pk=act.pk).update(acted_at=datetime(2026, 11, 22, 9, 30, tzinfo=MNL))
        long, short = self.render(), self.render(short=True)
        self.assertIn("Nov 20, 2026 2:05 PM", long)
        self.assertIn("Resubmitted Nov 22, 2026 9:30 AM", long)
        self.assertIn("resubmitted Nov 22, 2026 9:30 AM", short)

    def test_pages_show_it(self):
        self.client.force_login(self.emp.user)
        self.assertContains(self.client.get(reverse("leave:my_applications")), "Nov 20, 2026 2:05 PM")
        self.assertContains(self.client.get(reverse("leave:leave_detail", args=[self.a.pk])), "Date and time filed")
