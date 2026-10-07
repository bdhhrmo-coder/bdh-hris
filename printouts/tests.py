"""Tests for the digital stamps and the printable forms (codes, stamps, who
may print). PDF conversion itself is mocked: it needs LibreOffice and is
covered by leave/test_pdf_convert.py."""

from datetime import date, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment
from attendance.models import AttendanceCorrectionRequest, AttendanceCorrectionRequestAction
from cto.models import CTOUsageApplication, CTOUsageApplicationAction
from employees.models import Employee
from exchange.models import DutyExchangeRequest, DutyExchangeRequestAction
from leave.models import LeaveApplication, LeaveApplicationAction, LeaveType

from .stamps import StepDef, compute_stamps

STEPS = [
    StepDef("Applicant", "submit", "DIGITALLY FILED"),
    StepDef("Supervisor", "endorse", "DIGITALLY ENDORSED"),
    StepDef("Chief of Hospital", "approve", "DIGITALLY APPROVED"),
]


def make_employee(username, employee_id, position="", role=None, status="REGULAR"):
    user = User.objects.create_user(username=username, password="pw-12345")
    emp = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test", position=position,
        employment_status=status, date_hired=date(2026, 1, 1),
    )
    if role:
        RoleAssignment.objects.create(employee=emp, role=role)
    return emp


def act(action, user, minutes=0, notes=""):
    return SimpleNamespace(action=action, acted_by=user, notes=notes,
                           acted_at=timezone.now() + timedelta(minutes=minutes))


class ComputeStampsTests(TestCase):
    def setUp(self):
        self.a = make_employee("stamp_a", "ST-1", "Nurse I").user
        self.b = make_employee("stamp_b", "ST-2", "Nurse III").user

    def test_done_steps_carry_name_position_and_time_others_pending(self):
        stamps, stopped = compute_stamps([act("submit", self.a), act("endorse", self.b, 5)], STEPS)
        self.assertEqual([s.state for s in stamps], ["done", "done", "pending"])
        self.assertEqual(stamps[1].text, "DIGITALLY ENDORSED")
        self.assertEqual(stamps[1].name, "Stamp_B, Test")
        self.assertEqual(stamps[1].position, "Nurse III")
        self.assertTrue(stamps[1].when)
        self.assertEqual(stamps[2].text, "PENDING")
        self.assertIsNone(stopped)

    def test_return_keeps_earlier_stamps_and_adds_red_banner_with_remarks(self):
        stamps, stopped = compute_stamps(
            [act("submit", self.a), act("return", self.b, 5, notes="Attach DTR")], STEPS
        )
        self.assertEqual(stamps[0].state, "done")
        self.assertEqual(stopped.text, "RETURNED")
        self.assertEqual(stopped.notes, "Attach DTR")

    def test_banner_only_when_stopping_action_is_latest(self):
        _, stopped = compute_stamps([act("submit", self.a), act("reject", self.b, 5), act("submit", self.a, 9)], STEPS)
        self.assertIsNone(stopped)

    def test_step_may_be_completed_by_any_of_several_actions(self):
        steps = [StepDef("HR", ("validate", "process"), "DIGITALLY VALIDATED")]
        stamps, _ = compute_stamps([act("process", self.a)], steps)
        self.assertEqual(stamps[0].state, "done")


