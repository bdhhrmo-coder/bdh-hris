"""Date change of approved leave and CTO, and cancelling a pending
application (Part 2, owner decisions 2026-11-19)."""

from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from auditlog.aggregation import audit_rows
from cto.balances import compute_available_cto_balance
from cto.models import CTOUsageApplication
from cto.tests import Org, apply_cto, make_employee, on, opening_credit
from leave.balances import compute_available_balance
from leave.models import LeaveApplication, LeaveCreditTransaction, LeaveType
from notifications.models import Notification

from .models import DateChangeRequest as DC
from .rules import next_step

TODAY = date(2026, 11, 19)  # a Thursday
L = LeaveApplication


class Base(TestCase):
    def setUp(self):
        self.org = Org()
        self.client = Client()
        self.cosp = make_employee("cospemp", "E-C1", section=self.org.lab)
        self.cosp.employment_status = "COSP"
        self.cosp.date_hired = date(2025, 3, 1)  # contract year Mar 1, 2026 - Feb 28, 2027
        self.cosp.save()
        self.reg = self.org.lab_staff
        self.cosp_leave = LeaveType.objects.get(code="COSP_LEAVE")
        self.vl = LeaveType.objects.get(code="VL")

    def leave(self, emp, leave_type, start, end, days, status, take_days=True):
        app = L.objects.create(employee=emp, leave_type=leave_type, start_date=start, end_date=end,
                               number_of_days=Decimal(days), status=L.SUBMITTED)  # route fixed on submit
        app.status = status
        app.save()
        if take_days and status in (L.APPROVED, L.FOR_CAPITOL_APPROVAL):
            LeaveCreditTransaction.objects.create(
                employee=emp, leave_type=leave_type, transaction_type=LeaveCreditTransaction.USED,
                days=-Decimal(days), transaction_date=start, leave_application=app, created_by=emp.user)
        return app

    def file(self, emp, kind, target, start, end, why="Family event moved", today=TODAY):
        self.client.force_login(emp.user)
        with on(today):
            return self.client.post(reverse("datechange:new", args=[kind, target.pk]),
                                    {"new_start_date": start, "new_end_date": end, "justification": why})

    def error_of(self, response):
        if response.status_code == 302:
            return None
        return " ".join(str(e) for e in response.context["form"].non_field_errors())

    def people(self):
        o = self.org
        return [o.lab_sup, o.chief_nurse, o.er_head, o.hr, o.hr_admin, o.ao, o.coh]

    def act(self, change, action=None, notes="", today=TODAY):
        """One step by the first person who may act on it now."""
        change.refresh_from_db()
        actor = next(p for p in self.people() if next_step(change, p) is not None)
        self.client.force_login(actor.user)
        with on(today):
            r = self.client.post(reverse("datechange:action", args=[change.pk]),
                                 {"action": action or next_step(change, actor)[0], "notes": notes})
        change.refresh_from_db()
        return actor, r

    def walk(self, change, today=TODAY):
        steps = []
        while change.is_open:
            before = change.status
            actor, r = self.act(change, today=today)
            if change.status == before:
                self.fail(f"Step stuck at {before}: {[str(m) for m in r.wsgi_request._messages]}")
            steps.append((actor, change.status))
        return steps


