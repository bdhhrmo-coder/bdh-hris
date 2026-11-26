"""Pilot Item 6: leave and CTO markers on the duty schedule (made-up data only)."""

from datetime import date
from decimal import Decimal

from django.urls import reverse

from cto.models import CTOUsageApplication
from leave.models import LeaveApplication, LeaveType

from . import services
from .form_f50 import fill_schedule_form
from .leave_marks import CTO, CTO_PENDING, LV, LV_PENDING, duty_on_leave, marks, shown
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
        self.assertEqual(m[(self.alice.pk, D(5))], LV)
        self.assertEqual(m[(self.bob.pk, D(9))], CTO_PENDING)
        self.assertNotIn((self.alice.pk, D(7)), m)
        self.assertEqual(shown(m)[(self.bob.pk, D(9))], "CTO")  # pending CTO shows as CTO
        self.client.force_login(self.sup.user)
        page = self.client.get(reverse("schedules:edit", args=[self.s.pk]))
        self.assertContains(page, "approved leave")
        self.assertNotContains(page, "CTO?")
        self.assertNotContains(page, "LV?")

    def test_pending_leave_shows_nothing_but_allows_lv(self):
        vl = LeaveType.objects.get(code="VL")
        LeaveApplication.objects.create(employee=self.bob, leave_type=vl, start_date=D(15), end_date=D(15),
                                        number_of_days=Decimal("1"), status=LeaveApplication.SUBMITTED,
                                        route=LeaveApplication.ROUTE_REGULAR)
        m = marks(self.s)
        self.assertEqual(m[(self.bob.pk, D(15))], LV_PENDING)
        self.assertNotIn((self.bob.pk, D(15)), shown(m))
        row = self.s.rows.get(employee=self.bob)
        services.save_grid(self.s, {(row.pk, D(15)): code("LV").pk})
        self.assertEqual(self.cell(self.bob, 15).shift.code, "LV")

    def test_lv_refused_without_request(self):
        row = self.s.rows.get(employee=self.alice)
        set_days(self.s, self.alice, {7: "8-5"})
        refused = []
        services.save_grid(self.s, {(row.pk, D(7)): code("LV").pk, (row.pk, D(5)): code("LV").pk,
                                    (row.pk, D(8)): code("CTO").pk}, refused=refused)
        self.assertEqual(self.cell(self.alice, 7).shift.code, "8-5")  # kept its old value
        self.assertEqual(self.cell(self.alice, 5).shift.code, "LV")   # leave day: allowed
        self.assertEqual(len(refused), 2)  # LV on the 7th, CTO on the 8th
        self.assertIn("Mar 07", refused[0])
        # through the page, the reason is shown
        self.client.force_login(self.sup.user)
        r = self.client.post(reverse("schedules:edit", args=[self.s.pk]),
                             {f"c-{row.pk}-8": code("LV").pk, "then": "save"}, follow=True)
        self.assertContains(r, "needs a leave or CTO request")

    def test_duty_on_leave_warns_but_submits(self):
        set_days(self.s, self.alice, {5: "7A-7P"})
        set_days(self.s, self.bob, {9: "8-5"})  # pending CTO: no warning
        self.assertEqual([(e, d) for e, d, _c, _m in duty_on_leave(self.s)], [(self.alice, D(5))])
        self.client.force_login(self.sup.user)
        self.assertContains(self.client.get(reverse("schedules:edit", args=[self.s.pk])),
                            "On duty during approved leave or CTO")
        services.submit(self.s, self.sup.user)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, "SUBMITTED")

    def test_printout_shows_marks_and_legend(self):
        set_days(self.s, self.alice, {5: "OFF", 6: None})
        set_days(self.s, self.bob, {9: "OFF"})  # pending CTO on an OFF day: printed as CTO
        ws = fill_schedule_form(self.s).active
        values = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
        self.assertGreaterEqual(values.count("LV"), 2)
        self.assertEqual(values.count("CTO"), 1)
        self.assertFalse(any("?" in v for v in values if v in ("LV?", "CTO?")))
        self.assertTrue(any(v.startswith("LV = on approved leave") for v in values))
        set_days(self.s, self.bob, {9: "8-5"})  # a duty shift is printed as the shift
        ws = fill_schedule_form(self.s).active
        values = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
        self.assertNotIn("CTO", values)

    def test_date_change_moves_mark(self):
        self.leave.start_date, self.leave.end_date = D(20), D(21)  # what an approved date change does
        self.leave.save()
        m = marks(self.s)
        self.assertNotIn((self.alice.pk, D(5)), m)
        self.assertEqual(m[(self.alice.pk, D(20))], LV)

    def test_returned_or_cancelled_gives_no_mark(self):
        self.leave.status = LeaveApplication.RETURNED
        self.leave.save()
        self.cto.status = CTOUsageApplication.CANCELLED
        self.cto.save()
        self.assertEqual(marks(self.s), {})


class ExcelUploadLeaveCodeTests(Base):
    def test_refused_lv_cells_are_listed(self):
        from io import BytesIO

        from .excel import build_template, import_workbook

        s = self.new_schedule()
        wb = build_template(s)
        ws = wb["Schedule"]
        header = next(r for r in range(1, 20) if str(ws.cell(row=r, column=1).value or "").upper() == "EMPLOYEE ID")
        col = next(c for c in range(4, ws.max_column + 1) if ws.cell(row=header, column=c).value == 7)
        row = next(r for r in range(header + 1, ws.max_row + 1)
                   if str(ws.cell(row=r, column=1).value or "").upper() == self.alice.employee_id.upper())
        ws.cell(row=row, column=col, value="LV")
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        _filled, problems = import_workbook(s, buf)
        self.assertTrue(any("needs a leave or CTO request" in p for p in problems), problems)
        self.assertNotEqual(getattr(ScheduleCell.objects.get(row__schedule=s, row__employee=self.alice,
                                                             date=D(7)).shift, "code", ""), "LV")