class PrintViewTests(TestCase):
    """Each print page builds the right form, with the right code, for the
    people allowed to see the request - and refuses everyone else."""

    def setUp(self):
        from unittest.mock import MagicMock

        self.mock_pdf = MagicMock(return_value=b"%PDF-fake")
        for module in ("leave.cosp_leave_form", "cto.cto_form", "exchange.exchange_form", "attendance.correction_form"):
            patcher = patch(f"{module}.workbook_to_pdf", self.mock_pdf)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.emp = make_employee("print_emp", "PR-1", "Admin Aide", status="COSP")
        self.other = make_employee("print_other", "PR-2", "Nurse I")
        self.stranger = make_employee("print_stranger", "PR-3")
        self.hr = make_employee("print_hr", "PR-4", "HRMO II", role=RoleAssignment.HR_PROCESSOR)
        self.ictu = make_employee("print_ictu", "PR-5", "ICT Officer", role=RoleAssignment.ICTU_STAFF)

    def captured_sheet_text(self):
        wb = self.mock_pdf.call_args[0][0]
        ws = wb.active
        return " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)

    def get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)

    def test_cosp_leave_form_code_A_and_stamps(self):
        lt = LeaveType.objects.get(code="COSP_LEAVE")
        app = LeaveApplication.objects.create(employee=self.emp, leave_type=lt, start_date=date(2026, 10, 12),
                                              end_date=date(2026, 10, 12), number_of_days=Decimal("1"))
        LeaveApplicationAction.objects.create(application=app, action="submit", resulting_status="SUBMITTED",
                                              acted_by=self.emp.user)
        r = self.get(self.emp.user, reverse("leave:print_cosp_leave_form", args=[app.pk]))
        self.assertEqual(r.status_code, 200)
        text = self.captured_sheet_text()
        self.assertIn("BDH-ADM-HR-01F04-A", text)
        self.assertIn("DIGITALLY FILED", text)
        self.assertIn("PENDING", text)
        self.assertNotIn("Signature over printed name", text)

    def test_cto_form_code_B(self):
        app = CTOUsageApplication.objects.create(employee=self.emp, start_date=date(2026, 10, 20),
                                                 end_date=date(2026, 10, 20), number_of_days=Decimal("1"))
        CTOUsageApplicationAction.objects.create(application=app, action="submit", resulting_status="SUBMITTED",
                                                 acted_by=self.emp.user)
        self.assertEqual(self.get(self.emp.user, reverse("cto:print_cto_form", args=[app.pk])).status_code, 200)
        self.assertIn("BDH-ADM-HR-01F04-B", self.captured_sheet_text())
        self.assertEqual(self.get(self.stranger.user, reverse("cto:print_cto_form", args=[app.pk])).status_code, 403)

    def test_exchange_form_code_C_both_parties_can_print(self):
        req = DutyExchangeRequest.objects.create(employee_a=self.emp, date_a=date(2026, 10, 22),
                                                 employee_b=self.other, date_b=date(2026, 10, 24))
        DutyExchangeRequestAction.objects.create(request=req, action="file", resulting_status="PENDING_CONSENT",
                                                 acted_by=self.emp.user)
        url = reverse("exchange:print_exchange_form", args=[req.pk])
        self.assertEqual(self.get(self.other.user, url).status_code, 200)
        self.assertIn("BDH-ADM-HR-01F04-C", self.captured_sheet_text())
        self.assertEqual(self.get(self.stranger.user, url).status_code, 403)

    def test_correction_form_f10_rev2_ticks_reason_and_marks_other_validator_na(self):
        c = AttendanceCorrectionRequest.objects.create(
            correction_type="FORMAL", employee=self.emp, validator="ICTU", filed_by=self.emp.user,
        )
        c.lines.create(date=date(2026, 10, 5), time_in=time(8, 0), reason_category="OFFLINE")
        c.lines.create(date=date(2026, 10, 2), time_out=time(17, 0), reason_category="FAILED_ATTEMPT")
        AttendanceCorrectionRequestAction.objects.create(request=c, action="submit", resulting_status="SUBMITTED",
                                                         acted_by=self.emp.user)
        url = reverse("attendance:print_correction_form", args=[c.pk])
        self.assertEqual(self.get(self.ictu.user, url).status_code, 200)
        text = self.captured_sheet_text()
        self.assertIn("BDH-ADM-AO-01F10", text)
        self.assertIn("Revision: 2", text)
        self.assertIn("☒  Offline*", text)
        self.assertIn("☒  Failed Attempt*", text)
        self.assertIn("Oct 02, 2026", text)  # every date is printed
        self.assertIn("Oct 05, 2026", text)
        self.assertIn("NOT APPLICABLE", text)
        self.assertNotIn("RETURN HISTORY", text)
        self.assertEqual(self.get(self.stranger.user, url).status_code, 403)

    def test_minor_corrections_have_no_form(self):
        c = AttendanceCorrectionRequest.objects.create(correction_type="MINOR", employee=self.emp, filed_by=self.hr.user)
        c.lines.create(date=date(2026, 10, 5), time_in=time(8, 0), reason="HR fix")
        self.assertEqual(self.get(self.hr.user, reverse("attendance:print_correction_form", args=[c.pk])).status_code, 404)

    def test_missing_converter_shows_message_not_server_error(self):
        self.mock_pdf.side_effect = RuntimeError("LibreOffice was not found")
        app = CTOUsageApplication.objects.create(employee=self.emp, start_date=date(2026, 10, 20),
                                                 end_date=date(2026, 10, 20), number_of_days=Decimal("1"))
        r = self.get(self.emp.user, reverse("cto:print_cto_form", args=[app.pk]))
        self.assertRedirects(r, reverse("cto:my_cto"), fetch_redirect_response=False)
