"""Pilot Item 6: leave and CTO markers on the duty schedule (made-up data only)."""

from datetime import date
from decimal import Decimal

from django.urls import reverse

from cto.models import CTOUsageApplication
from leave.models import LeaveApplication, LeaveType

from . import services
from .form_f50 import fill_schedule_form
from .leave_marks import duty_on_leave, marks
from .models import ScheduleCell
from .tests import MONTH, YEAR, Base, code, set_days


def D(day):
    return date(YEAR, MONTH, day)


class LeaveMarkTests(Base):
    def setUp(self):
        super().setUp()
        self.s = self.new_schedule()
        vl = LeaveType.objects.get(code="VL")
        self.leave = LeaveApplication.objects.create(
            employee=self.alice, leave_type=vl, start_date=D(5), end_date=D(6), number_of_days=Decimal("2"),
            status=LeaveApplication.FOR_CAPITOL_APPROVAL, route=LeaveApplication.ROUTE_REGULAR)
        self.cto = CTOUsageApplication.objects.create(
            employee=self.bob, start_date=D(9), end_date=D(9), number_of_days=Decimal("1"),
            status=CTOUsageApplication.SUBMITTED)

    def cell(self, emp, day):
        return ScheduleCell.objects.get(row__schedule=self.s, row__employee=emp, date=D(day))

    def test_marks_and_grid(self):
        m = marks(self.s)
        self.assertEqual(m[(self.alice.pk, D(5))], "LV")
        self.assertEqual(m[(self.bob.pk, D(9))], "CTO?")
        self.assertNotIn((self.alice.pk, D(7)), m)
        self.client.force_login(self.sup.user)
        page = self.client.get(reverse("schedules:edit", args=[self.s.pk]))
        self.assertContains(page, "CTO?")
        self.assertContains(page, "approved leave")

    def test_lv_refused_without_request(self):
        row = self.s.rows.get(employee=self.alice)
        set_days(self.s, self.alice, {7: "8-5"})
        refused = []
        services.save_grid(self.s, {(row.pk, D(7)): code("LV").pk, (row.pk, D(5)): code("LV").pk}, refused=refused)
        self.assertEqual(self.cell(self.alice, 7).shift.code, "8-5")  # kept its old value
        self.assertEqual(self.cell(self.alice, 5).shift.code, "LV")   # leave day: allowed
        self.assertEqual(len(refused), 1)
        self.assertIn("Mar 07", refused[0])
        # through the page, the reason is shown
        self.client.force_login(self.sup.user)
        r = self.client.post(reverse("schedules:edit", args=[self.s.pk]),
                             {f"c-{row.pk}-8": code("LV").pk, "then": "save"}, follow=True)
        self.assertContains(r, "needs a leave or CTO request")

    def test_duty_on_leave_warns_but_submits(self):
        set_days(self.s, self.alice, {5: "7A-7P"})
        self.assertEqual([(e, d) for e, d, _c, _m in duty_on_leave(self.s)], [(self.alice, D(5))])
        self.client.force_login(self.sup.user)
        self.assertContains(self.client.get(reverse("schedules:edit", args=[self.s.pk])),
                            "On duty during approved leave or CTO")
        services.submit(self.s, self.sup.user)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, "SUBMITTED")

    def test_printout_shows_approved_marks_and_legend(self):
        set_days(self.s, self.alice, {5: "OFF", 6: None})
        self.cto.status = CTOUsageApplication.APPROVED
        self.cto.save()
        set_days(self.s, self.bob, {9: "8-5"})  # duty shift: printed as the shift
        wb = fill_schedule_form(self.s)
        ws = wb.active
        values = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
        self.assertGreaterEqual(values.count("LV"), 2)
        self.assertNotIn("CTO", values)
        self.assertTrue(any(v.startswith("LV = on approved leave") for v in values))

    def test_date_change_moves_mark(self):
        self.leave.start_date, self.leave.end_date = D(20), D(21)  # what an approved date change does
        self.leave.save()
        m = marks(self.s)
        self.assertNotIn((self.alice.pk, D(5)), m)
        self.assertEqual(m[(self.alice.pk, D(20))], "LV")

    def test_returned_or_cancelled_gives_no_mark(self):
        self.leave.status = LeaveApplication.RETURNED
        self.leave.save()
        self.cto.status = CTOUsageApplication.CANCELLED
        self.cto.save()
        self.assertEqual(marks(self.s), {})
