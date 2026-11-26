"""Gaps against Design Package v1.1 fixed on 2026-11-26, and the owner's four
decisions of the same day (1 B: COSP periods open by themselves; 2 A: pooled
peers added by HR, with a list; 3 B: a deduction counts in one period only;
4 A: one-step peer picking, unchanged). All data made up."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from auditlog.aggregation import audit_rows
from employees.models import AssignmentLine, Holiday, PersonnelActionType
from outonpass.models import CospContractPeriod

from . import deductions as dd, monitoring as mon, raters, results, rules, score_sheet
from .models import (Comment, CospForm, Deduction, DeductionCriterion, EvaluationPeriod as P, PeriodEvent, PMSetting,
                     RaterAssignment as A, Response)
from .test_raters import person
from .test_scoring import ScoringBase, fill

TODAY = timezone.localdate()


class ComputedBase(ScoringBase):
    def computed(self):
        p = self.period(self.n[0], window=False, kind=P.MONTH1)
        p.end_date = TODAY - timedelta(days=1)
        CospForm.objects.create(period=p, supervisor=self.erh, score=Decimal("4.00"), submitted_at=timezone.now())
        p.absence_days, p.tardy_days, p.dtr_verified_by = 0, 0, self.hra2.user
        p.save()
        sup = fill(self.rater(p, A.SUPERVISOR, self.erh), [5, 4, 4, 5, 4, 4, 5, 4, 4, 3, 4, 5])
        Comment.objects.create(assignment=sup, kind=Comment.STRENGTH, raw_text="Calm in codes")
        results.compute(self.hrp, self.hrp.user, p)
        p.refresh_from_db()
        return p


class AuditAndScoreSheetTests(ComputedBase):
    def test_performance_rows_in_the_audit_log_without_scores(self):
        p = self.computed()
        results.review(self.cn, self.cn.user, p, True)
        results.screen_comment(self.hra, self.hra.user, Comment.objects.get(), "release")
        results.release(self.hra, self.hra.user, p)
        rows = audit_rows(module="Performance")
        actions = {r["action"] for r in rows}
        self.assertTrue({"Result computed", "Next-level review: approved", "Comment released", "Result released"}
                        <= actions)
        self.assertFalse(any("TPE" in r["detail"] or "Calm" in r["detail"] for r in rows))

    def test_closed_form_never_names_the_rater(self):
        p = self.period(self.n[0])
        a = self.rater(p, A.PEER, self.n[3])
        raters.answer_contact(a, False, self.n[3].user)
        row = next(r for r in audit_rows(module="Performance") if r["action"].startswith("Rating form closed"))
        self.assertEqual(row["actor"], "Rater (confidential)")

    def test_viewing_raw_comments_is_logged_once_a_day(self):
        p = self.computed()
        self.client.force_login(self.hra.user)
        self.client.get(reverse("performance:period", args=[p.pk]))
        self.client.get(reverse("performance:period", args=[p.pk]))
        self.assertEqual(p.events.filter(action="viewed_raw").count(), 1)

    def test_score_sheet(self):
        p = self.computed()
        ws = score_sheet.build(p, self.hra.user).active
        text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
        self.assertIn("TOTAL PERFORMANCE EVALUATION (TPE) SCORE SHEET", text)
        self.assertIn("[form code to be assigned by HRMO]", text)
        self.assertIn("RECEIVED", " ".join(s.role_label.upper() for s in score_sheet.stamps(p)))
        self.assertFalse(score_sheet.may_print(self.n[0], p))  # not released yet
        self.assertTrue(score_sheet.may_print(self.hra, p))

    def test_released_period_cannot_be_recomputed_and_page_says_so(self):
        p = self.computed()
        results.review(self.cn, self.cn.user, p, True)
        results.screen_comment(self.hra, self.hra.user, Comment.objects.get(), "release")
        results.release(self.hra, self.hra.user, p)
        self.client.force_login(self.hra.user)
        r = self.client.get(reverse("performance:period", args=[p.pk]))
        self.assertNotContains(r, "Compute again")
        self.assertContains(r, "Released ")
        self.assertNotContains(r, "Not yet: the period is not computed")


class DeductionOnceTests(ComputedBase):
    def final(self, code, day, **kw):
        return Deduction.objects.create(employee=self.n[0], criterion=DeductionCriterion.objects.get(code=code),
                                        incident_date=day, description="t", points=Decimal("1.00"),
                                        status=Deduction.FINAL, filed_by=self.hra, final_at=timezone.now(), **kw)

    def test_counted_once_in_the_earliest_unreleased_period(self):
        semestral = self.period(self.n[0])  # ends TODAY + 30
        pre = P.objects.create(employee=self.n[0], period_type=P.PRE_EXPIRY, track=P.RANK, status=P.OPEN,
                               start_date=TODAY - timedelta(days=300), end_date=TODAY + timedelta(days=140))
        d = self.final("R2", TODAY - timedelta(days=10))
        self.assertTrue(dd.counts_in(d, semestral))
        self.assertFalse(dd.counts_in(d, pre))
        dd.claim(semestral)
        d.refresh_from_db()
        self.assertEqual(d.period, semestral)
        self.assertEqual(dd.period_deductions(pre)[1], 0)
        self.assertEqual(dd.elsewhere_notes(pre), [d])

    def test_r7_on_absences_already_counted_is_shown_not_deducted(self):
        p = self.period(self.n[0])
        self.final("R7", TODAY - timedelta(days=5), in_attendance=True)
        rows, total = dd.period_deductions(p)
        self.assertEqual(total, 0)
        self.assertIn("Attendance", rows[0]["note"])

    def test_hr_marks_r7_at_validation(self):
        d = dd.file(self.erh, self.erh.user, self.n[0], DeductionCriterion.objects.get(code="R7"),
                    TODAY - timedelta(days=3), "AWOL decision", Decimal("0.50"), evidence_ref="memo",
                    final_ref="Decision 01")
        dd.validate(self.hrp, self.hrp.user, d, True, in_attendance=True)
        d.refresh_from_db()
        self.assertTrue(d.in_attendance)


class AutoCospPeriodTests(ScoringBase):
    def test_planned_then_opened_by_the_daily_job_never_back_filled(self):
        new = person("pmnewhire", section=self.nursing, unit=self.er, hired=TODAY - timedelta(days=10))
        CospContractPeriod.objects.create(employee=new, start_date=TODAY - timedelta(days=10),
                                          end_date=TODAY + timedelta(days=355), entered_by=self.hra.user)
        old = person("pmold", section=self.nursing, unit=self.er, hired=TODAY - timedelta(days=400))
        CospContractPeriod.objects.create(employee=old, start_date=TODAY - timedelta(days=400),
                                          end_date=TODAY + timedelta(days=10), entered_by=self.hra.user)
        rules.auto_cosp_periods(TODAY)
        m1 = P.objects.get(employee=new, period_type=P.MONTH1)
        self.assertEqual(m1.status, P.PLANNED)
        self.assertFalse(P.objects.filter(employee=old).exists())  # its pre-expiry period already ended
        rules.auto_cosp_periods(TODAY)  # twice: nothing doubled
        self.assertEqual(P.objects.filter(employee=new, period_type=P.MONTH1).count(), 1)
        rules.auto_cosp_periods(m1.end_date - timedelta(days=5))
        m1.refresh_from_db()
        self.assertEqual(m1.status, P.OPEN)
        self.assertTrue(m1.raters.filter(group=A.SUPERVISOR, rater=self.erh).exists())
        self.assertTrue(m1.events.filter(action="opened").exists())

    def test_cancel_needs_an_hr_administrator_and_a_reason(self):
        c = CospContractPeriod.objects.filter(employee=self.n[0]).last()
        p = P.objects.create(employee=self.n[0], contract=c, period_type=P.PRE_EXPIRY, track=P.RANK, status=P.PLANNED,
                             start_date=c.start_date, end_date=c.end_date - timedelta(days=60))
        for who, reason in ((self.hrp, "x"), (self.hra, "")):
            with self.assertRaises(Exception):
                rules.cancel_period(who, who.user, p, reason)
        rules.cancel_period(self.hra, self.hra.user, p, "Contract ended early")
        p.refresh_from_db()
        self.assertEqual(p.status, P.CANCELLED)


class HistoryTests(ScoringBase):
    def line(self, emp, start, opening=False):
        return AssignmentLine.objects.create(
            employee=emp, section=self.nursing, unit=self.er, effective_from=start, status=AssignmentLine.VERIFIED,
            is_opening=opening, action_type=PersonnelActionType.objects.get(name="Transfer"),
            encoded_by=self.hrp.user, verified_by=self.hra.user)

    def test_share_from_the_history_and_opening_lines(self):
        p = self.period(self.n[1])
        self.assertTrue(raters.history_check(self.n[1], self.erh, p)[0])  # no history yet: contact question only
        self.line(self.n[1], TODAY - timedelta(days=50))  # 81 of 181 days
        ok, note = raters.history_check(self.n[1], self.erh, p)
        self.assertFalse(ok)
        self.assertIn("45%", note)
        AssignmentLine.objects.filter(employee=self.n[1]).update(is_opening=True)
        self.assertTrue(raters.history_check(self.n[1], self.erh, p)[0])

    def test_subordinate_below_the_share_is_left_off_and_shown_to_hr(self):
        self.line(self.n[1], TODAY - timedelta(days=50))
        p = self.period(self.erh, track=P.SUPERVISOR)
        raters.build_assignments(p, self.hra.user)
        subs = set(p.raters.filter(group=A.SUBORDINATE).values_list("rater_id", flat=True))
        self.assertNotIn(self.n[1].pk, subs)
        self.assertIn(self.n[0].pk, subs)
        self.assertTrue(p.events.filter(action="rater_left_off").exists())
        self.assertEqual([e for e, _ in results._left_off(p)], [self.n[1]])

    def test_contact_question_follows_the_setting(self):
        p = self.period(self.n[0])
        a = self.rater(p, A.PEER, self.n[2])
        self.assertIn("at least half of the time", raters.contact_question(a))
        PMSetting.objects.create(key="contact_share", value="0.6", effective_from=TODAY, approval_ref="PMT test")
        self.assertIn("at least 60% of the time", raters.contact_question(a))


class PoolingAndLeniencyTests(ScoringBase):
    def test_short_of_peers_after_picking(self):
        p = self.period(self.n[0])
        self.rater(p, A.PEER, self.n[1])
        self.assertEqual(raters.needs_pooling(p), 0)  # supervisor has not picked yet
        PeriodEvent.objects.create(period=p, action="peers_finalized", user=self.erh.user)
        self.assertEqual(raters.needs_pooling(p), 2)
        names = raters.pool_candidates(p)
        self.assertNotIn(self.n[1], names)
        self.assertNotIn(self.n[0], names)
        self.assertEqual(mon.short_of_peers([p]), [(p, 2)])

    def test_ratings_given_hide_small_confidential_groups(self):
        p = self.period(self.n[0])
        fill(self.rater(p, A.SUPERVISOR, self.erh), [5] * 12)
        for who in self.n[1:3]:
            fill(self.rater(p, A.PEER, who), [4] * 12)
        row = mon.ratings_given([p])[0]
        self.assertEqual(row["sup"]["mean"], Decimal("5.00"))
        self.assertIsNone(row["peer"]["mean"])  # 2 peers only
        self.assertEqual(mon.supervisor_given([p])[0]["n"], 1)


class ScreenTests(ScoringBase):
    def test_autosave_progress_and_texts(self):
        p = self.period(self.n[0])
        a = self.rater(p, A.SUPERVISOR, self.erh)
        self.client.force_login(self.erh.user)
        r = self.client.post(reverse("performance:rate_autosave", args=[a.pk]), {"i_A1": "4"})
        self.assertContains(r, "Saved")
        self.assertEqual(Response.objects.get(assignment=a, indicator__code="A1").value, 4)
        r = self.client.get(reverse("performance:tasks"))
        self.assertContains(r, "0 of 1 done")
        sub = self.rater(self.period(self.erh, track=P.SUPERVISOR), A.SUBORDINATE, self.n[0], "B")
        sub.contact_ok, sub.conflict_cleared = True, True
        sub.save()
        self.client.force_login(self.n[0].user)
        r = self.client.get(reverse("performance:rate", args=[sub.pk]))
        self.assertContains(r, "Avoid rating everyone high, or everyone low.")

    def test_conflict_answer_is_kept_on_the_form(self):
        p = self.period(self.n[0])
        a = self.rater(p, A.SUPERVISOR, self.erh)
        raters.answer_conflict(a, False, self.erh.user)
        a.refresh_from_db()
        self.assertTrue(a.conflict_cleared)

    def test_holiday_list_and_history_views(self):
        Holiday.objects.create(date=TODAY, name="Made-up Day", created_by=self.hra.user)
        self.client.force_login(self.n[0].user)
        r = self.client.get(reverse("employees:holidays"))
        self.assertContains(r, "Made-up Day")
        self.assertNotContains(r, "Add a holiday")
        self.assertEqual(self.client.post(reverse("employees:holidays"), {"date": "2026-12-01", "name": "x"})
                         .status_code, 403)
        self.assertEqual(self.client.get(reverse("employees:employee_assignments", args=[self.n[1].pk]))
                         .status_code, 403)
        self.client.force_login(self.erh.user)  # the unit head sees own staff
        self.assertEqual(self.client.get(reverse("employees:employee_assignments", args=[self.n[1].pk]))
                         .status_code, 200)

    def test_hr_processor_sets_the_wording(self):
        self.client.force_login(self.hrp.user)
        self.client.post(reverse("performance:setup"), {"what": "wording", "employee": self.n[0].pk, "track": "2",
                                                       "reason": "Works in the dietary office"})
        self.assertEqual(raters.form_a_wording(self.n[0]), "2")
