"""Pilot Item 1: recall a submitted schedule; HR corrects a range of days."""

from datetime import date

from django.urls import reverse

from notifications.models import Notification

from . import rules, services
from .models import ScheduleChange
from .tests import MONTH, YEAR, Base, S, code, set_days


class RecallTests(Base):
    def submitted(self):
        s = self.new_schedule()
        services.submit(s, self.sup.user)
        return s

    def recall(self, who, s, reason):
        self.client.force_login(who.user)
        return self.client.post(reverse("schedules:recall", args=[s.pk]), {"reason": reason})

    def test_button_only_for_preparer_until_coh_approves(self):
        s = self.submitted()
        self.client.force_login(self.sup.user)
        self.assertContains(self.client.get(reverse("schedules:detail", args=[s.pk])), "Recall and edit")
        for other in (self.hr, self.ao, self.coh):
            self.assertFalse(rules.can_recall(other.user, s))
            self.client.force_login(other.user)
            self.assertNotContains(self.client.get(reverse("schedules:detail", args=[s.pk])), "Recall and edit")
        self.route_to(s, S.APPROVED)
        self.assertFalse(rules.can_recall(self.sup.user, s))
        self.recall(self.sup, s, "late fix")
        s.refresh_from_db()
        self.assertEqual(s.status, S.APPROVED)

    def test_reason_required(self):
        s = self.submitted()
        self.recall(self.sup, s, "  ")
        s.refresh_from_db()
        self.assertEqual(s.status, S.SUBMITTED)

    def test_recall_returns_notifies_and_resubmit_goes_to_hr(self):
        s = self.submitted()
        services.act(s, self.hr.user, "review")  # now at the AO step
        r = self.recall(self.sup, s, "Wrong shift for Bob")
        self.assertRedirects(r, reverse("schedules:edit", args=[s.pk]), fetch_redirect_response=False)
        s.refresh_from_db()
        self.assertEqual(s.status, S.RETURNED)
        self.assertTrue(s.is_editable)
        self.assertEqual(s.actions.last().action, "recall")
        for person in (self.hr, self.hr2, self.ao):
            self.assertTrue(Notification.objects.filter(recipient=person, message__contains="Wrong shift").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.coh, message__contains="Wrong shift").exists())
        services.submit(s, self.sup.user)
        s.refresh_from_db()
        self.assertEqual(s.status, S.SUBMITTED)
        self.assertEqual(rules.STEPS[s.status][3], "HR")


class RangeCorrectionTests(Base):
    def test_range_skips_same_shift_and_notifies_once(self):
        s = self.new_schedule()
        set_days(s, self.alice, {10: "7A-7P", 11: "OFF", 12: "7A-7P", 13: "7A-7P"})
        self.route_to(s, S.RECORDED)
        before = Notification.objects.filter(recipient=self.alice).count()
        row = s.rows.get(employee=self.alice)
        self.client.force_login(self.hr.user)
        self.client.post(reverse("schedules:correct", args=[s.pk]),
                         {"row": row.pk, "date": "2027-03-10", "date_to": "2027-03-13",
                          "shift": code("OFF").pk, "reason": "Sick leave"})
        days = sorted(c.date.day for c in ScheduleChange.objects.filter(schedule=s))
        self.assertEqual(days, [10, 12, 13])  # the 11th was already OFF
        self.assertEqual(Notification.objects.filter(recipient=self.alice).count(), before + 1)
        self.client.force_login(self.hr.user)
        page = self.client.get(reverse("schedules:detail", args=[s.pk]))
        self.assertContains(page, "Changes after approval")

    def test_range_errors(self):
        s = self.new_schedule()
        self.route_to(s, S.APPROVED)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            services.hr_correction(s, self.alice, date(YEAR, MONTH, 13), code("OFF"), "x", self.hr.user,
                                   day_to=date(YEAR, MONTH, 10))
        with self.assertRaises(ValidationError):
            services.hr_correction(s, self.alice, date(YEAR, MONTH, 30), code("OFF"), "x", self.hr.user,
                                   day_to=date(YEAR, MONTH + 1, 2))
        with self.assertRaises(ValidationError):  # HR never corrects their own duty
            services.hr_correction(s, self.hr, date(YEAR, MONTH, 3), code("OFF"), "x", self.hr.user)
