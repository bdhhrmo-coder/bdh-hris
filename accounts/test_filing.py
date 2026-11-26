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


class EveryPageShowsIt(TestCase):
    """Audit fix F5: each request type's list, detail and approval queue carries the filing time.
    (Exchange of Duty and single Official Business/Errand requests have no detail page of their own.)"""

    PAGES = {
        "Leave": ["leave/my_applications.html", "leave/leave_detail.html", "leave/leave_queue.html"],
        "CTO application and claim": ["cto/my_cto.html", "cto/cto_detail.html", "cto/claim_detail.html",
                                      "cto/cto_queue.html"],
        "Exchange of Duty": ["exchange/my_exchanges.html", "exchange/exchange_queue.html"],
        "Attendance correction": ["attendance/my_attendance.html", "attendance/correction_detail.html",
                                  "attendance/correction_queue.html"],
        "Official Business / Errand / OT": ["official_requests/my_requests.html",
                                            "official_requests/batch_detail.html",
                                            "official_requests/request_queue.html"],
        "Date change": ["datechange/change_detail.html", "datechange/_changes_card.html",
                        "datechange/_queue_card.html"],
        "Out-on-Pass": ["outonpass/mine.html", "outonpass/detail.html", "outonpass/queue.html"],
    }

    def test_templates_use_the_filed_tag(self):
        from django.template.loader import get_template

        for kind, names in self.PAGES.items():
            for name in names:
                source = get_template(name).template.source
                self.assertIn("{% filed", source, f"{kind}: {name}")


from schedules.tests import Base as ScheduleBase  # noqa: E402


class ScheduleSubmittedTests(ScheduleBase):
    """Duty schedules show "Submitted: ..." or "Not yet submitted" (owner checklist B2)."""

    def show(self, s):
        return Template("{% load ui %}{% schedule_submitted s %}").render(Context({"s": s}))

    def test_draft_then_submitted(self):
        from schedules import services

        s = self.new_schedule()
        self.assertIn("Not yet submitted", self.show(s))
        services.submit(s, self.sup.user)
        self.assertIn("Submitted: ", self.show(s))
        self.assertNotIn("Not yet submitted", self.show(s))
        self.client.force_login(self.sup.user)
        self.assertContains(self.client.get(reverse("schedules:detail", args=[s.pk])), "Submitted: ")
        self.assertContains(self.client.get(reverse("schedules:list")), "Submitted: ")
