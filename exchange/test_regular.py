"""Regular Exchange of Duty and the draft duty schedule (owner decisions 2026-11-03):
filed and approved before the schedule is submitted, filled into the draft
and locked there; pending ones block submission; emergency never touches a
draft; supervisors see the approved list."""

from datetime import date

from django.core.exceptions import ValidationError
from django.urls import reverse

from schedules import services
from schedules.models import DutySchedule as S
from schedules.models import ScheduleCell
from schedules.tests import MONTH, YEAR, Base, code

from .models import DutyExchangeRequest as X
from .regular import kind_for, locked_cells

D10, D11 = date(YEAR, MONTH, 10), date(YEAR, MONTH, 11)


class RegularExchangeTests(Base):
    def file(self, who=None, partner=None, **extra):
        self.client.force_login((who or self.alice).user)
        data = {"employee_b": (partner or self.bob).pk, "date_a": D10, "date_b": D11, "reason": "family",
                "shift_a": code("7A-7P").pk, "shift_b": code("7P-7A").pk}
        data.update(extra)
        return self.client.post(reverse("exchange:exchange_apply"), data)

    def approve(self, x):
        X.objects.filter(pk=x.pk).update(status=X.RECOMMENDED_BY_AO)
        self.client.force_login(self.coh.user)
        self.client.post(reverse("exchange:exchange_action", args=[x.pk]), {"action": "approve"})
        x.refresh_from_db()
        return x

    def cell(self, schedule, emp, day):
        return ScheduleCell.objects.get(row__schedule=schedule, row__employee=emp, date=day).shift

    def test_no_schedule_yet_is_regular_and_needs_shifts(self):
        self.file(shift_a="", shift_b="")
        self.assertFalse(X.objects.exists())
        self.file()
        x = X.objects.get()
        self.assertEqual((x.kind, x.shift_a.code, x.shift_b.code), (X.KIND_REGULAR, "7A-7P", "7P-7A"))

    def test_kind_follows_the_schedule(self):
        self.assertEqual(kind_for(self.alice, D10, self.bob, D11, False)[0], X.KIND_REGULAR)
        s = self.new_schedule()
        self.assertEqual(kind_for(self.alice, D10, self.bob, D11, False)[0], X.KIND_REGULAR)  # draft
        services.submit(s, self.sup.user)
        kind, errors = kind_for(self.alice, D10, self.bob, D11, False)
        self.assertIsNone(kind)
        self.assertIn("under review", errors[0])
        self.assertEqual(kind_for(self.alice, D10, self.bob, D11, True)[0], X.KIND_EMERGENCY)
        self.route_to(s, S.APPROVED)
        self.assertEqual(kind_for(self.alice, D10, self.bob, D11, False)[0], X.KIND_MODIFICATION)

    def test_regular_filing_closes_seven_days_before_cutoff(self):
        from .regular import regular_errors

        errs = regular_errors(self.alice, D10, self.bob, D11, code("7A-7P"), code("7P-7A"), date(2027, 2, 14))
        self.assertTrue(any("had to be filed by Feb 13, 2027" in e for e in errs))
        self.assertEqual(regular_errors(self.alice, D10, self.bob, D11, code("7A-7P"), code("7P-7A"),
                                        date(2027, 2, 13)), [])

    def test_approval_fills_the_draft_and_locks_those_days(self):
        s = self.new_schedule()
        self.file()
        x = self.approve(X.objects.get())
        self.assertEqual(x.status, X.APPROVED)
        self.assertEqual([self.cell(s, e, d).code for e, d in
                          ((self.alice, D10), (self.bob, D10), (self.bob, D11), (self.alice, D11))],
                         ["OFF", "7A-7P", "OFF", "7P-7A"])
        row = s.rows.get(employee=self.alice)
        services.save_grid(s, {(row.pk, D10): code("8-5").pk})   # supervisor tries to change it
        self.assertEqual(self.cell(s, self.alice, D10).code, "OFF")
        self.assertEqual(len(locked_cells(s)), 4)

    def test_draft_created_after_approval_is_filled_in(self):
        self.file()
        self.approve(X.objects.get())
        s = self.new_schedule()
        self.assertEqual(self.cell(s, self.bob, D10).code, "7A-7P")
        self.client.force_login(self.sup.user)
        page = self.client.get(reverse("schedules:edit", args=[s.pk]))
        self.assertContains(page, "Approved Exchanges of Duty this month")

    def test_pending_regular_exchange_blocks_submission(self):
        s = self.new_schedule()
        self.file()
        x = X.objects.get()
        X.objects.filter(pk=x.pk).update(status=X.ENDORSED_BY_SUPERVISOR)
        with self.assertRaises(ValidationError):
            services.submit(s, self.sup.user)
        self.approve(x)
        services.submit(s, self.sup.user)
        s.refresh_from_db()
        self.assertEqual(s.status, S.SUBMITTED)

    def test_unconsented_regular_exchange_is_cancelled_at_submission(self):
        s = self.new_schedule()
        self.file()
        services.submit(s, self.sup.user)
        self.assertEqual(X.objects.get().status, X.CANCELLED)

    def test_emergency_never_touches_a_draft(self):
        s = self.new_schedule()
        x = X.objects.create(employee_a=self.alice, date_a=D10, employee_b=self.bob, date_b=D11,
                             kind=X.KIND_EMERGENCY, is_emergency=True, emergency_justification="Sick child",
                             shift_a=code("7A-7P"), shift_b=code("7P-7A"))
        self.approve(x)
        self.assertEqual(x.status, X.APPROVED)
        self.assertIsNone(self.cell(s, self.alice, D10))   # draft untouched
        self.assertEqual(locked_cells(s), {})

    def test_same_person_cannot_be_on_two_exchanges_for_one_date(self):
        self.file()
        self.file(partner=self.bob, date_a=D10, date_b=date(YEAR, MONTH, 12))
        self.assertEqual(X.objects.count(), 1)

    def test_supervisor_sees_approved_list(self):
        self.file()
        self.approve(X.objects.get())
        self.client.force_login(self.sup.user)
        page = self.client.get(reverse("exchange:approved_exchanges") + f"?y={YEAR}&m={MONTH}")
        self.assertContains(page, self.alice.full_name)
        self.client.force_login(self.outsider.user)
        self.assertEqual(self.client.get(reverse("exchange:approved_exchanges")).status_code, 403)
