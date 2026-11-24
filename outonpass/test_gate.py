"""Out-on-Pass stage 3: Tap Out / Tap In, charging, missed taps and the Gate page (CLAUDE.md 13B)."""

from datetime import date, datetime, time
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment as RA
from employees.models import Employee
from orgstructure.models import Section

from . import gate
from .models import MissedTap as M
from .models import OutOnPass as P

DAY = date(2027, 2, 3)


def at(h, m=0, s=0, d=DAY):
    return timezone.make_aware(datetime.combine(d, time(h, m, s)))


def emp(username, section=None, roles=(), status="REGULAR", under=""):
    u = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=u, employee_id=username.upper(), surname=username.title(), first_name="T",
                                employment_status=status, cosp_under=under)
    if section:
        e.sections.add(section)
    for r in roles:
        RA.objects.create(employee=e, role=r, section=section if r == RA.SUPERVISOR else None)
    return e


class GateTests(TestCase):
    def setUp(self):
        lab = Section.objects.get(name="Laboratory Section")
        gs = Section.objects.get(name="General Services Section")
        self.head = emp("labhead", lab, [RA.SUPERVISOR])
        self.staff = emp("staff", lab)
        self.guard = emp("guard", gs, status="COSP", under="PGSS")
        self.hr = emp("hr", roles=[RA.HR_PROCESSOR])
        self.p = self.make(self.staff)

    def make(self, e, kind=P.ORDINARY, out=time(10), back=time(11), status=P.APPROVED):
        return P.objects.create(employee=e, kind=kind, pass_date=DAY, time_out=out, time_in=back,
                                reason_type=P.REASON_GOVT_BANK, reason="Bank", status=status, filed_by=e.user)

    def post(self, who, url, data, now):
        self.client.force_login(who.user)
        with mock.patch("outonpass.gate.current_time", return_value=now):
            r = self.client.post(url, data)
        self.p.refresh_from_db()
        return r

    def tap(self, which, now):
        return self.post(self.staff, reverse("outonpass:tap", args=[self.p.pk]), {"which": which}, now)

    # -- taps and charging --
    def test_tap_window(self):
        self.tap("out", at(9, 40))
        self.assertIsNone(self.p.tapped_out_at)  # opens 15 minutes before the planned time out
        self.tap("out", at(9, 50))
        self.assertEqual(self.p.tapped_out_at, at(9, 50))

    def test_too_late_to_tap_out(self):
        self.tap("out", at(11, 1))
        self.assertIsNone(self.p.tapped_out_at)

    def test_tap_in_charges_actual_minutes(self):
        self.tap("out", at(10, 2, 41))
        self.tap("in", at(10, 49, 5))
        self.assertEqual((self.p.status, self.p.minutes_charged, self.p.excess_minutes, self.p.over_balance_minutes),
                         (P.COMPLETED, 47, 0, 0))

    def test_grace_and_excess(self):
        self.tap("out", at(10))
        self.tap("in", at(12, 5))
        self.assertEqual((self.p.minutes_charged, self.p.excess_minutes), (125, 0))
        p2 = self.make(self.staff)
        P.objects.filter(pk=p2.pk).update(pass_date=date(2027, 2, 4))
        self.p = P.objects.get(pk=p2.pk)
        self.tap("out", at(10, d=date(2027, 2, 4)))
        self.tap("in", at(12, 9, d=date(2027, 2, 4)))
        self.assertEqual((self.p.minutes_charged, self.p.excess_minutes), (129, 9))

    def test_over_balance_recorded(self):
        from .models import PassOpeningUsage
        PassOpeningUsage.objects.create(employee=self.staff, year=2027, minutes_used=700, reason="tracker",
                                        entered_by=self.hr.user)
        self.tap("out", at(10))
        self.tap("in", at(10, 50))
        self.assertEqual(self.p.over_balance_minutes, 30)  # 20 minutes left, 50 used

    def test_only_the_employee_taps(self):
        self.post(self.head, reverse("outonpass:tap", args=[self.p.pk]), {"which": "out"}, at(10))
        self.assertIsNone(self.p.tapped_out_at)

    def test_unapproved_pass_cannot_tap(self):
        P.objects.filter(pk=self.p.pk).update(status=P.ENDORSED)
        self.tap("out", at(10))
        self.assertIsNone(self.p.tapped_out_at)

    def test_cannot_cancel_after_tapping_out(self):
        self.tap("out", at(10))
        self.post(self.staff, reverse("outonpass:action", args=[self.p.pk]), {"action": "cancel"}, at(10, 5))
        self.assertEqual(self.p.status, P.APPROVED)

    # -- missed taps --
    def missed(self, which, when, now):
        return self.post(self.staff, reverse("outonpass:missed_new", args=[self.p.pk, which]),
                         {"claimed_at": f"{timezone.localtime(when):%Y-%m-%dT%H:%M}", "reason": "Forgot"}, now)

    def act(self, who, m, action, now, **extra):
        r = self.post(who, reverse("outonpass:missed_act", args=[m.pk]), {"action": action, **extra}, now)
        m.refresh_from_db()
        return r

    def test_missed_tap_in_full_chain_then_charged(self):
        self.tap("out", at(10))
        self.missed("in", at(10, 40), at(13))
        m = M.objects.get()
        self.assertEqual(self.act(self.guard, m, "verify", at(13)).status_code, 403)  # supervisor first
        self.act(self.head, m, "confirm", at(13, 5))
        self.assertEqual(m.status, M.CONFIRMED)
        # The guard corrects the time from the gate log: a remark is required.
        self.act(self.guard, m, "verify", at(13, 10), verified_at="2027-02-03T10:44")
        self.assertEqual(m.status, M.CONFIRMED)
        self.act(self.guard, m, "verify", at(13, 10), verified_at="2027-02-03T10:44", notes="Logbook says 10:44")
        self.assertEqual(m.status, M.VERIFIED)
        self.assertEqual(self.p.status, P.APPROVED)  # nothing charged before HRMO reviews
        self.act(self.hr, m, "review", at(14))
        self.assertEqual(m.status, M.REVIEWED)
        self.assertEqual((self.p.status, self.p.tapped_in_at, self.p.minutes_charged), (P.COMPLETED, at(10, 44), 44))

    def test_emergency_pass_records_time_out_as_missed_tap(self):
        P.objects.filter(pk=self.p.pk).update(kind=P.EMERGENCY)
        self.tap("out", at(10))
        self.assertIsNone(self.p.tapped_out_at)
        self.missed("out", at(10), at(10, 30))
        m = M.objects.get()
        self.act(self.head, m, "confirm", at(10, 35))
        self.act(self.guard, m, "verify", at(10, 40))
        self.act(self.hr, m, "review", at(10, 45))
        self.assertEqual(self.p.tapped_out_at, at(10))
        self.tap("in", at(11, 10))
        self.assertEqual((self.p.status, self.p.minutes_charged), (P.COMPLETED, 70))

    def test_reject_needs_remark_and_allows_a_corrected_one(self):
        self.tap("out", at(10))
        self.missed("in", at(10, 40), at(13))
        m = M.objects.get()
        self.act(self.head, m, "reject", at(13, 5))
        self.assertEqual(m.status, M.SUBMITTED)
        self.act(self.head, m, "reject", at(13, 5), notes="You were back at 11:20")
        self.assertEqual(m.status, M.REJECTED)
        self.missed("in", at(11, 20), at(13, 10))
        self.assertEqual(M.objects.filter(status=M.SUBMITTED).count(), 1)

    def test_missed_time_must_make_sense(self):
        self.tap("out", at(10))
        self.missed("in", at(9, 30), at(13))  # before the time out
        self.assertFalse(M.objects.exists())
        self.missed("in", at(14), at(13))  # in the future
        self.assertFalse(M.objects.exists())

    def test_hr_who_confirmed_cannot_also_review(self):
        both = emp("hrhead", Section.objects.get(name="Laboratory Section"), [RA.SUPERVISOR, RA.HR_PROCESSOR])
        RA.objects.filter(employee=self.head).delete()  # the HR person is now the lab's supervisor
        self.tap("out", at(10))
        self.missed("in", at(10, 40), at(13))
        m = M.objects.get()
        self.act(both, m, "confirm", at(13, 1))
        self.act(self.guard, m, "verify", at(13, 2))
        self.assertEqual(self.act(both, m, "review", at(13, 3)).status_code, 403)
        self.act(self.hr, m, "review", at(13, 4))
        self.assertEqual(m.status, M.REVIEWED)

    # -- Gate page --
    def test_gate_page_for_guards_and_hr_only(self):
        self.client.force_login(self.guard.user)
        with mock.patch("django.utils.timezone.localdate", return_value=DAY):
            r = self.client.get(reverse("outonpass:gate"))
        self.assertContains(r, self.p.pass_number)
        self.client.force_login(self.staff.user)
        self.assertEqual(self.client.get(reverse("outonpass:gate")).status_code, 403)
        self.client.force_login(self.hr.user)
        self.assertEqual(self.client.get(reverse("outonpass:gate")).status_code, 200)

    def test_not_back_list(self):
        self.tap("out", at(10))
        with mock.patch("outonpass.gate.current_time", return_value=at(12)):
            self.assertEqual([p.pk for p in gate.not_back()], [self.p.pk])

    # -- HR closes a pass with no Tap In (owner decision 2026-11-14, Option B) --
    def close(self, who, now, **data):
        return self.post(who, reverse("outonpass:hr_close", args=[self.p.pk]), data, now)

    def test_hr_closes_out_not_back_with_remark(self):
        self.tap("out", at(10))
        self.assertEqual(self.close(self.hr, at(10, 30), what="close", time_in="2027-02-03T10:20",
                                    remark="x").status_code, 403)  # expected time in not passed
        self.close(self.hr, at(15), what="close", time_in="2027-02-03T12:40", remark="")
        self.assertIsNone(self.p.tapped_in_at)  # remark required
        self.close(self.hr, at(15), what="close", time_in="2027-02-03T12:40", remark="Gate logbook: 12:40 PM")
        self.assertEqual((self.p.status, self.p.minutes_charged, self.p.excess_minutes), (P.COMPLETED, 160, 40))
        self.assertTrue(self.p.actions.filter(action="hr_close").exists())

    def test_staff_and_supervisor_cannot_close(self):
        self.tap("out", at(10))
        for who in (self.staff, self.head):
            self.assertEqual(self.close(who, at(15), what="close", time_in="2027-02-03T12:00",
                                        remark="x").status_code, 403)

    def test_hr_marks_never_used_pass(self):
        self.close(self.hr, at(15), what="unused", remark="Did not go out, per supervisor")
        self.assertEqual(self.p.status, P.CANCELLED)
        from . import rules
        self.assertEqual(rules.summary(self.staff, DAY)["committed"], 0)

    def test_hr_cannot_close_own_pass(self):
        mine = self.make(self.hr)
        self.p = mine
        self.tap("out", at(10))  # staff tap: not theirs, ignored
        P.objects.filter(pk=mine.pk).update(tapped_out_at=at(10))
        self.assertEqual(self.close(self.hr, at(15), what="close", time_in="2027-02-03T11:00",
                                    remark="x").status_code, 403)
