"""Leave and CTO balances in the standard look (owner request 2026-11-14): the parts must add up
to the existing balance, and pending figures must follow each module's own rules."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from cto.models import CTOClaim, CTOClaimLine, CTOCreditAllocation, CTOUsageApplication
from employees.models import Employee
from orgstructure.models import Section

from .balance_panels import cto_panel, leave_breakdown, leave_panels
from .balances import compute_available_balance
from .models import LeaveApplication, LeaveCreditTransaction as T, LeaveType

TODAY = date(2026, 11, 14)
D = Decimal


def emp(username, status="REGULAR", hired=date(2024, 1, 1)):
    u = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=u, employee_id=username.upper(), surname=username.title(), first_name="T",
                                employment_status=status, date_hired=hired)
    e.sections.add(Section.objects.get(name="Laboratory Section"))
    return e


class LeaveBreakdownTests(TestCase):
    def setUp(self):
        self.e = emp("staff")
        self.vl = LeaveType.objects.get(code="VL")
        self.wl = LeaveType.objects.get(code="WELLNESS")

    def ledger(self, lt, kind, days, when):
        T.objects.create(employee=self.e, leave_type=lt, transaction_type=kind, days=D(days), transaction_date=when,
                         created_by=self.e.user)

    def test_parts_add_up_to_the_existing_balance(self):
        self.ledger(self.vl, T.USED, "-3", date(2025, 6, 2))
        self.ledger(self.vl, T.USED, "-2", date(2026, 3, 9))
        self.ledger(self.vl, T.ADJUSTMENT, "-0.5", date(2026, 4, 1))
        b = leave_breakdown(self.e, self.vl, TODAY)
        self.assertEqual(b["available"], compute_available_balance(self.e, self.vl, TODAY))
        self.assertEqual(b["beginning"] + b["earned"] - b["used"] + b["other"], b["available"])
        self.assertEqual(b["used"], D("2"))
        from .balances import months_of_service
        months = months_of_service(self.e.date_hired, TODAY) - months_of_service(self.e.date_hired, date(2026, 1, 1))
        self.assertEqual(b["earned"], D("1.25") * months)  # whatever the existing accrual rule grants

    def test_non_cumulative_starts_fresh(self):
        self.ledger(self.wl, T.USED, "-1", date(2026, 2, 2))
        b = leave_breakdown(self.e, self.wl, TODAY)
        self.assertEqual((b["beginning"], b["earned"], b["used"], b["available"]), (0, D("5"), D("1"), D("4")))

    def test_pending_shown_but_not_deducted(self):
        LeaveApplication.objects.create(employee=self.e, leave_type=self.vl, start_date=date(2026, 12, 1),
                                        end_date=date(2026, 12, 2), number_of_days=D("2"),
                                        status=LeaveApplication.SUBMITTED, route=LeaveApplication.ROUTE_REGULAR)
        vl = next(p for p in leave_panels(self.e, TODAY) if p["name"] == "Vacation Leave")
        available = compute_available_balance(self.e, self.vl, TODAY)
        self.assertIn(("Pending applications", "2 days"), vl["rows"])
        self.assertIn(f"After pending: {(available - 2).normalize():f} days", vl["sub"])

    def test_apply_page_shows_the_cards(self):
        self.client.force_login(self.e.user)
        r = self.client.get(reverse("leave:leave_apply"))
        self.assertContains(r, "Vacation Leave")
        self.assertContains(r, "Beginning balance (Jan 1)")


class CTOPanelTests(TestCase):
    def setUp(self):
        self.e = emp("nurse")
        claim = CTOClaim.objects.create(employee=self.e, is_opening=True, filed_by=self.e.user)
        self.line = CTOClaimLine.objects.create(
            claim=claim, work_date=date(2026, 9, 1), hours_worked=D("0"), day_type="WEEKDAY", shift_hours_used=8,
            multiplier_applied=D("0"), credited_hours=D("0"), credited_days=D("3"), state=CTOClaimLine.CREDITED,
            effective_date=date(2026, 10, 1), year_earned=2026, use_deadline=date(2026, 12, 15))

    def use(self, days, status):
        app = CTOUsageApplication.objects.create(employee=self.e, start_date=date(2026, 11, 20),
                                                 end_date=date(2026, 11, 20), number_of_days=D(days), status=status)
        CTOCreditAllocation.objects.create(line=self.line, usage_application=app, days=D(days))

    def test_days_split_into_used_pending_available(self):
        self.use("1", CTOUsageApplication.APPROVED)
        self.use("0.5", CTOUsageApplication.SUBMITTED)
        p = cto_panel(self.e, TODAY)
        cards = {c["label"]: c["value"] for c in p["cards"]}
        self.assertEqual(cards, {"Total earned credits": "3 days", "Utilized": "1 day",
                                 "Pending applications": "0.5 days"})
        self.assertEqual(p["primary"]["value"], "1.5 days")  # pending already held from filing
        self.assertIn("Dec 15, 2026", p["primary"]["sub"])
        self.assertEqual(p["limit"]["value"], "1.5 days of 5 days")

    def test_my_cto_page(self):
        self.client.force_login(self.e.user)
        self.assertContains(self.client.get(reverse("cto:my_cto")), "Available CTO balance")
