from datetime import date, timedelta

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from attendance.models import AttendanceCorrectionRequest, AttendanceCorrectionRequestAction
from cto.models import CTOUsageApplication, CTOUsageApplicationAction
from documents.models import UploadedDocument, UploadedDocumentEvent
from employees.models import Employee, EmployeeEditHistory
from exchange.models import DutyExchangeRequest, DutyExchangeRequestAction
from leave.models import LeaveApplication, LeaveApplicationAction, LeaveType
from official_requests.models import OfficialRequest, OfficialRequestAction

from . import retention
from .aggregation import audit_rows
from .models import RetentionReviewRecord
from .permissions import can_view_audit_log

PDF_BYTES = b"%PDF-1.4\n%mock\n"


def make_employee(username, employee_id, role=None, separation_date=None):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test",
        date_hired=date(2020, 1, 1), separation_date=separation_date,
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    return employee


class PermissionsTests(TestCase):
    def test_hr_administrator_can_view(self):
        hr = make_employee("auditperm_hr", "EMP-AL-1", role=RoleAssignment.HR_ADMINISTRATOR)
        self.assertTrue(can_view_audit_log(hr))

    def test_plain_employee_cannot_view(self):
        plain = make_employee("auditperm_plain", "EMP-AL-2")
        self.assertFalse(can_view_audit_log(plain))

    def test_none_cannot_view(self):
        self.assertFalse(can_view_audit_log(None))


class AggregationTests(TestCase):
    """One assertion per source, confirming each of the seven existing
    accountability records surfaces in the merged audit log."""

    def setUp(self):
        self.actor = User.objects.create_user(username="auditlog_actor", password="testpass123")
        self.employee = make_employee("auditagg_emp", "EMP-AG-1")

    def test_employee_edit_history_appears(self):
        EmployeeEditHistory.objects.create(
            employee=self.employee, field_name="position", field_label="Position",
            old_value="Nurse I", new_value="Nurse II", reason="Promotion", changed_by=self.actor,
        )
        rows = audit_rows()
        matches = [r for r in rows if r["module"] == "Employee Record"]
        self.assertEqual(len(matches), 1)
        self.assertIn(self.employee.pk, matches[0]["employee_ids"])

    def test_leave_action_appears(self):
        leave_type = LeaveType.objects.create(
            name="Vacation Leave", code="VL-AGG", applicable_to=LeaveType.APPLICABLE_BOTH,
            balance_tracking=LeaveType.TRACKING_UNTRACKED, requires_full_routing=True,
        )
        application = LeaveApplication.objects.create(
            employee=self.employee, leave_type=leave_type, start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5), number_of_days=1, status=LeaveApplication.SUBMITTED,
        )
        LeaveApplicationAction.objects.create(
            application=application, action="SUBMITTED", resulting_status=LeaveApplication.SUBMITTED,
            acted_by=self.actor,
        )
        rows = audit_rows(module="Leave")
        self.assertEqual(len(rows), 1)
        self.assertIn(self.employee.pk, rows[0]["employee_ids"])

    def test_cto_action_appears(self):
        application = CTOUsageApplication.objects.create(
            employee=self.employee, start_date=date(2026, 2, 1), end_date=date(2026, 2, 1),
            number_of_days=1, reason="Personal", status=CTOUsageApplication.SUBMITTED,
        )
        CTOUsageApplicationAction.objects.create(
            application=application, action="SUBMITTED", resulting_status=CTOUsageApplication.SUBMITTED,
            acted_by=self.actor,
        )
        rows = audit_rows(module="CTO")
        self.assertEqual(len(rows), 1)

    def test_exchange_action_lists_both_employees(self):
        other = make_employee("auditagg_other", "EMP-AG-2")
        request = DutyExchangeRequest.objects.create(
            employee_a=self.employee, date_a=date(2026, 3, 1),
            employee_b=other, date_b=date(2026, 3, 2),
        )
        DutyExchangeRequestAction.objects.create(
            request=request, action="CONSENTED", resulting_status=DutyExchangeRequest.SUBMITTED,
            acted_by=self.actor,
        )
        rows = audit_rows(module="Exchange of Duty")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["employee_ids"], {self.employee.pk, other.pk})

    def test_attendance_action_appears(self):
        request = AttendanceCorrectionRequest.objects.create(
            correction_type=AttendanceCorrectionRequest.FORMAL, employee=self.employee,
            validator="HR", filed_by=self.actor,
        )
        AttendanceCorrectionRequestAction.objects.create(
            request=request, action="SUBMITTED", resulting_status=AttendanceCorrectionRequest.SUBMITTED,
            acted_by=self.actor,
        )
        rows = audit_rows(module="Attendance Correction")
        self.assertEqual(len(rows), 1)

    def test_official_request_action_appears(self):
        request = OfficialRequest.objects.create(
            request_type=OfficialRequest.OFFICIAL_BUSINESS, employee=self.employee,
            start_date=date(2026, 5, 1), end_date=date(2026, 5, 1), purpose="Conference",
        )
        OfficialRequestAction.objects.create(
            request=request, action="SUBMITTED", resulting_status=OfficialRequest.SUBMITTED,
            acted_by=self.actor,
        )
        rows = audit_rows(module="Official Request")
        self.assertEqual(len(rows), 1)

    def test_document_event_resolves_employee_through_content_object(self):
        leave_type = LeaveType.objects.create(
            name="Sick Leave", code="SL-AGG", applicable_to=LeaveType.APPLICABLE_BOTH,
            balance_tracking=LeaveType.TRACKING_UNTRACKED, requires_full_routing=True,
        )
        application = LeaveApplication.objects.create(
            employee=self.employee, leave_type=leave_type, start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5), number_of_days=1,
        )
        content_type = ContentType.objects.get_for_model(LeaveApplication)
        document = UploadedDocument.objects.create(
            content_type=content_type, object_id=application.pk,
            file=SimpleUploadedFile("cert.pdf", PDF_BYTES, content_type="application/pdf"),
            original_filename="cert.pdf", file_size=len(PDF_BYTES), file_type=UploadedDocument.PDF,
            uploaded_by=self.actor,
        )
        UploadedDocumentEvent.objects.create(
            document=document, event_type=UploadedDocumentEvent.UPLOADED, acted_by=self.actor,
        )
        rows = audit_rows(module="Documents")
        self.assertEqual(len(rows), 1)
        self.assertIn(self.employee.pk, rows[0]["employee_ids"])

    def test_employee_filter_scopes_results(self):
        other = make_employee("auditagg_filter_other", "EMP-AG-3")
        EmployeeEditHistory.objects.create(
            employee=self.employee, field_name="email", old_value="a@x.com", new_value="b@x.com",
            reason="Update", changed_by=self.actor,
        )
        EmployeeEditHistory.objects.create(
            employee=other, field_name="email", old_value="c@x.com", new_value="d@x.com",
            reason="Update", changed_by=self.actor,
        )
        rows = audit_rows(employee_id=self.employee.pk)
        self.assertEqual(len(rows), 1)

    def test_date_range_filter(self):
        entry = EmployeeEditHistory.objects.create(
            employee=self.employee, field_name="email", old_value="a@x.com", new_value="b@x.com",
            reason="Update", changed_by=self.actor,
        )
        yesterday = (entry.edited_at - timedelta(days=1)).date()
        tomorrow = (entry.edited_at + timedelta(days=1)).date()
        self.assertEqual(len(audit_rows(date_from=yesterday, date_to=tomorrow)), 1)
        self.assertEqual(len(audit_rows(date_from=tomorrow)), 0)


