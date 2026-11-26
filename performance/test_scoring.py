"""PMS steps 4-5 (rebuilt 2026-11-26): the v1.1 §5.2 worked examples, review, release,
contests and the deduction route. All data made up."""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from . import deductions as dd, results, scoring
from .models import (Comment, CospForm, Deduction, DeductionCriterion, EvaluationPeriod as P, Indicator, PMSetting,
                     RaterAssignment as A, RatingWindow, Response)
from .test_raters import Base, person

TODAY = timezone.localdate()


def fill(a, values, form="A"):
    """values: one per scored item (None = N/O)."""
    codes = scoring.scored_codes(form)
    for code, v in zip(codes, values):
        Response.objects.update_or_create(assignment=a, indicator=Indicator.objects.get(code=code),
                                          defaults={"value": v, "not_observed": v is None})
    a.status, a.submitted_at = A.SUBMITTED, timezone.now()
    a.first_shown_at = a.submitted_at - timedelta(minutes=5)
    a.save()
    return a


def avg_values(target, n=12):
    """12 values (some N/O) whose average is the target."""
    table = {Decimal("4.25"): [5] * 3 + [4] * 9, Decimal("4.5"): [5] * 6 + [4] * 6, Decimal("4.0"): [4] * 12,
             Decimal("3.8"): [4] * 8 + [3] * 2 + [None] * 2, Decimal("4.2"): [5] * 2 + [4] * 8 + [None] * 2,
             Decimal("4.6"): [5] * 6 + [4] * 4 + [None] * 2, Decimal("3.9"): [4] * 9 + [3] * 1 + [None] * 2,
             Decimal("2.8"): [3] * 8 + [2] * 2 + [None] * 2, Decimal("4.4"): [5] * 4 + [4] * 6 + [None] * 2}
    return table[Decimal(target)]


class ScoringBase(Base):
    def rater(self, period, group, who, form="A"):
        return A.objects.create(period=period, rater=who, group=group, form=form, status=A.ASSIGNED)

    def final_deduction(self, emp, code, points, day=None):
        d = Deduction.objects.create(employee=emp, criterion=DeductionCriterion.objects.get(code=code),
                                     incident_date=day or TODAY - timedelta(days=10), description="t",
                                     points=Decimal(points), status=Deduction.FINAL, filed_by=self.hra,
                                     final_at=timezone.now())
        return d


