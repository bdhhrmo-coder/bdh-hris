"""PMS steps 2-3 (rebuilt 2026-11-26): settings, tracks, periods, raters and the rating
forms. All data made up."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment as R
from employees.models import Employee
from notifications.models import Notification
from orgstructure.models import Section, Unit
from outonpass.models import CospContractPeriod

from . import raters, rules
from .models import EvaluationPeriod as P, Indicator, PMSetting, RaterAssignment as A, RatingWindow

TODAY = timezone.localdate()


def person(name, role=None, section=None, unit=None, role_section=None, role_unit=None, status="COSP",
           position="Nurse I", hired=date(2023, 1, 1)):
    user = User.objects.create_user(name, password="x")
    e = Employee.objects.create(user=user, employee_id=name.upper(), surname=name.title(), first_name="T",
                                position=position, date_hired=hired, employment_status=status)
    for s in [section] if section else []:
        e.sections.add(s)
    if unit:
        e.units.add(unit)
    if role:
        R.objects.create(employee=e, role=role, section=role_section, unit=role_unit)
    return e


class Base(TestCase):
    def setUp(self):
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.gss = Section.objects.get(name="General Services Section")
        self.er = Unit.objects.get(section=self.nursing, name="ER")
        self.hrp = person("pmhrp", R.HR_PROCESSOR, status="REGULAR")
        self.hra = person("pmhra", R.HR_ADMINISTRATOR, status="REGULAR")
        self.hra2 = person("pmhra2", R.HR_ADMINISTRATOR, status="REGULAR")
        self.ao = person("pmao", R.ADMINISTRATIVE_OFFICER, status="REGULAR")
        self.coh = person("pmcoh", R.CHIEF_OF_HOSPITAL, status="REGULAR")
        self.cn = person("pmcn", R.SUPERVISOR, self.nursing, role_section=self.nursing, status="REGULAR")
        self.erh = person("pmerh", R.SUPERVISOR, self.nursing, self.er, role_unit=self.er, status="REGULAR")
        self.n = [person(f"pmnurse{i}", section=self.nursing, unit=self.er) for i in range(6)]
        for e in self.n:
            CospContractPeriod.objects.create(employee=e, start_date=date(2022, 1, 1), end_date=date(2022, 12, 31),
                                              entered_by=self.hra.user)
            CospContractPeriod.objects.create(employee=e, start_date=TODAY - timedelta(days=300),
                                              end_date=TODAY + timedelta(days=200), entered_by=self.hra.user)
        self.window = RatingWindow.objects.create(name="Test window", semester="2099-S1", start_date=TODAY - timedelta(days=150),
                                                  end_date=TODAY + timedelta(days=30), opens_on=TODAY,
                                                  closes_on=TODAY + timedelta(days=20), status=RatingWindow.OPEN,
                                                  created_by=self.hra.user)

    def period(self, emp, status=P.OPEN, window=True, kind=P.SEMESTRAL, track=None):
        return P.objects.create(employee=emp, window=self.window if window else None, period_type=kind,
                                track=track or rules.track_for(emp, TODAY), status=status,
                                start_date=self.window.start_date, end_date=self.window.end_date)

    def task(self, period, group):
        return period.raters.get(group=group)


class SeedAndSettingsTests(Base):
    def test_instruments_seeded(self):
        self.assertEqual(Indicator.objects.filter(code__regex=r"^A\d+$").count(), 12)
        self.assertEqual(Indicator.objects.filter(code__regex=r"^B\d+$").count(), 15)
        self.assertEqual(set(Indicator.objects.filter(day_to_day=True).values_list("code", flat=True)),
                         {f"B{i}" for i in range(4, 13)})
        self.assertFalse(Indicator.objects.filter(code__in=["Q1", "Q2", "Q3"], scored=True).exists())
        self.assertEqual(rules.number_for("trim_threshold"), 0)
        self.assertEqual(rules.number_for("w_sup_sub"), 30)

    def test_settings_append_only_with_approval(self):
        with self.assertRaises(ValidationError):
            rules.change_setting(self.hrp, self.hrp.user, "peer_cap", "5", TODAY, "PMT")
        with self.assertRaises(ValidationError):
            rules.change_setting(self.hra, self.hra.user, "peer_cap", "5", TODAY, "")
        with self.assertRaises(ValidationError):
            rules.change_setting(self.hra, self.hra.user, "peer_cap", "5", TODAY - timedelta(days=1), "PMT")
        rules.change_setting(self.hra, self.hra.user, "peer_cap", "5", TODAY + timedelta(days=10), "PMT test")
        self.assertEqual(rules.number_for("peer_cap"), 4)
        self.assertEqual(rules.number_for("peer_cap", TODAY + timedelta(days=10)), 5)
        self.assertEqual(PMSetting.objects.filter(key="peer_cap").count(), 2)


class TrackAndPeriodTests(Base):
    def test_tracks(self):
        self.assertEqual(rules.track_for(self.n[0], TODAY), P.RANK)
        self.assertEqual(rules.track_for(self.erh, TODAY), P.SUPERVISOR)
        self.assertEqual(rules.track_for(self.ao, TODAY), P.SUPERVISOR)
        self.assertIsNone(rules.track_for(self.coh, TODAY))
        oic = self.n[5]
        R.objects.create(employee=oic, role=R.SUPERVISOR, unit=self.er, is_oic=True, delegated_by=self.erh,
                         start_date=TODAY - timedelta(days=1), end_date=TODAY + timedelta(days=5))
        self.assertEqual(rules.track_for(oic, TODAY), P.RANK)  # OIC lines never count

    def test_regular_track_waits_for_the_setting(self):
        self.assertFalse(rules.in_scope(self.erh, TODAY))
        PMSetting.objects.create(key="regular_track_enabled", value="1", effective_from=TODAY, approval_ref="CSC FO")
        self.assertTrue(rules.in_scope(self.erh, TODAY))

    def test_cosp_plan_first_and_later_contract(self):
        new = person("pmnew", section=self.nursing, unit=self.er, hired=date(2027, 1, 1))
        c = CospContractPeriod.objects.create(employee=new, start_date=date(2027, 1, 1), end_date=date(2027, 12, 31),
                                              entered_by=self.hra.user)
        kinds = [k for k, s, e in rules.cosp_plan(c)]
        self.assertEqual(kinds[0], P.MONTH1)
        self.assertEqual(kinds[-1], P.PRE_EXPIRY)
        self.assertIn(P.QUARTER, kinds)
        plan = rules.cosp_plan(c)
        self.assertEqual(plan[-1][2], date(2027, 12, 31) - timedelta(days=60))
        for (k1, s1, e1), (k2, s2, e2) in zip(plan, plan[1:]):
            self.assertEqual(s2, e1 + timedelta(days=1))  # no gaps
        later = CospContractPeriod.objects.filter(employee=self.n[0]).last()
        self.assertEqual([k for k, s, e in rules.cosp_plan(later)], [P.PRE_EXPIRY])
        self.assertEqual(rules.plan_cosp_periods(self.hrp, self.hrp.user, c), len(plan))
        self.assertTrue(P.objects.filter(employee=new, status=P.PLANNED).exists())

    def test_open_window_creates_periods_for_eligible_only(self):
        new = person("pmfirst", section=self.nursing, unit=self.er, hired=TODAY - timedelta(days=200))
        CospContractPeriod.objects.create(employee=new, start_date=TODAY - timedelta(days=200),
                                          end_date=TODAY + timedelta(days=200), entered_by=self.hra.user)
        self.window.status = RatingWindow.CLOSED
        self.window.save()
        w = rules.create_window(self.hra, self.hra.user, "2098-S2", TODAY, TODAY + timedelta(days=20))
        w.start_date, w.end_date = TODAY - timedelta(days=150), TODAY + timedelta(days=30)
        w.save()
        warnings = rules.open_window(self.hra, self.hra.user, w)
        self.assertTrue(warnings)  # no holidays entered
        got = set(P.objects.filter(window=w).values_list("employee_id", flat=True))
        self.assertTrue({e.pk for e in self.n} <= got)
        self.assertNotIn(new.pk, got)        # first contract: month one / quarterly instead
        self.assertNotIn(self.erh.pk, got)   # regular track not enabled yet
        self.assertNotIn(self.coh.pk, got)


class RaterTests(Base):
    def test_rank_and_file_self_and_supervisor(self):
        p = self.period(self.n[0])
        raters.build_assignments(p, self.hra.user)
        self.assertEqual(self.task(p, A.SUPERVISOR).rater, self.erh)
        self.assertEqual(self.task(p, A.SELF).form, "C")
        self.assertEqual(self.task(p, A.SUPERVISOR).wording, "1")  # Nursing: clinical wording

    def test_absent_approver_rates_when_an_oic_covers(self):
        oic = self.n[5]
        R.objects.create(employee=oic, role=R.SUPERVISOR, unit=self.er, is_oic=True, delegated_by=self.erh,
                         start_date=TODAY - timedelta(days=1), end_date=TODAY + timedelta(days=60))
        self.assertEqual(raters.immediate_supervisors(self.n[0]), [self.erh])

    def test_supervisor_track_top_and_subordinates(self):
        p = self.period(self.erh, track=P.SUPERVISOR)
        raters.build_assignments(p, self.hra.user)
        self.assertEqual({a.rater for a in p.raters.filter(group=A.TOP)}, {self.ao, self.coh})
        self.assertEqual({a.rater for a in p.raters.filter(group=A.SUBORDINATE)}, set(self.n))
        self.assertTrue(all(a.wording == "1" for a in p.raters.filter(group=A.SUBORDINATE)))
        cn = self.period(self.cn, track=P.SUPERVISOR)
        raters.build_assignments(cn, self.hra.user)
        self.assertEqual({a.rater for a in cn.raters.filter(group=A.SUBORDINATE)}, {self.erh})
        self.assertEqual(cn.raters.filter(group=A.SUBORDINATE).first().wording, "2")
        ao = self.period(self.ao, track=P.SUPERVISOR)
        raters.build_assignments(ao, self.hra.user)
        self.assertEqual({a.rater for a in ao.raters.filter(group=A.TOP)}, {self.coh})  # the COH alone

    def test_peers_propose_and_finalize(self):
        p = self.period(self.n[0])
        raters.build_assignments(p, self.hra.user)
        with self.assertRaises(ValidationError):
            raters.propose_peers(self.n[0], p, self.n[1:6] + [self.cn])  # more than 5
        raters.propose_peers(self.n[0], p, self.n[1:4])
        with self.assertRaises(ValidationError):
            raters.finalize_peers(self.n[0], self.n[0].user, p, self.n[1:4])  # not the employee
        with self.assertRaises(ValidationError):
            raters.finalize_peers(self.erh, self.erh.user, p, self.n[1:4])  # all proposed
        raters.finalize_peers(self.erh, self.erh.user, p, [self.n[1], self.n[2], self.n[4]])
        self.assertEqual(raters.counted_peers(p).count(), 3)
        self.assertEqual(p.raters.get(rater=self.n[3]).status, A.NOT_SELECTED)

    def test_peer_cap(self):
        PMSetting.objects.create(key="peer_cap", value="1", effective_from=date(2026, 1, 1), approval_ref="t")
        p1, p2 = self.period(self.n[0]), self.period(self.n[1])
        raters.finalize_peers(self.erh, self.erh.user, p1, [self.n[2], self.n[3], self.n[4]])
        with self.assertRaises(ValidationError):
            raters.finalize_peers(self.erh, self.erh.user, p2, [self.n[2], self.n[3], self.n[5]])


class RatingFormTests(Base):
    def setUp(self):
        super().setUp()
        self.p = self.period(self.n[0])
        raters.build_assignments(self.p, self.hra.user)
        self.sup = self.task(self.p, A.SUPERVISOR)

    def post(self, a, **data):
        self.client.force_login(a.rater.user)
        return self.client.post(reverse("performance:rate", args=[a.pk]), data)

    def answers(self, a, value="4"):
        data = {f"i_A{i}": value for i in range(1, 13)}
        data["what"] = "submit"
        return data

    def test_conflict_then_orientation_then_form(self):
        self.client.force_login(self.erh.user)
        self.assertContains(self.client.get(reverse("performance:rate", args=[self.sup.pk])), "Do any of these apply?")
        self.post(self.sup, what="conflict", answer="none")
        self.assertContains(self.client.get(reverse("performance:rate", args=[self.sup.pk])), "Before you rate")
        self.post(self.sup, what="orientation")
        self.assertContains(self.client.get(reverse("performance:rate", args=[self.sup.pk])), "Treats patients")
        self.post(self.sup, what="save", **{"i_A1": "5"})
        self.sup.refresh_from_db()
        self.assertEqual(self.sup.status, A.IN_PROGRESS)
        data = self.answers(self.sup)
        del data["i_A12"]
        self.post(self.sup, **data)
        self.sup.refresh_from_db()
        self.assertEqual(self.sup.status, A.IN_PROGRESS)  # one item left
        self.post(self.sup, **self.answers(self.sup))
        self.sup.refresh_from_db()
        self.assertEqual(self.sup.status, A.SUBMITTED)
        self.assertIsNotNone(self.sup.first_shown_at)
        self.post(self.sup, what="save", **{"i_A1": "1"})
        self.assertEqual(self.sup.responses.get(indicator__code="A1").value, 4)  # locked

    def test_conflict_closes_and_alerts_hr_without_naming(self):
        self.post(self.sup, what="conflict", answer="applies")
        self.sup.refresh_from_db()
        self.assertEqual(self.sup.status, A.CONFLICT)
        note = Notification.objects.filter(recipient=self.hra).last()
        self.assertIn("closed without counting", note.message)
        self.assertNotIn(self.erh.surname, note.message)

    def test_peer_contact_no_closes(self):
        raters.finalize_peers(self.erh, self.erh.user, self.p, self.n[1:4])
        peer = self.p.raters.get(rater=self.n[1])
        self.client.force_login(self.n[1].user)
        self.assertContains(self.client.get(reverse("performance:rate", args=[peer.pk])), "at least weekly")
        self.post(peer, what="contact", answer="no")
        peer.refresh_from_db()
        self.assertEqual(peer.status, A.DECLINED)
        self.assertIn(self.p, raters.peers_to_finalize(self.erh))  # back on the supervisor's list

    def test_self_form_has_no_na_and_requires_text(self):
        me = self.task(self.p, A.SELF)
        self.client.force_login(self.n[0].user)
        self.post(me, what="orientation")
        r = self.client.get(reverse("performance:rate", args=[me.pk]))
        self.assertContains(r, "Rate yourself on: Respect")
        self.assertNotContains(r, 'value="NO"')
        data = {f"i_CA{i}": "4" for i in range(1, 5)}
        data.update({"what": "submit", "i_C2": "Calm in codes", "i_C3": ""})
        self.post(me, **data)
        me.refresh_from_db()
        self.assertNotEqual(me.status, A.SUBMITTED)
        data["i_C3"] = "Charting speed"
        self.post(me, **data)
        me.refresh_from_db()
        self.assertEqual(me.status, A.SUBMITTED)


class PeriodPageTests(Base):
    def test_names_only_for_hr_admins_and_never_own(self):
        p = self.period(self.n[0])
        raters.build_assignments(p, self.hra.user)
        url = reverse("performance:period", args=[p.pk])
        self.client.force_login(self.hra.user)
        self.assertContains(self.client.get(url), self.erh.full_name)
        self.client.force_login(self.hrp.user)
        r = self.client.get(url)
        self.assertNotContains(r, self.erh.full_name)
        self.assertContains(r, "Counts")
        own = self.period(self.hra, track=P.RANK)
        self.client.force_login(self.hra.user)
        self.assertEqual(self.client.get(reverse("performance:period", args=[own.pk])).status_code, 403)
        self.client.force_login(self.n[0].user)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_setup_and_periods_pages(self):
        self.client.force_login(self.hrp.user)
        self.assertContains(self.client.get(reverse("performance:setup")), "Rank-and-file weight")
        self.assertContains(self.client.get(reverse("performance:periods")), "Test window")
        self.client.force_login(self.n[0].user)
        self.assertEqual(self.client.get(reverse("performance:setup")).status_code, 403)