class RetentionTests(TestCase):
    def test_not_separated_never_due(self):
        make_employee("retention_active", "EMP-RT-1")
        self.assertEqual(list(retention.employees_due_for_retention_review()), [])

    def test_recently_separated_not_yet_due(self):
        recent = date.today() - timedelta(days=365 * 5)
        make_employee("retention_recent", "EMP-RT-2", separation_date=recent)
        self.assertEqual(list(retention.employees_due_for_retention_review()), [])

    def test_separated_past_ten_years_is_due(self):
        old = date.today() - timedelta(days=365 * 11)
        employee = make_employee("retention_old", "EMP-RT-3", separation_date=old)
        self.assertIn(employee, list(retention.employees_due_for_retention_review()))

    def test_decided_employee_drops_off_due_list(self):
        old = date.today() - timedelta(days=365 * 11)
        employee = make_employee("retention_decided", "EMP-RT-4", separation_date=old)
        actor = User.objects.create_user(username="retention_actor", password="testpass123")
        RetentionReviewRecord.objects.create(
            employee=employee, decision=RetentionReviewRecord.RETAIN, reason="Pending case", reviewed_by=actor,
        )
        self.assertNotIn(employee, list(retention.employees_due_for_retention_review()))


class ViewAccessTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_hr_administrator_can_load_audit_log(self):
        make_employee("auditview_hr", "EMP-AV-1", role=RoleAssignment.HR_ADMINISTRATOR)
        self.client.login(username="auditview_hr", password="testpass123")
        response = self.client.get(reverse("auditlog:audit_log"))
        self.assertEqual(response.status_code, 200)

    def test_plain_employee_gets_403(self):
        make_employee("auditview_plain", "EMP-AV-2")
        self.client.login(username="auditview_plain", password="testpass123")
        response = self.client.get(reverse("auditlog:audit_log"))
        self.assertEqual(response.status_code, 403)

    def test_retention_list_requires_access(self):
        make_employee("auditview_plain2", "EMP-AV-3")
        self.client.login(username="auditview_plain2", password="testpass123")
        response = self.client.get(reverse("auditlog:retention_review_list"))
        self.assertEqual(response.status_code, 403)

    def test_retention_decision_requires_reason(self):
        make_employee("auditview_hr2", "EMP-AV-4", role=RoleAssignment.HR_ADMINISTRATOR)
        old = date.today() - timedelta(days=365 * 11)
        subject = make_employee("auditview_subject", "EMP-AV-5", separation_date=old)
        self.client.login(username="auditview_hr2", password="testpass123")

        response = self.client.post(
            reverse("auditlog:retention_review_decide", args=[subject.pk]),
            {"decision": RetentionReviewRecord.DISPOSE, "reason": ""},
        )
        self.assertRedirects(response, reverse("auditlog:retention_review_list"))
        self.assertEqual(RetentionReviewRecord.objects.filter(employee=subject).count(), 0)

    def test_retention_decision_with_reason_is_recorded(self):
        make_employee("auditview_hr3", "EMP-AV-6", role=RoleAssignment.HR_ADMINISTRATOR)
        old = date.today() - timedelta(days=365 * 11)
        subject = make_employee("auditview_subject2", "EMP-AV-7", separation_date=old)
        self.client.login(username="auditview_hr3", password="testpass123")

        response = self.client.post(
            reverse("auditlog:retention_review_decide", args=[subject.pk]),
            {"decision": RetentionReviewRecord.RETAIN, "reason": "Pending administrative case."},
        )
        self.assertRedirects(response, reverse("auditlog:retention_review_list"))
        record = RetentionReviewRecord.objects.get(employee=subject)
        self.assertEqual(record.decision, RetentionReviewRecord.RETAIN)
        self.assertEqual(record.reason, "Pending administrative case.")
