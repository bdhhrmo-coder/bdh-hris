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


class RecallAfterCutoffAndLocks(Base):
    """Audit fix F4 (owner checklist B1)."""

    def test_resubmission_after_cutoff_is_shown_to_hr(self):
        from datetime import timedelta

        from django.utils import timezone

        s = self.new_schedule()
        services.submit(s, self.sup.user)
        S.objects.filter(pk=s.pk).update(cutoff_date=timezone.localdate() - timedelta(days=1))
        s.refresh_from_db()
        services.recall(s, self.sup.user, "Late correction")
        services.submit(s, self.sup.user)
        last = s.actions.order_by("-pk").first()
        self.assertEqual(last.action, "resubmit")
        self.assertTrue(last.notes.startswith("Resubmitted after cut-off ("), last.notes)
        self.assertTrue(Notification.objects.filter(recipient=self.hr, message__contains="Resubmitted after cut-off")
                        .exists())

    def test_resubmission_before_cutoff_has_no_note(self):
        from datetime import timedelta

        from django.utils import timezone

        s = self.new_schedule()
        services.submit(s, self.sup.user)
        S.objects.filter(pk=s.pk).update(cutoff_date=timezone.localdate() + timedelta(days=3))
        s.refresh_from_db()
        services.recall(s, self.sup.user, "Fix")
        services.submit(s, self.sup.user)
        self.assertEqual(s.actions.order_by("-pk").first().notes, "")

    def test_exchange_locked_days_stay_locked_after_a_recall(self):
        from exchange.models import DutyExchangeRequest as X
        from exchange.regular import apply_to_drafts, locked_cells

        s = self.new_schedule()
        d10, d11 = date(YEAR, MONTH, 10), date(YEAR, MONTH, 11)
        x = X.objects.create(employee_a=self.alice, date_a=d10, employee_b=self.bob, date_b=d11,
                             kind=X.KIND_REGULAR, shift_a=code("7A-7P"), shift_b=code("7P-7A"), status=X.APPROVED)
        apply_to_drafts(x, self.sup.user)
        locked = dict(locked_cells(s))
        self.assertEqual(len(locked), 4)
        services.submit(s, self.sup.user)
        services.recall(s, self.sup.user, "Wrong shift")
        s.refresh_from_db()
        self.assertEqual(dict(locked_cells(s)), locked)
        row = s.rows.get(employee=self.alice)
        services.save_grid(s, {(row.pk, d10): code("8-5").pk})  # try to change a locked day
        self.assertEqual(row.cells.get(date=d10).shift.code, "OFF")
