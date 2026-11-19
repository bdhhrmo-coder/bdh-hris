"""CTO covering part of a COSP leave (Part 3, owner decisions 2026-11-19)."""

from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from cto.balances import compute_available_cto_balance
from cto.models import CTOClaimLine, CTOUsageApplication as C
from cto.tests import Org, apply_cto, make_employee, on, opening_credit
from datechange.models import DateChangeRequest as DC
from datechange.rules import next_step

from .balances import compute_available_balance
from .models import LeaveApplication as L, LeaveCreditTransaction, LeaveType

TODAY = date(2026, 11, 19)


class Base(TestCase):
    def setUp(self):
        self.org = Org()
        self.client = Client()
        self.emp = make_employee("cospcto", "E-CC", section=self.org.lab)
        self.emp.employment_status = "COSP"
        self.emp.date_hired = date(2025, 3, 1)
        self.emp.save()
        self.cosp_leave = LeaveType.objects.get(code="COSP_LEAVE")

    def credit(self, days="3"):
        return opening_credit(self.org, self.emp, days=days, earned=date(2026, 6, 1), entered=date(2026, 11, 10))

    def apply(self, start, end, days, cto=None, emp=None, leave_type=None):
        """cto = (days, from, to) or None."""
        emp = emp or self.emp
        data = {"leave_type": (leave_type or self.cosp_leave).pk, "start_date": start, "end_date": end,
                "number_of_days": days, "justification": "", "location": "PH"}
        if cto:
            data.update(use_cto="on", cto_days=cto[0], cto_start_date=cto[1], cto_end_date=cto[2])
        self.client.force_login(emp.user)
        with on(TODAY):
            return self.client.post(reverse("leave:leave_apply"), data)

    def errors(self, r):
        if r.status_code == 302:
            return ""
        f = r.context["form"]
        return " ".join(str(e) for errs in f.errors.values() for e in errs)

    def act(self, app, who, action, notes="", today=TODAY):
        self.client.force_login(who.user)
        with on(today):
            r = self.client.post(reverse("leave:leave_action", args=[app.pk]), {"action": action, "notes": notes})
        app.refresh_from_db()
        return r

    def approve(self, app, today=TODAY):
        o = self.org
        for who, action in ((o.lab_sup, "endorse"), (o.hr, "process"), (o.ao, "recommend"), (o.coh, "approve")):
            self.act(app, who, action, today=today)

    def cto_balance(self):
        return compute_available_cto_balance(self.emp, TODAY)

    def leave_balance(self):
        return compute_available_balance(self.emp, self.cosp_leave, date(2026, 12, 31))


