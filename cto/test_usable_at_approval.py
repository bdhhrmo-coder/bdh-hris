"""Owner decision 2026-11-26 (CTO balance, Option B): approved CTO credit is usable from the day
the COH approves the claim. Year earned and the use deadline still follow the month after the
workday, so December work stays next year's CTO and carries over without a reason. Made-up data only."""

from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from . import tests as t
from .balances import compute_available_cto_balance, effective_date_for, upcoming_credit
from .carryover import at_risk_lines, run_year_end
from .models import CTOClaimLine


@override_settings(CTO_CREDIT_USABLE_AT_APPROVAL=True)
class UsableAtApprovalTests(TestCase):
    def setUp(self):
        self.org = t.Org()
        self.emp = self.org.lab_staff

    def credit(self, work_date, approved_on, hours="8"):
        ot = t.approved_ot(self.org, self.emp, work_date, hours=hours, today=work_date)
        claim = t.filed_claim(self.org, self.emp, [(ot, hours, None, None)], work_date)
        with t.on(approved_on):
            t.approve_all(claim, on_approved=t.credit_lines)
        return claim.lines.get()

    def test_usable_the_day_the_coh_approves(self):
        line = self.credit(date(2026, 11, 2), date(2026, 11, 10))
        self.assertEqual(line.effective_date, date(2026, 11, 10))
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 11, 10)), Decimal("1.00"))
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 11, 9)), Decimal("0"))
        self.assertEqual(upcoming_credit(self.emp, date(2026, 11, 10)), [])  # nothing waiting
        self.assertEqual((line.year_earned, line.use_deadline), (2026, date(2026, 12, 15)))

    def test_december_work_is_next_years_cto_and_needs_no_reason(self):
        line = self.credit(date(2026, 12, 3), date(2026, 12, 10))
        self.assertEqual(line.effective_date, date(2026, 12, 10))  # usable in December already
        self.assertEqual((line.year_earned, line.use_deadline), (2027, date(2027, 12, 15)))
        self.assertEqual(at_risk_lines(self.emp, 2026), [])
        run_year_end(date(2026, 12, 17))
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CREDITED)  # not forfeited

    def test_november_work_still_needs_a_reason_after_dec_15(self):
        line = self.credit(date(2026, 11, 20), date(2026, 12, 1))
        self.assertEqual(line.use_deadline, date(2026, 12, 15))
        self.assertEqual([x.pk for x in at_risk_lines(self.emp, 2026)], [line.pk])

    def test_approved_in_january_counts_as_that_year(self):
        line = self.credit(date(2026, 11, 20), date(2027, 1, 5))
        self.assertEqual((line.effective_date, line.year_earned, line.use_deadline),
                         (date(2027, 1, 5), 2027, date(2027, 12, 15)))

    def test_switch_off_keeps_the_earlier_rule(self):
        with override_settings(CTO_CREDIT_USABLE_AT_APPROVAL=False):
            self.assertEqual(effective_date_for(date(2026, 11, 2), date(2026, 11, 10)), date(2026, 12, 1))
        self.assertEqual(effective_date_for(date(2026, 11, 2), date(2026, 11, 10)), date(2026, 11, 10))