class WorkedExampleTests(ScoringBase):
    def test_example_1_regular_rank_and_file(self):
        emp = person("pmregular", section=self.nursing, unit=self.er, status="REGULAR")
        p = self.period(emp, track=P.RANK)
        p.certified_rating, p.certified_verified_by = Decimal("4.40"), self.hra2.user
        p.absence_days, p.tardy_days, p.dtr_verified_by = 1, 2, self.hra2.user
        p.save()
        fill(self.rater(p, A.SUPERVISOR, self.erh), avg_values("4.25"))
        for who, v in zip(self.n[:4], ["4.5", "4.0", "3.8", "4.2"]):
            fill(self.rater(p, A.PEER, who), avg_values(v))
        self.final_deduction(emp, "R3", "2")
        r = scoring.score(p)
        pts = {x["component"]: scoring.show(x["points"]) for x in r["parts"]}
        self.assertEqual(pts, {"perf": Decimal("44.0"), "sup": Decimal("25.5"), "peer": Decimal("8.3"),
                               "att": Decimal("7.9")})
        self.assertEqual(scoring.show(r["total"]), Decimal("83.7"))
        self.assertEqual(scoring.description(r["total"], p), "Strong")

    def test_example_2_cosp_month_one_peer_skipped(self):
        p = self.period(self.n[0], window=False, kind=P.MONTH1)
        CospForm.objects.create(period=p, supervisor=self.erh, score=Decimal("4.00"), submitted_at=timezone.now())
        p.absence_days, p.tardy_days, p.dtr_verified_by = 0, 1, self.hra2.user
        p.save()
        fill(self.rater(p, A.SUPERVISOR, self.erh), avg_values("4.5"))
        r = scoring.score(p)
        used = {x["component"]: scoring.show(x["weight_used"]) for x in r["parts"]}
        self.assertEqual(used, {"perf": Decimal("55.6"), "sup": Decimal("33.3"), "peer": Decimal("0.0"),
                                "att": Decimal("11.1")})
        self.assertEqual(scoring.show(r["total"]), Decimal("85.2"))

    def test_example_3_unit_head_subordinates_skipped(self):
        emp = person("pmuh", section=self.nursing, status="REGULAR")
        p = self.period(emp, track=P.SUPERVISOR)
        p.certified_rating, p.certified_verified_by = Decimal("4.20"), self.hra2.user
        p.save()
        fill(self.rater(p, A.TOP, self.ao, "B"), [4] * 15, "B")
        fill(self.rater(p, A.TOP, self.coh, "B"), [5] * 6 + [4] * 9, "B")  # 4.4
        for who in self.n[:2]:
            fill(self.rater(p, A.SUBORDINATE, who, "B"), [4] * 15, "B")
        peers = [person(f"pmsp{i}", section=self.nursing, status="REGULAR") for i in range(5)]
        for who, v in zip(peers, ["4.6", "4.2", "4.0", "3.9", "2.8"]):
            fill(self.rater(p, A.SUP_PEER, who, "B"), avg_values(v) + [None] * 3, "B")
        self.final_deduction(emp, "R1", "1")
        r = scoring.score(p)
        sub = next(x for x in r["parts"] if x["component"] == "sub")
        self.assertTrue(sub["skipped"] and "never pooled" in sub["note"])
        self.assertEqual(scoring.show(r["total"]), Decimal("82.1"))  # no trimming (owner decision 2026-11-25)
        PMSetting.objects.create(key="trim_threshold", value="5", effective_from=date(2026, 1, 1), approval_ref="t")
        self.assertEqual(scoring.show(scoring.score(p)["total"]), Decimal("82.5"))  # v1.1 example with trimming

    def test_example_4_deduction_cap(self):
        emp = person("pmcap", section=self.nursing, unit=self.er, status="REGULAR")
        p = self.period(emp, track=P.RANK)
        p.certified_rating, p.certified_verified_by = Decimal("5.00"), self.hra2.user
        p.absence_days, p.tardy_days, p.dtr_verified_by = 0, 0, self.hra2.user
        p.save()
        fill(self.rater(p, A.SUPERVISOR, self.erh), [5] * 12)
        self.final_deduction(emp, "R3", "9")
        self.final_deduction(emp, "R5", "8")
        r = scoring.score(p)
        self.assertEqual(r["deductions"], Decimal("15"))
        self.assertEqual(scoring.show(r["total"]), Decimal("85.0"))

    def test_example_6_subordinate_no_rule(self):
        p = self.period(self.erh, track=P.SUPERVISOR)
        a = self.rater(p, A.SUBORDINATE, self.n[0], "B")
        values = [None] * 3 + [4] * 6 + [None, None] + [5] + [None] * 3  # N/O on B1-3, B10-11, B13-15
        fill(a, values, "B")
        avg, why = scoring.rater_average(a, p)
        self.assertEqual(avg, Decimal(29) / 7)
        peer = self.rater(p, A.SUP_PEER, self.cn, "B")
        fill(peer, values, "B")  # 8 of 15 N/O: not counted for a peer
        self.assertIsNone(scoring.rater_average(peer, p)[0])


