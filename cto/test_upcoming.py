"""Pilot Item 4 (rebuilt 2026-11-26): approved CTO credit that is not usable
yet is shown at once, with the date it becomes usable; the balance itself
follows the unchanged effective-date rule (§7.1). Made-up data only."""

from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from leave.balance_cards import balance_cards
from leave.balance_panels import cto_panel

from . import tests as t
from .balances import compute_available_cto_balance, upcoming_credit, upcoming_text


class UpcomingCreditTests(TestCase):
    def setUp(self):
        self.org = t.Org()
        self.emp = self.org.lab_staff
        RoleAssignment.objects.create(employee=self.org.lab_sup, role=RoleAssignment.SUPERVISOR)

    def credit(self, work_date, hours, approved_on):
        ot = t.approved_ot(self.org, self.emp, work_date, hours=hours, today=work_date)
        claim = t.filed_claim(self.org, self.emp, [(ot, hours, None, None)], work_date)
        with t.on(approved_on):
            t.approve_all(claim, on_approved=t.credit_lines)

    def test_shown_with_its_date_and_the_balance_unchanged(self):
        self.credit(date(2026, 11, 2), "8", date(2026, 11, 10))  # 1 day, usable Dec 1
        today = date(2026, 11, 12)
        self.assertEqual(compute_available_cto_balance(self.emp, today), Decimal("0"))
        self.assertEqual(upcoming_credit(self.emp, today), [(date(2026, 12, 1), Decimal("1.00"))])
        text = upcoming_text(self.emp, today)
        self.assertEqual(text, "1 day, usable from Dec 1, 2026")
        panel = cto_panel(self.emp, today)
        earned = next(c for c in panel["cards"] if c["label"] == "Total earned credits")
        self.assertEqual(earned["value"], "1 day")  # counted as earned at once
        self.assertEqual(earned["note"], "1 day usable from Dec 1, 2026")
        self.assertEqual(panel["primary"]["value"], "0 days")  # available: usable today only
        card = next(c for c in balance_cards(self.emp, today) if c["key"] == "CTO")
        self.assertEqual(card["upcoming"], "Also earned: " + text)
        self.assertEqual(card["remaining"], Decimal("0"))
        self.assertEqual(upcoming_text(self.emp, date(2026, 12, 1)), "")  # it is in the balance now
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 12, 1)), Decimal("1.00"))

    def test_apply_page_and_home_card(self):
        self.credit(date(2026, 11, 2), "8", date(2026, 11, 10))
        c = Client()
        c.force_login(self.emp.user)
        with t.on(date(2026, 11, 12)):
            self.assertContains(c.get(reverse("cto:cto_apply")), "Also earned:")
            self.assertContains(c.get(reverse("cto:cto_apply")), "1 day, usable from Dec 1, 2026")
            self.assertContains(c.get("/hris/home/"), "Also earned: 1 day, usable from Dec 1, 2026")
            self.assertContains(c.get(reverse("cto:my_cto")), "usable from Dec 1, 2026")