class SplitTests(Base):
    def test_split_days_cto_for_some_leave_credits_for_the_rest(self):
        self.credit("3")
        before_leave = self.leave_balance()
        r = self.apply("2026-12-01", "2026-12-03", "3", cto=("1", "2026-12-02", "2026-12-02"))
        self.assertEqual(r.status_code, 302, self.errors(r))
        app = L.objects.get()
        cover = app.cto_cover
        self.assertEqual((cover.status, cover.number_of_days, cover.start_date), (C.SUBMITTED, 1, date(2026, 12, 2)))
        self.assertEqual(self.cto_balance(), Decimal("2"))  # 1 day held at filing
        self.assertIn("1 day(s) charged to CTO (Dec 02, 2026); 2 day(s) to leave credits", app.actions.first().notes)
        self.approve(app)
        self.assertEqual(app.status, L.APPROVED)
        cover.refresh_from_db()
        self.assertEqual(cover.status, C.APPROVED)
        self.assertEqual(LeaveCreditTransaction.objects.get(leave_application=app).days, Decimal("-2"))
        self.assertEqual(self.leave_balance(), before_leave - 2)
        self.assertEqual(self.cto_balance(), Decimal("2"))

    def test_all_days_on_cto_takes_no_leave_credits(self):
        self.credit("3")
        before_leave = self.leave_balance()
        self.apply("2026-12-01", "2026-12-02", "2", cto=("2", "2026-12-01", "2026-12-02"))
        app = L.objects.get()
        self.approve(app)
        self.assertEqual(app.status, L.APPROVED)
        self.assertFalse(LeaveCreditTransaction.objects.filter(leave_application=app).exists())
        self.assertEqual(self.leave_balance(), before_leave)
        self.assertEqual(self.cto_balance(), Decimal("1"))

    def test_default_is_leave_credits_only(self):
        self.credit("3")
        self.apply("2026-12-01", "2026-12-01", "1")
        app = L.objects.get()
        self.assertIsNone(app.cto_days_charged)
        self.assertEqual(self.cto_balance(), Decimal("3"))

    def test_cto_balance_too_low(self):
        self.credit("1")
        r = self.apply("2026-12-01", "2026-12-02", "2", cto=("2", "2026-12-01", "2026-12-02"))
        self.assertIn("Not enough CTO credit", self.errors(r))
        self.assertFalse(L.objects.exists())
        self.assertFalse(C.objects.exists())

    def test_cto_dates_must_be_inside_the_leave(self):
        self.credit("3")
        r = self.apply("2026-12-01", "2026-12-02", "2", cto=("1", "2026-12-03", "2026-12-03"))
        self.assertIn("inside the leave dates", self.errors(r))

    def test_cto_monthly_cap(self):
        self.credit("10")
        for start, end, days in (("2026-12-07", "2026-12-08", "2"), ("2026-12-10", "2026-12-11", "2"),
                                 ("2026-12-14", "2026-12-14", "1")):
            apply_cto(self.emp, start, end, days, TODAY)
        self.assertEqual(C.objects.count(), 3)
        r = self.apply("2026-12-01", "2026-12-02", "2", cto=("1", "2026-12-01", "2026-12-01"))
        self.assertIn("5-CTO-day-per-month", self.errors(r))
        # The same days on leave credits only are fine: CTO limits do not apply to them.
        r = self.apply("2026-12-01", "2026-12-02", "2")
        self.assertEqual(r.status_code, 302)

    def test_cto_no_three_consecutive_days(self):
        self.credit("5")
        apply_cto(self.emp, "2026-12-03", "2026-12-04", "2", TODAY)
        r = self.apply("2026-12-01", "2026-12-02", "2", cto=("1", "2026-12-02", "2026-12-02"))
        self.assertIn("3 or more consecutive days", self.errors(r))

    def test_expired_credit_cannot_cover(self):
        self.credit("3")  # usable up to Dec 15, 2026
        r = self.apply("2026-12-16", "2026-12-17", "2", cto=("1", "2026-12-16", "2026-12-16"))
        self.assertIn("Not enough CTO credit", self.errors(r))

    def test_credit_gone_at_approval_returns_the_leave(self):
        line = self.credit("3")
        self.apply("2026-12-01", "2026-12-02", "2", cto=("1", "2026-12-01", "2026-12-01"))
        app = L.objects.get()
        o = self.org
        for who, action in ((o.lab_sup, "endorse"), (o.hr, "process"), (o.ao, "recommend")):
            self.act(app, who, action)
        CTOClaimLine.objects.filter(pk=line.pk).update(state=CTOClaimLine.FORFEITED)
        self.act(app, o.coh, "approve")
        self.assertEqual(app.status, L.RETURNED)
        self.assertIn("Returned automatically at approval", app.actions.last().notes)
        self.assertFalse(LeaveCreditTransaction.objects.filter(leave_application=app).exists())
        self.assertEqual(app.cto_cover.status, C.RETURNED)
        self.assertFalse(app.cto_cover.allocations.exists())


class ReleaseTests(Base):
    def test_cancel_releases_the_cto_days(self):
        self.credit("3")
        self.apply("2026-12-01", "2026-12-02", "2", cto=("2", "2026-12-01", "2026-12-02"))
        app = L.objects.get()
        self.assertEqual(self.cto_balance(), Decimal("1"))
        self.client.force_login(self.emp.user)
        self.client.post(reverse("leave:leave_cancel", args=[app.pk]))
        app.refresh_from_db()
        self.assertEqual((app.status, app.cto_cover.status), (L.CANCELLED, C.CANCELLED))
        self.assertEqual(self.cto_balance(), Decimal("3"))

    def test_rejection_releases_the_cto_days(self):
        self.credit("3")
        self.apply("2026-12-01", "2026-12-01", "1", cto=("1", "2026-12-01", "2026-12-01"))
        app = L.objects.get()
        self.act(app, self.org.lab_sup, "reject", notes="Short-staffed")
        self.assertEqual(app.status, L.REJECTED)
        self.assertEqual(self.cto_balance(), Decimal("3"))

    def test_resubmit_after_return_holds_again_and_can_drop_cto(self):
        self.credit("3")
        self.apply("2026-12-01", "2026-12-01", "1", cto=("1", "2026-12-01", "2026-12-01"))
        app = L.objects.get()
        self.act(app, self.org.lab_sup, "return", notes="Fix it")
        self.assertEqual(self.cto_balance(), Decimal("3"))
        self.client.force_login(self.emp.user)
        with on(TODAY):
            self.client.post(reverse("leave:leave_edit", args=[app.pk]), {
                "leave_type": self.cosp_leave.pk, "start_date": "2026-12-01", "end_date": "2026-12-01",
                "number_of_days": "1", "justification": "", "use_cto": "on", "cto_days": "1",
                "cto_start_date": "2026-12-01", "cto_end_date": "2026-12-01"})
        app.refresh_from_db()
        self.assertEqual((app.status, app.cto_cover.status), (L.SUBMITTED, C.SUBMITTED))
        self.assertEqual(self.cto_balance(), Decimal("2"))
        with on(TODAY):  # edited again before anyone acts: back to leave credits only
            self.client.post(reverse("leave:leave_edit", args=[app.pk]), {
                "leave_type": self.cosp_leave.pk, "start_date": "2026-12-01", "end_date": "2026-12-01",
                "number_of_days": "1", "justification": ""})
        self.assertFalse(C.objects.exists())
        self.assertEqual(self.cto_balance(), Decimal("3"))