class ReleaseAndContestTests(ScoringBase):
    def setUp(self):
        super().setUp()
        self.p = self.period(self.n[0], window=False, kind=P.MONTH1)
        self.p.end_date = TODAY - timedelta(days=1)
        CospForm.objects.create(period=self.p, supervisor=self.erh, score=Decimal("4.00"), submitted_at=timezone.now())
        self.p.absence_days, self.p.tardy_days, self.p.dtr_verified_by = 0, 0, self.hra2.user
        self.p.save()
        self.sup = fill(self.rater(self.p, A.SUPERVISOR, self.erh), [4] * 12)  # straight-line
        Comment.objects.create(assignment=self.sup, kind=Comment.STRENGTH, raw_text="Calm in codes")

    def test_inputs_need_a_second_hr_person(self):
        emp = person("pmreg2", section=self.nursing, unit=self.er, status="REGULAR")
        p = self.period(emp, track=P.RANK)
        results.encode_certified(self.hra, self.hra.user, p, "4.10", "PHRMO-TEST-1")
        with self.assertRaises(ValidationError):
            results.verify_input(self.hra, self.hra.user, p, "certified")
        results.verify_input(self.hra2, self.hra2.user, p, "certified")
        with self.assertRaises(ValidationError):
            results.encode_certified(self.hra, self.hra.user, self.p, "4.10", "x")  # COSP: no certified rating

    def test_compute_review_flags_comments_release_contest(self):
        results.compute(self.hrp, self.hrp.user, self.p)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, P.UNDER_REVIEW)  # COSP: next-level review is mandatory
        self.assertEqual(results.next_level_reviewers(self.p), [self.cn])
        results.review(self.cn, self.cn.user, self.p, True)
        self.assertIn("flags are waiting", results.release_problems(self.p))
        self.sup.refresh_from_db()
        self.assertIn("straight-line", self.sup.flags)
        with self.assertRaises(ValidationError):
            results.decide_flag(self.hra, self.hra.user, self.sup, False, "")
        results.decide_flag(self.hra, self.hra.user, self.sup, True)
        results.screen_comment(self.hra, self.hra.user, Comment.objects.get(), "release")
        results.release(self.hra, self.hra.user, self.p)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, P.RELEASED)
        self.client.force_login(self.n[0].user)
        r = self.client.get(reverse("performance:result", args=[self.p.pk]))
        self.assertContains(r, "Calm in codes")
        self.assertNotContains(r, "A1")  # wording and means, no item codes
        results.file_contest(self.n[0], self.n[0].user, self.p, "The supervisor rated the wrong month.")
        c = self.p.contests.get()
        with self.assertRaises(ValidationError):
            results.contest_step(self.coh, self.coh.user, c, "x")  # HR first
        results.contest_step(self.hrp, self.hrp.user, c, "Checked")
        results.contest_step(self.ao, self.ao.user, c, "Endorsed")
        results.contest_step(self.coh, self.coh.user, c, "Upheld", upheld=True)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, P.FINAL)

    def test_finalize_after_the_contest_window(self):
        results.compute(self.hrp, self.hrp.user, self.p)
        results.review(self.cn, self.cn.user, self.p, True)
        self.sup.refresh_from_db()
        results.decide_flag(self.hra, self.hra.user, self.sup, True)
        results.screen_comment(self.hra, self.hra.user, Comment.objects.get(), "withhold", reason="Identifies rater")
        results.release(self.hra, self.hra.user, self.p)
        results.finalize_due(TODAY + timedelta(days=30))
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, P.FINAL)

    def test_semestral_waits_for_the_window(self):
        p = self.period(self.n[1])
        with self.assertRaises(ValidationError):
            results.compute(self.hrp, self.hrp.user, p)
        RatingWindow.objects.filter(pk=self.window.pk).update(status=RatingWindow.CLOSED)
        p.refresh_from_db()
        with self.assertRaises(ValidationError):
            results.compute(self.hrp, self.hrp.user, p)  # inputs still missing


