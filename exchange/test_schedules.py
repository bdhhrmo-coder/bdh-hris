"""Batch 5 item 5.2: Exchange of Duty checks against the duty schedules."""

from datetime import date, timedelta

from django.urls import reverse

from schedules import services
from schedules.models import DutySchedule as S
from schedules.models import ScheduleChange
from schedules.tests import MONTH, YEAR, Base, code, set_days

from .models import DutyExchangeRequest
from .schedule_checks import check


class ExchangeScheduleTests(Base):
    """5.2: same position, both dates duty days, partner off, cut-off rule."""

    def setUp(self):
        super().setUp()
        self.s = self.new_schedule()
        set_days(self.s, self.alice, {10: "7A-7P", 11: "OFF"})
        set_days(self.s, self.bob, {10: "OFF", 11: "7P-7A"})
        self.d10, self.d11 = date(YEAR, MONTH, 10), date(YEAR, MONTH, 11)

    def test_valid_swap_before_cutoff(self):
        services.submit(self.s, self.sup.user)
        errors, notes = check(self.alice, self.d10, self.bob, self.d11, date(2027, 2, 1), False)
        self.assertEqual(errors, [])

    def test_draft_schedule_is_not_checked(self):
        errors, notes = check(self.alice, self.d10, self.bob, self.d11, date(2027, 2, 1), False)
        self.assertEqual(errors, [])
        self.assertTrue(notes)  # told it was not checked

    def test_position_must_match(self):
        services.submit(self.s, self.sup.user)
        errors, _ = check(self.alice, self.d10, self.carl, self.d11, date(2027, 2, 1), False)
        self.assertTrue(any("same position" in e for e in errors))

    def test_both_dates_must_be_duty_days_and_partner_off(self):
        services.submit(self.s, self.sup.user)
        errors, _ = check(self.alice, self.d11, self.bob, self.d10, date(2027, 2, 1), False)
        self.assertTrue(any("not on duty" in e for e in errors))
        set_days(self.s, self.bob, {10: "8-5"})
        errors, _ = check(self.alice, self.d10, self.bob, self.d11, date(2027, 2, 1), False)
        self.assertTrue(any("two shifts" in e for e in errors))

    def test_cutoff_rule_before_approval_and_24h_after(self):
        services.submit(self.s, self.sup.user)
        late = self.s.cutoff_date - timedelta(days=6)   # 6 days before cut-off: too late
        errors, _ = check(self.alice, self.d10, self.bob, self.d11, late, False)
        self.assertTrue(any("cut-off" in e for e in errors))
        on_time = self.s.cutoff_date - timedelta(days=7)
        self.assertEqual(check(self.alice, self.d10, self.bob, self.d11, on_time, False)[0], [])
        self.assertEqual(check(self.alice, self.d10, self.bob, self.d11, late, True)[0], [])  # emergency
        self.route_to(self.s, S.APPROVED)
        self.assertEqual(check(self.alice, self.d10, self.bob, self.d11, late, False)[0], [])

    def test_coh_approval_writes_swap_into_schedule(self):
        self.route_to(self.s, S.RECORDED)
        req = DutyExchangeRequest.objects.create(employee_a=self.alice, date_a=self.d10, employee_b=self.bob,
                                                 date_b=self.d11, status=DutyExchangeRequest.RECOMMENDED_BY_AO)
        self.client.force_login(self.coh.user)
        self.client.post(reverse("exchange:exchange_action", args=[req.pk]), {"action": "approve"})
        req.refresh_from_db()
        self.assertEqual(req.status, DutyExchangeRequest.APPROVED)
        _, a10 = services.cell_for(self.alice, self.d10)
        _, b10 = services.cell_for(self.bob, self.d10)
        _, a11 = services.cell_for(self.alice, self.d11)
        _, b11 = services.cell_for(self.bob, self.d11)
        self.assertEqual((a10.shift.code, b10.shift.code, a11.shift.code, b11.shift.code),
                         ("OFF", "7A-7P", "7P-7A", "OFF"))
        self.assertEqual(ScheduleChange.objects.filter(exchange=req).count(), 4)  # originals kept

    def test_coh_approval_stops_if_schedule_changed(self):
        self.route_to(self.s, S.RECORDED)
        req = DutyExchangeRequest.objects.create(employee_a=self.alice, date_a=self.d10, employee_b=self.bob,
                                                 date_b=self.d11, status=DutyExchangeRequest.RECOMMENDED_BY_AO)
        services.hr_correction(self.s, self.alice, self.d10, code("OFF"), "Sick", self.hr.user)
        self.client.force_login(self.coh.user)
        self.client.post(reverse("exchange:exchange_action", args=[req.pk]), {"action": "approve"})
        req.refresh_from_db()
        self.assertEqual(req.status, DutyExchangeRequest.RECOMMENDED_BY_AO)
        self.assertFalse(ScheduleChange.objects.filter(exchange=req).exists())
