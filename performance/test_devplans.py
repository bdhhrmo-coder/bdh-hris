"""PMS steps 6-7 (rebuilt 2026-11-26): development plans, the D11 automatic missed
check-in, monitoring, reminders, reports, the menu and the home card. All data made up."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment as R
from notifications.models import Notification

from . import devplans as dp, monitoring as mon, raters
from .models import (Deduction, DevCheckin as K, DevPlan, EvaluationPeriod as P, RaterAssignment as A,
                     RatingWindow)
from .test_raters import Base, person

TODAY = timezone.localdate()


def goal(i, **kw):
    row = {"commitment": f"Give complete endorsements at every shift change ({i}).", "intervention_type": "COACHING",
           "intervention": "", "training_request_ref": "", "responsible": "EMPLOYEE",
           "target_date": (TODAY + timedelta(days=60)).isoformat()}
    row.update(kw)
    return row


class PlanBase(Base):
    def setUp(self):
        super().setUp()
        self.p = self.period(self.n[0], status=P.RELEASED)
        self.p.released_at, self.p.conference_on, self.p.conference_by = timezone.now(), TODAY, self.erh
        self.p.save()
        self.dates = [(TODAY + timedelta(days=30)).isoformat(), (TODAY + timedelta(days=50)).isoformat()]

    def send(self, who=None, goals=None, dates=None):
        who = who or self.erh
        return dp.save_draft(who, who.user, self.p, {"strengths": "Calm", "growth_areas": "Handover"},
                             goals or [goal(1), goal(2)], self.dates if dates is None else dates, send=True)

    def agreed(self):
        plan = self.send()
        dp.agree(self.n[0], self.n[0].user, plan)
        plan.refresh_from_db()
        return plan


class FlowTests(PlanBase):
    def test_draft_send_change_agree(self):
        draft = dp.save_draft(self.erh, self.erh.user, self.p, {}, [goal(1)], [], send=False)
        self.assertFalse(dp.can_view(self.n[0], self.p, draft))  # the employee sees it once sent
        with self.assertRaises(ValidationError):
            self.send(goals=[goal(1)])  # two commitments
        with self.assertRaises(ValidationError):
            self.send(dates=[])
        plan = self.send()
        self.assertEqual(K.objects.filter(goal__plan=plan).count(), 4)
        with self.assertRaises(ValidationError):
            dp.ask_changes(self.n[0], self.n[0].user, plan, "")
        dp.ask_changes(self.n[0], self.n[0].user, plan, "Target 2 is too soon.")
        plan.refresh_from_db()
        self.assertIsNone(plan.sent_at)
        plan = self.send()
        dp.agree(self.n[0], self.n[0].user, plan)
        plan.refresh_from_db()
        self.assertEqual(plan.status, DevPlan.AGREED)

    def test_who_prepares_and_sees(self):
        for who in (self.n[0], self.cn, self.hra, self.n[1]):
            with self.assertRaises(ValidationError):
                self.send(who=who)
        plan = self.send()
        self.assertTrue(dp.can_view(self.hra, self.p, plan))
        self.assertFalse(dp.can_edit(self.hra, plan))
        self.assertFalse(dp.can_view(self.n[1], self.p, plan))
        self.assertFalse(dp.can_view(self.ao, self.p, plan))  # the AO and COH have no access

    def test_supervisor_comes_from_the_shared_lookup(self):
        with mock.patch("performance.raters.immediate_supervisors", return_value=[self.cn]):
            self.assertTrue(dp.is_supervisor(self.cn, self.p))
            self.send(who=self.cn)

    def test_checkins_and_close(self):
        plan = self.agreed()
        first = date.fromisoformat(self.dates[0])
        with self.assertRaises(ValidationError):
            dp.record_checkin(self.erh, self.erh.user, plan, first, False, None, {}, {})  # missed needs a reason
        dp.record_checkin(self.erh, self.erh.user, plan, first, True, TODAY, {1: "On track"}, {1: "No calls"})
        with self.assertRaises(ValidationError):
            dp.close(self.erh, self.erh.user, plan)
        dp.record_checkin(self.erh, self.erh.user, plan, date.fromisoformat(self.dates[1]), False, None,
                          {1: "On approved leave"}, {})
        dp.close(self.erh, self.erh.user, plan, "Met")
        plan.refresh_from_db()
        self.assertEqual(plan.status, DevPlan.CLOSED)
        self.assertFalse(Deduction.objects.exists())  # a check-in the supervisor recorded files nothing


class AutoMissedTests(PlanBase):
    def test_missed_after_five_working_days_files_x7_and_late_catch_up(self):
        plan = self.agreed()
        first = date.fromisoformat(self.dates[0])
        self.assertEqual(dp.auto_miss(first + timedelta(days=3)), 0)
        self.assertEqual(dp.auto_miss(first + timedelta(days=10)), 1)
        rows = K.objects.filter(goal__plan=plan, planned_on=first)
        self.assertTrue(all(r.status == K.MISSED and r.auto_missed_at for r in rows))
        d = Deduction.objects.get()
        self.assertEqual((d.employee, d.criterion.code, d.status), (self.erh, "X7", Deduction.FILED))
        self.assertIn("missed development-plan check-in", d.auto_source)
        self.assertEqual(dp.auto_miss(first + timedelta(days=11)), 0)  # once only
        dp.record_checkin(self.erh, self.erh.user, plan, first, True, TODAY, {}, {})
        self.assertTrue(all(r.status == K.HELD and r.late for r in K.objects.filter(goal__plan=plan, planned_on=first)))
        self.assertEqual(Deduction.objects.count(), 1)  # the incident stays and follows the normal route


class ReminderTests(Base):
    def test_rating_reminders_once_per_point(self):
        self.window.opens_on, self.window.closes_on = TODAY, TODAY + timedelta(days=20)
        self.window.save()
        p = self.period(self.n[0])
        raters.build_assignments(p, self.hra.user)
        Notification.objects.all().delete()
        told = lambda: Notification.objects.filter(recipient=self.erh, message__startswith="Reminder").count()  # noqa
        self.assertEqual(mon.send_reminders(TODAY + timedelta(days=9))[0], 0)
        mon.send_reminders(TODAY + timedelta(days=10))
        mon.send_reminders(TODAY + timedelta(days=10))
        self.assertEqual(told(), 1)
        mon.send_reminders(TODAY + timedelta(days=18))
        self.assertEqual(told(), 2)
        self.assertEqual(p.raters.get(group=A.SUPERVISOR).reminders_sent, "1,2,close")


class ReportTests(Base):
    def result(self, emp, total, status=P.FINAL):
        p = self.period(emp, status=status)
        p.tpe_total, p.released_at = Decimal(total), timezone.now()
        p.save()
        return p

    def test_summary_hides_small_groups_rr_feed_final_only(self):
        self.result(self.n[0], 82)
        self.result(self.n[1], 91)
        self.assertIsNone(mon.summary(self.window)["mean"])
        self.result(self.n[2], 85, status=P.RELEASED)
        s = mon.summary(self.window)
        self.assertEqual(s["mean"], Decimal("86.0"))
        self.assertNotIn(self.n[0].full_name, str(mon.summary_tables(s)))
        header, rows = mon.rr_feed(self.window)
        self.assertEqual(len(rows), 2)
        self.assertIn("PHRMO-certified rating", header)


class MenuAndHomeTests(PlanBase):
    def test_menu_groups_and_tabs(self):
        self.client.force_login(self.hra.user)
        r = self.client.get(reverse("performance:periods"))
        for text in (">Performance<", "HR Records", "HR Settings", "Performance Report", "Assignment History",
                     "Performance Reviews and Contests"):
            self.assertContains(r, text)
        self.assertNotContains(r, ">HR Administration<")

    def test_home_card_only_while_something_waits_and_never_for_ictu_or_sysadmin(self):
        self.client.force_login(self.n[0].user)
        self.assertNotContains(self.client.get("/hris/home/"), "Performance tasks")
        self.send()
        self.assertContains(self.client.get("/hris/home/"), "Development plan to answer")
        R.objects.create(employee=self.n[0], role=R.ICTU_STAFF)
        self.assertNotContains(self.client.get("/hris/home/"), "Performance tasks")
        admin = person("pmsysadm", R.SYSTEM_ADMINISTRATOR, status="REGULAR")
        p = self.period(admin, track=P.RANK)
        raters.build_assignments(p, self.hra.user)
        self.client.force_login(admin.user)
        self.assertNotContains(self.client.get("/hris/home/"), "Performance tasks")

    def test_pages_open(self):
        self.client.force_login(self.hra.user)
        for name in ("performance:monitoring", "performance:report", "performance:renewal", "performance:reviews",
                     "performance:plans", "performance:incidents", "performance:my_results", "performance:tasks",
                     "performance:deduction_criteria"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("performance:rr_feed") + f"?window={self.window.pk}").status_code,
                         200)
        for fmt in ("xlsx", "pdf"):
            self.assertEqual(self.client.get(reverse("performance:report") + f"?window={self.window.pk}&format={fmt}")
                             .status_code, 200)
        self.client.force_login(self.n[0].user)
        self.assertEqual(self.client.get(reverse("performance:monitoring")).status_code, 403)
        self.assertEqual(self.client.get(reverse("performance:report")).status_code, 403)
        self.client.force_login(self.erh.user)
        self.assertContains(self.client.get(reverse("performance:plan", args=[self.p.pk])), "not a PIP")