class DeductionRouteTests(ScoringBase):
    def test_route_to_final_and_period_reopened(self):
        crit = DeductionCriterion.objects.get(code="R4")
        with self.assertRaises(ValidationError):
            dd.file(self.n[0], self.n[0].user, self.n[0], crit, TODAY, "x", "0.50", "ref")  # about yourself
        with self.assertRaises(ValidationError):
            dd.file(self.erh, self.erh.user, self.n[0], DeductionCriterion.objects.get(code="S1"), TODAY, "x",
                    "1.00", "ref")  # supervisors only
        d = dd.file(self.erh, self.erh.user, self.n[0], crit, TODAY, "No endorsement at shift change", "0.50",
                    "Logbook p. 4")
        with self.assertRaises(ValidationError):
            dd.validate(self.n[0], self.n[0].user, d, True)
        dd.validate(self.hrp, self.hrp.user, d, True)
        with self.assertRaises(ValidationError):
            dd.decide(self.ao, self.ao.user, d, True)  # wait for the explanation
        dd.explain(self.n[0], self.n[0].user, d, "Monitor was down.")
        with self.assertRaises(ValidationError):
            dd.decide(self.coh, self.coh.user, d, True)  # the AO decides
        dd.decide(self.ao, self.ao.user, d, True)
        p = self.period(self.n[0], window=False, kind=P.QUARTER)
        p.start_date, p.end_date, p.status = TODAY - timedelta(days=30), TODAY + timedelta(days=1), P.COMPUTED
        p.save()
        dd.finalize_due(TODAY + timedelta(days=30))
        d.refresh_from_db()
        p.refresh_from_db()
        self.assertEqual(d.status, Deduction.FINAL)
        self.assertEqual(p.status, P.OPEN)  # computed again with the deduction
        self.assertEqual(dd.period_deductions(p)[1], Decimal("0.50"))

    def test_coh_decides_when_the_ao_is_the_employee_and_review_route(self):
        d = dd.file(self.coh, self.coh.user, self.ao, DeductionCriterion.objects.get(code="S4"), TODAY, "x", "1.00",
                    "ref")
        dd.validate(self.hrp, self.hrp.user, d, True)
        dd.explain(self.ao, self.ao.user, d, "y")
        self.assertFalse(dd.decider_ok(self.ao, d))
        self.assertFalse(dd.decider_ok(self.coh, d))  # the COH filed it
        d2 = dd.file(self.hra, self.hra.user, self.n[1], DeductionCriterion.objects.get(code="X9"), TODAY, "x", "2.00",
                     "ref")
        dd.validate(self.hrp, self.hrp.user, d2, True)
        dd.explain(self.n[1], self.n[1].user, d2, "y")
        dd.decide(self.ao, self.ao.user, d2, True)
        dd.request_review(self.n[1], self.n[1].user, d2, "Wrong person")
        dd.review_step(self.hrp, self.hrp.user, d2, "Checked")
        with self.assertRaises(ValidationError):
            dd.review_step(self.ao, self.ao.user, d2, "x")  # the AO approved it: an HR Administrator endorses
        dd.review_step(self.hra2, self.hra2.user, d2, "Endorsed")
        dd.review_step(self.coh, self.coh.user, d2, "Not proven", stands=False)
        d2.refresh_from_db()
        self.assertEqual(d2.status, Deduction.DISMISSED)

    def test_criteria_changes_need_approval_and_are_logged(self):
        c = DeductionCriterion.objects.get(code="X7")
        with self.assertRaises(ValidationError):
            dd.change_criterion(self.hra, self.hra.user, c, "1.00", "4", True, "")
        dd.change_criterion(self.hra, self.hra.user, c, "1.00", "4", True, "PMT 2027-01")
        c.refresh_from_db()
        self.assertEqual(c.cap, Decimal("4"))
        self.assertTrue(c.changes.filter(field="cap").exists())

    def test_pages(self):
        self.client.force_login(self.erh.user)
        self.assertContains(self.client.get(reverse("performance:incidents")), "File an incident")
        self.client.post(reverse("performance:incidents"), {
            "employee": self.n[0].pk, "criterion": DeductionCriterion.objects.get(code="R1").pk,
            "incident_date": TODAY.isoformat(), "points": "0.50", "evidence_ref": "Memo", "description": "Late"})
        d = Deduction.objects.get()
        self.client.force_login(self.n[0].user)
        self.assertContains(self.client.get(reverse("performance:incident", args=[d.pk])), self.erh.full_name)
        self.client.force_login(self.n[1].user)
        self.assertEqual(self.client.get(reverse("performance:incident", args=[d.pk])).status_code, 403)