class DateChangeKeepsSplitTests(Base):
    def setUp(self):
        super().setUp()
        self.credit("3")
        self.apply("2026-12-01", "2026-12-03", "3", cto=("1", "2026-12-02", "2026-12-02"))
        self.app = L.objects.get()
        self.approve(self.app)

    def file(self, start, end):
        self.client.force_login(self.emp.user)
        with on(TODAY):
            return self.client.post(reverse("datechange:new", args=["leave", self.app.pk]),
                                    {"new_start_date": start, "new_end_date": end, "justification": "Moved"})

    def test_date_change_moves_the_cto_days_with_the_leave(self):
        held = list(self.app.cto_cover.allocations.values_list("line_id", "days"))
        before = (self.cto_balance(), self.leave_balance())
        self.file("2026-12-08", "2026-12-10")
        change = DC.objects.get()
        people = [self.org.lab_sup, self.org.hr, self.org.ao, self.org.coh]
        while change.is_open:
            who = next(p for p in people if next_step(change, p))
            self.client.force_login(who.user)
            with on(TODAY):
                self.client.post(reverse("datechange:action", args=[change.pk]),
                                 {"action": next_step(change, who)[0]})
            change.refresh_from_db()
        self.assertEqual(change.status, DC.APPLIED)
        cover = C.objects.get(cosp_leave=self.app)
        self.assertEqual((cover.start_date, cover.end_date), (date(2026, 12, 9), date(2026, 12, 9)))
        self.assertEqual(list(cover.allocations.values_list("line_id", "days")), held)
        self.assertEqual((self.cto_balance(), self.leave_balance()), before)

    def test_new_dates_are_checked_against_the_cto_rules(self):
        r = self.file("2026-12-15", "2026-12-17")  # CTO day would be Dec 16, past the credit's deadline
        self.assertIn("must be used by December 15, 2026", " ".join(
            str(e) for e in r.context["form"].non_field_errors()))
        self.assertFalse(DC.objects.exists())

    def test_cto_days_of_a_leave_cannot_be_changed_or_cancelled_on_their_own(self):
        cover = self.app.cto_cover
        self.client.force_login(self.emp.user)
        with on(TODAY):
            self.client.post(reverse("datechange:new", args=["cto", cover.pk]),
                             {"new_start_date": "2026-12-09", "new_end_date": "2026-12-09", "justification": "x"})
        self.assertFalse(DC.objects.exists())
        self.assertRedirects(self.client.get(reverse("cto:cto_detail", args=[cover.pk])),
                             reverse("leave:leave_detail", args=[self.app.pk]), fetch_redirect_response=False)


class VisibilityTests(Base):
    def test_regular_employees_do_not_see_or_use_the_option(self):
        reg = self.org.lab_staff
        opening_credit(self.org, reg, days="3", earned=date(2026, 6, 1), entered=date(2026, 11, 10))
        self.client.force_login(reg.user)
        page = self.client.get(reverse("leave:leave_apply"))
        self.assertNotContains(page, 'name="use_cto"')
        vl = LeaveType.objects.get(code="VL")
        self.apply("2026-12-01", "2026-12-01", "1", cto=("1", "2026-12-01", "2026-12-01"), emp=reg, leave_type=vl)
        self.assertEqual(L.objects.filter(employee=reg).count(), 1)
        self.assertFalse(C.objects.exists())

    def test_cosp_employee_sees_the_option(self):
        self.client.force_login(self.emp.user)
        self.assertContains(self.client.get(reverse("leave:leave_apply")), 'name="use_cto"')

    def test_cto_queue_and_counts_leave_out_the_cto_days_of_a_leave(self):
        self.credit("3")
        self.apply("2026-12-01", "2026-12-01", "1", cto=("1", "2026-12-01", "2026-12-01"))
        self.org.lab_sup.refresh_from_db()
        from cto.permissions import visible_cto_applications_for
        from homepage.kpis import waiting_for_me

        self.assertFalse(visible_cto_applications_for(self.org.lab_sup).exists())
        counts = dict((label, n) for label, n, _ in waiting_for_me(self.org.lab_sup, self.org.lab_sup.user))
        self.assertEqual(counts.get("Leave"), 1)
        self.assertNotIn("CTO", counts)
        self.client.force_login(self.org.lab_sup.user)
        self.assertContains(self.client.get(reverse("leave:leave_queue")), "charged to CTO")