class FilingRuleTests(Base):
    def test_same_days_rule_blocks_dates_too_short(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 3), "3", L.APPROVED)
        r = self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-09")
        self.assertIn("too short for 3 day(s)", self.error_of(r))
        self.assertFalse(DC.objects.exists())

    def test_overlap_with_other_leave_or_cto_is_refused(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        self.leave(self.cosp, self.cosp_leave, date(2026, 12, 9), date(2026, 12, 9), "1", L.SUBMITTED)
        r = self.file(self.cosp, "leave", app, "2026-12-09", "2026-12-09")
        self.assertIn("overlap your COSP Leave", self.error_of(r))
        CTOUsageApplication.objects.create(employee=self.cosp, start_date=date(2026, 12, 10),
                                           end_date=date(2026, 12, 10), number_of_days=1, status="APPROVED")
        r = self.file(self.cosp, "leave", app, "2026-12-10", "2026-12-10")
        self.assertIn("overlap your CTO", self.error_of(r))

    def test_started_or_passed_request_is_refused(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 11, 19), date(2026, 11, 20), "2", L.APPROVED)
        r = self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-09")
        self.assertRedirects(r, reverse("leave:leave_detail", args=[app.pk]), fetch_redirect_response=False)
        self.assertFalse(DC.objects.exists())

    def test_pending_request_is_refused(self):
        for status in (L.SUBMITTED, L.ENDORSED_BY_SUPERVISOR, L.RECOMMENDED_BY_AO, L.RETURNED):
            app = self.leave(self.reg, self.vl, date(2026, 12, 1), date(2026, 12, 1), "1", status)
            self.file(self.reg, "leave", app, "2026-12-08", "2026-12-08")
        self.assertFalse(DC.objects.exists())

    def test_only_the_owner_can_file(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        for other in (self.reg, self.org.hr_admin, self.org.lab_sup):
            self.file(other, "leave", app, "2026-12-08", "2026-12-08")
        self.assertFalse(DC.objects.exists())

    def test_one_pending_change_at_a_time(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-08")
        self.file(self.cosp, "leave", app, "2026-12-09", "2026-12-09")
        self.assertEqual(DC.objects.count(), 1)

    def test_new_dates_stay_in_the_same_year(self):
        app = self.leave(self.reg, self.vl, date(2026, 12, 28), date(2026, 12, 28), "1", L.FOR_CAPITOL_APPROVAL)
        r = self.file(self.reg, "leave", app, "2027-01-05", "2027-01-05")
        self.assertIn("same year", self.error_of(r))

    def test_cosp_leave_stays_in_the_same_contract_year(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2027, 2, 22), date(2027, 2, 22), "1", L.APPROVED)
        r = self.file(self.cosp, "leave", app, "2027-03-02", "2027-03-02")
        self.assertIn("same contract year", self.error_of(r))

    def test_justification_is_required(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-08", why="   ")
        self.assertFalse(DC.objects.exists())

    def test_button_shows_only_for_owner_of_approved_future_request(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        url = reverse("datechange:new", args=["leave", app.pk])
        self.client.force_login(self.cosp.user)
        with on(TODAY):
            self.assertContains(self.client.get(reverse("leave:leave_detail", args=[app.pk])), url)
        self.client.force_login(self.org.hr_admin.user)
        with on(TODAY):
            self.assertNotContains(self.client.get(reverse("leave:leave_detail", args=[app.pk])), url)


class RoutingAndBalanceTests(Base):
    def test_cosp_leave_full_route_coh_approves_and_balance_unchanged(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 2), "2", L.APPROVED)
        before = compute_available_balance(self.cosp, self.cosp_leave, date(2026, 12, 31))
        self.file(self.cosp, "leave", app, "2026-12-09", "2026-12-10")
        change = DC.objects.get()
        steps = self.walk(change)
        o = self.org
        self.assertEqual([a for a, _ in steps], [o.lab_sup, o.hr, o.ao, o.coh])
        self.assertEqual(change.status, DC.APPLIED)
        app.refresh_from_db()
        self.assertEqual((app.start_date, app.end_date), (date(2026, 12, 9), date(2026, 12, 10)))
        self.assertEqual(app.status, L.APPROVED)
        self.assertEqual(app.number_of_days, Decimal("2"))
        self.assertEqual(compute_available_balance(self.cosp, self.cosp_leave, date(2026, 12, 31)), before)
        self.assertEqual(LeaveCreditTransaction.objects.filter(leave_application=app).count(), 1)

    def test_original_dates_kept_while_pending(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-08")
        change = DC.objects.get()
        for _ in range(3):  # Supervisor, HR, AO
            self.act(change)
            app.refresh_from_db()
            self.assertEqual(app.start_date, date(2026, 12, 1))
        self.assertEqual(change.status, DC.RECOMMENDED_BY_AO)

    def test_rejection_keeps_dates_and_balance(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        before = compute_available_balance(self.cosp, self.cosp_leave, date(2026, 12, 31))
        self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-08")
        change = DC.objects.get()
        self.act(change)  # endorsed
        _, r = self.act(change, action="reject")  # no remark
        self.assertEqual(change.status, DC.ENDORSED_BY_SUPERVISOR)
        self.act(change, action="reject", notes="Short-staffed that week")
        self.assertEqual(change.status, DC.REJECTED)
        app.refresh_from_db()
        self.assertEqual(app.start_date, date(2026, 12, 1))
        self.assertEqual(compute_available_balance(self.cosp, self.cosp_leave, date(2026, 12, 31)), before)
        note = Notification.objects.filter(recipient=self.cosp).latest("pk")
        self.assertIn("Short-staffed", note.message)
        self.assertEqual(note.url, f"/date-changes/{change.pk}/")
        # A new change can be asked for after a rejection.
        self.file(self.cosp, "leave", app, "2026-12-09", "2026-12-09")
        self.assertEqual(DC.objects.count(), 2)

    def test_regular_leave_ends_with_coh_recommendation_and_hr_cannot_reject(self):
        app = self.leave(self.reg, self.vl, date(2026, 12, 1), date(2026, 12, 1), "1", L.FOR_CAPITOL_APPROVAL)
        self.file(self.reg, "leave", app, "2026-12-08", "2026-12-08")
        change = DC.objects.get()
        self.act(change)  # Supervisor
        self.assertEqual(change.status, DC.ENDORSED_BY_SUPERVISOR)
        self.act(change, action="reject", notes="no")  # HR may not reject regular leave
        self.assertEqual(change.status, DC.ENDORSED_BY_SUPERVISOR)
        steps = self.walk(change)
        self.assertEqual(steps[-1][0], self.org.coh)
        self.assertEqual(change.actions.last().action, "recommend_capitol")
        app.refresh_from_db()
        self.assertEqual((app.start_date, app.status), (date(2026, 12, 8), L.FOR_CAPITOL_APPROVAL))
        self.assertIn("Provincial Capitol", Notification.objects.filter(recipient=self.reg).latest("pk").message)
        self.client.force_login(self.reg.user)
        self.assertContains(self.client.get(reverse("leave:leave_detail", args=[app.pk])),
                            "send it to the Provincial Capitol")

    def test_nursing_two_step_route(self):
        nurse = self.org.nurse
        app = self.leave(nurse, self.vl, date(2026, 12, 1), date(2026, 12, 1), "1", L.FOR_CAPITOL_APPROVAL)
        self.file(nurse, "leave", app, "2026-12-08", "2026-12-08")
        steps = self.walk(DC.objects.get())
        o = self.org
        self.assertEqual([a for a, _ in steps], [o.er_head, o.chief_nurse, o.hr, o.ao, o.coh])
        self.assertEqual(steps[0][1], DC.ENDORSED_BY_UNIT_HEAD)

    def test_final_step_rechecks_and_blocks_once_original_started(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 11, 30), date(2026, 11, 30), "1", L.APPROVED)
        self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-08")
        change = DC.objects.get()
        for _ in range(3):
            self.act(change)
        self.act(change, today=date(2026, 11, 30))  # COH, on the original date
        self.assertEqual(change.status, DC.RECOMMENDED_BY_AO)
        app.refresh_from_db()
        self.assertEqual(app.start_date, date(2026, 11, 30))

    def test_audit_log_and_history_record_the_change(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-08", why="Daughter's graduation moved")
        change = DC.objects.get()
        self.walk(change)
        rows = [r for r in audit_rows() if r["module"] == "Date Change"]
        self.assertEqual(len(rows), 5)  # requested + 4 steps
        self.assertTrue(any("Daughter's graduation" in r["detail"] and "Dec 01, 2026 -> Dec 08, 2026" in r["detail"]
                            for r in rows))
        self.client.force_login(self.cosp.user)
        page = self.client.get(reverse("leave:leave_detail", args=[app.pk]))
        self.assertContains(page, "Date changes")
        self.assertContains(page, reverse("datechange:detail", args=[change.pk]))

    def test_printed_form_follows_the_record(self):
        """No separate print data: the form reads the leave record, which
        keeps the approved dates until the change is applied."""
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-08")
        change = DC.objects.get()
        app.refresh_from_db()
        self.assertEqual(app.dates_label, "Dec 01, 2026")
        self.walk(change)
        app.refresh_from_db()
        self.assertEqual(app.dates_label, "Dec 08, 2026")


class CTODateChangeTests(Base):
    def setUp(self):
        super().setUp()
        self.emp = self.org.lab_staff
        opening_credit(self.org, self.emp, days="10", earned=date(2026, 6, 1), entered=date(2026, 11, 10))

    def cto(self, start, end, days, status=CTOUsageApplication.APPROVED):
        apply_cto(self.emp, start, end, days, TODAY)
        app = CTOUsageApplication.objects.filter(employee=self.emp).latest("pk")
        app.status = status
        app.save()
        return app

    def test_cto_balance_unchanged_after_approval_and_credit_kept(self):
        app = self.cto("2026-11-24", "2026-11-24", "1")
        held = list(app.allocations.values_list("line_id", "days"))
        before = compute_available_cto_balance(self.emp, TODAY)
        self.file(self.emp, "cto", app, "2026-12-01", "2026-12-01")
        change = DC.objects.get()
        steps = self.walk(change)
        self.assertEqual(steps[-1][0], self.org.coh)
        app.refresh_from_db()
        self.assertEqual((app.start_date, app.status), (date(2026, 12, 1), CTOUsageApplication.APPROVED))
        self.assertEqual(list(app.allocations.values_list("line_id", "days")), held)
        self.assertEqual(compute_available_cto_balance(self.emp, TODAY), before)

    def test_cto_monthly_cap(self):
        app = self.cto("2026-11-24", "2026-11-24", "1")
        self.cto("2026-12-01", "2026-12-02", "2")
        self.cto("2026-12-08", "2026-12-09", "2")
        self.cto("2026-12-14", "2026-12-14", "1")
        self.assertEqual(CTOUsageApplication.objects.filter(start_date__month=12).count(), 3)
        r = self.file(self.emp, "cto", app, "2026-12-11", "2026-12-11")
        self.assertIn("5-CTO-day-per-month", self.error_of(r))

    def test_cto_no_three_consecutive_days(self):
        app = self.cto("2026-11-24", "2026-11-24", "1")
        self.cto("2026-12-02", "2026-12-03", "2")
        r = self.file(self.emp, "cto", app, "2026-12-04", "2026-12-04")
        self.assertIn("3 or more consecutive days", self.error_of(r))

    def test_cto_credit_deadline_is_never_extended(self):
        app = self.cto("2026-11-24", "2026-11-24", "1")
        r = self.file(self.emp, "cto", app, "2026-12-16", "2026-12-16")
        self.assertIn("must be used by December 15, 2026", self.error_of(r))

    def test_cto_balance_unchanged_after_return(self):
        app = self.cto("2026-11-24", "2026-11-24", "1")
        before = compute_available_cto_balance(self.emp, TODAY)
        self.file(self.emp, "cto", app, "2026-12-01", "2026-12-01")
        change = DC.objects.get()
        self.act(change, action="return", notes="Check the schedule first")
        self.assertEqual(change.status, DC.RETURNED)
        app.refresh_from_db()
        self.assertEqual(app.start_date, date(2026, 11, 24))
        self.assertEqual(compute_available_cto_balance(self.emp, TODAY), before)


class CancelPendingTests(Base):
    def test_employee_cancels_pending_regular_leave_and_days_come_back(self):
        app = self.leave(self.reg, self.vl, date(2026, 12, 1), date(2026, 12, 1), "1", L.SUBMITTED)
        app.status = L.PROCESSED_BY_HR  # HR recorded it: the day is taken
        app.save()
        LeaveCreditTransaction.objects.create(employee=self.reg, leave_type=self.vl,
                                              transaction_type=LeaveCreditTransaction.USED, days=-1,
                                              transaction_date=date(2026, 12, 1), leave_application=app,
                                              created_by=self.reg.user)
        before = compute_available_balance(self.reg, self.vl, date(2026, 12, 31))
        self.client.force_login(self.reg.user)
        self.client.post(reverse("leave:leave_cancel", args=[app.pk]))
        app.refresh_from_db()
        self.assertEqual(app.status, L.CANCELLED)
        self.assertEqual(compute_available_balance(self.reg, self.vl, date(2026, 12, 31)), before + 1)
        self.assertEqual(app.actions.last().action, "cancel")
        self.assertTrue(any(r["action"] == "Cancelled by the filer" for r in audit_rows() if r["module"] == "Leave"))

    def test_approved_leave_cannot_be_cancelled_and_others_cannot_cancel(self):
        approved = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        pending = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 8), date(2026, 12, 8), "1", L.SUBMITTED)
        self.client.force_login(self.cosp.user)
        self.client.post(reverse("leave:leave_cancel", args=[approved.pk]))
        self.client.force_login(self.org.hr_admin.user)
        r = self.client.post(reverse("leave:leave_cancel", args=[pending.pk]))
        self.assertEqual(r.status_code, 403)
        approved.refresh_from_db(); pending.refresh_from_db()
        self.assertEqual((approved.status, pending.status), (L.APPROVED, L.SUBMITTED))

    def test_employee_cancels_pending_cto_and_held_credit_is_released(self):
        emp = self.org.lab_staff
        opening_credit(self.org, emp, days="3", earned=date(2026, 6, 1), entered=date(2026, 11, 10))
        apply_cto(emp, "2026-11-24", "2026-11-24", "1", TODAY)
        app = CTOUsageApplication.objects.get()
        self.assertEqual(compute_available_cto_balance(emp, TODAY), Decimal("2"))
        self.client.force_login(emp.user)
        self.client.post(reverse("cto:cto_cancel", args=[app.pk]))
        app.refresh_from_db()
        self.assertEqual(app.status, CTOUsageApplication.CANCELLED)
        self.assertEqual(compute_available_cto_balance(emp, TODAY), Decimal("3"))


class PageTests(Base):
    def test_pages_render(self):
        app = self.leave(self.cosp, self.cosp_leave, date(2026, 12, 1), date(2026, 12, 1), "1", L.APPROVED)
        self.client.force_login(self.cosp.user)
        with on(TODAY):
            r = self.client.get(reverse("datechange:new", args=["leave", app.pk]))
        self.assertContains(r, "Approved dates (now)")
        self.file(self.cosp, "leave", app, "2026-12-08", "2026-12-08")
        change = DC.objects.get()
        self.client.force_login(self.org.lab_sup.user)
        self.assertContains(self.client.get(reverse("leave:leave_queue")), "Date change requests waiting for you")
        self.assertContains(self.client.get(reverse("datechange:detail", args=[change.pk])), "Endorse")
        self.client.force_login(self.org.ao.user)  # sees it, may not act yet
        page = self.client.get(reverse("datechange:detail", args=[change.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'value="recommend"')
        other = make_employee("outsider", "E-OUT", section=self.org.nursing)
        self.client.force_login(other.user)
        self.assertEqual(self.client.get(reverse("datechange:detail", args=[change.pk])).status_code, 403)

    def test_cto_detail_page(self):
        emp = self.org.lab_staff
        opening_credit(self.org, emp, days="3", earned=date(2026, 6, 1), entered=date(2026, 11, 10))
        apply_cto(emp, "2026-11-24", "2026-11-24", "1", TODAY)
        app = CTOUsageApplication.objects.get()
        self.client.force_login(emp.user)
        with on(TODAY):
            page = self.client.get(reverse("cto:cto_detail", args=[app.pk]))
        self.assertContains(page, "Cancel this application")
        self.assertNotContains(page, "Request date change")
        app.status = CTOUsageApplication.APPROVED
        app.save()
        with on(TODAY):
            self.assertContains(self.client.get(reverse("cto:cto_detail", args=[app.pk])), "Request date change")
