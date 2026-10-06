from datetime import date

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import RoleAssignment
from cto.models import CTOCreditEntry, CTOMultiplierRate
from employees.models import Employee
from leave.models import LeaveApplication, LeaveType

from .models import DocumentRequirement, UploadedDocument
from .permissions import can_view_document
from .requirements import requirement_status_for
from .validators import MAX_FILE_SIZE_BYTES, MAX_FILES_PER_TRANSACTION, validate_upload

PDF_BYTES = b"%PDF-1.4\n%mock pdf content for tests\n"
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 20
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


def make_employee(username, employee_id, role=None):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test",
        date_hired=date(2024, 1, 1), employment_status="REGULAR",
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    return employee


def make_credit_entry(employee, actor_user, duty_type=CTOCreditEntry.DUTY_REGULAR):
    rate, _ = CTOMultiplierRate.objects.get_or_create(
        effective_date=date(2025, 12, 16), defaults=dict(weekday_multiplier="1.00", restday_holiday_multiplier="1.50"),
    )
    return CTOCreditEntry.objects.create(
        employee=employee, work_date=date(2026, 3, 1), duty_type=duty_type, hours_worked="8.00",
        shift_hours_used=8, multiplier_applied="1.00", credited_hours="8.00", credited_days="1.00",
        recorded_by=actor_user,
    )


@override_settings(MEDIA_ROOT="/tmp/bdh_hris_test_media")
class ValidatorTests(TestCase):
    def test_valid_pdf_accepted(self):
        f = SimpleUploadedFile("cert.pdf", PDF_BYTES, content_type="application/pdf")
        file_type = validate_upload(f, existing_active_count=0)
        self.assertEqual(file_type, UploadedDocument.PDF)

    def test_valid_jpg_accepted(self):
        f = SimpleUploadedFile("photo.jpg", JPG_BYTES, content_type="image/jpeg")
        file_type = validate_upload(f, existing_active_count=0)
        self.assertEqual(file_type, UploadedDocument.JPG)

    def test_disallowed_extension_rejected(self):
        f = SimpleUploadedFile("script.exe", b"MZ\x90\x00", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            validate_upload(f, existing_active_count=0)

    def test_renamed_file_with_wrong_content_rejected(self):
        # A .pdf extension on content that isn't really a PDF.
        f = SimpleUploadedFile("fake.pdf", b"not a real pdf at all", content_type="application/pdf")
        with self.assertRaises(ValidationError):
            validate_upload(f, existing_active_count=0)

    def test_oversized_file_rejected(self):
        f = SimpleUploadedFile("big.pdf", PDF_BYTES + b"0" * (MAX_FILE_SIZE_BYTES + 1), content_type="application/pdf")
        with self.assertRaises(ValidationError):
            validate_upload(f, existing_active_count=0)

    def test_five_file_cap_enforced(self):
        f = SimpleUploadedFile("cert.pdf", PDF_BYTES, content_type="application/pdf")
        with self.assertRaises(ValidationError):
            validate_upload(f, existing_active_count=MAX_FILES_PER_TRANSACTION)

    def test_four_existing_still_allows_fifth(self):
        f = SimpleUploadedFile("cert.pdf", PDF_BYTES, content_type="application/pdf")
        validate_upload(f, existing_active_count=MAX_FILES_PER_TRANSACTION - 1)  # should not raise


@override_settings(MEDIA_ROOT="/tmp/bdh_hris_test_media")
class RequirementStatusTests(TestCase):
    """CLAUDE.md §7 (confirmed 2026-09-27): regular vs Medical Transport
    CTO document sets are fully separate, with an OR alternative group."""

    def setUp(self):
        self.hr_user = User.objects.create_user(username="hr_docreq", password="x")
        self.employee = make_employee("docreq1", "EMP-DOCREQ1")

    def test_regular_duty_requires_three_documents(self):
        entry = make_credit_entry(self.employee, self.hr_user, duty_type=CTOCreditEntry.DUTY_REGULAR)
        status = requirement_status_for(entry)
        labels = {row["label"] for row in status}
        self.assertEqual(labels, {"Allowed to Work form", "DTR/logbook copy", "OT Accomplishment Report"})
        self.assertTrue(all(not row["satisfied"] for row in status))

    def test_medical_transport_requires_trip_ticket_and_alternative(self):
        entry = make_credit_entry(self.employee, self.hr_user, duty_type=CTOCreditEntry.DUTY_MEDICAL_TRANSPORT)
        status = requirement_status_for(entry)
        labels = {row["label"] for row in status}
        self.assertIn("Trip Ticket (with employee's name on it)", labels)
        self.assertTrue(any("Certificate of Appearance" in label and "Logbook copy" in label for label in labels))
        self.assertEqual(len(status), 2)  # trip ticket + one collapsed alternative-group row

    def test_uploading_one_alternative_satisfies_the_group(self):
        entry = make_credit_entry(self.employee, self.hr_user, duty_type=CTOCreditEntry.DUTY_MEDICAL_TRANSPORT)
        content_type = ContentType.objects.get_for_model(entry)
        cert_requirement = DocumentRequirement.objects.get(
            content_type=content_type, sub_type_value="MEDICAL_TRANSPORT", label="Logbook copy",
        )
        UploadedDocument.objects.create(
            content_type=content_type, object_id=entry.pk, requirement=cert_requirement,
            file=SimpleUploadedFile("log.pdf", PDF_BYTES), original_filename="log.pdf",
            file_size=len(PDF_BYTES), file_type=UploadedDocument.PDF, uploaded_by=self.hr_user,
        )
        status = requirement_status_for(entry)
        alt_row = next(row for row in status if "Logbook copy" in row["label"])
        self.assertTrue(alt_row["satisfied"])


@override_settings(MEDIA_ROOT="/tmp/bdh_hris_test_media")
class ConfidentialityPermissionTests(TestCase):
    """Confirmed 2026-09-27: confidential documents are visible only to the
    owner, HR, and System Administrator — not Supervisor/AO/COH."""

    def setUp(self):
        self.employee = make_employee("conf1", "EMP-CONF1")
        self.supervisor = make_employee("confsup1", "EMP-CONFSUP1", role=RoleAssignment.SUPERVISOR)
        self.hr = make_employee("confhr1", "EMP-CONFHR1", role=RoleAssignment.HR_PROCESSOR)
        self.sysadmin = make_employee("confsys1", "EMP-CONFSYS1", role=RoleAssignment.SYSTEM_ADMINISTRATOR)
        self.hr_user = User.objects.create_user(username="hr_conf_actor", password="x")
        self.vl = LeaveType.objects.get(code="VL")
        self.application = LeaveApplication.objects.create(
            employee=self.employee, leave_type=self.vl, start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 1), number_of_days="1.00",
        )
        self.content_type = ContentType.objects.get_for_model(self.application)
        self.confidential_doc = UploadedDocument.objects.create(
            content_type=self.content_type, object_id=self.application.pk,
            file=SimpleUploadedFile("cert.pdf", PDF_BYTES), original_filename="cert.pdf",
            file_size=len(PDF_BYTES), file_type=UploadedDocument.PDF, is_confidential=True,
            uploaded_by=self.hr_user,
        )

    def test_owner_can_view_confidential_document(self):
        self.assertTrue(can_view_document(self.employee, self.confidential_doc))

    def test_hr_can_view_confidential_document(self):
        self.assertTrue(can_view_document(self.hr, self.confidential_doc))

    def test_system_administrator_can_view_confidential_document(self):
        self.assertTrue(can_view_document(self.sysadmin, self.confidential_doc))

    def test_supervisor_cannot_view_confidential_document(self):
        self.assertFalse(can_view_document(self.supervisor, self.confidential_doc))

    def test_non_confidential_document_follows_transaction_visibility(self):
        non_confidential = UploadedDocument.objects.create(
            content_type=self.content_type, object_id=self.application.pk,
            file=SimpleUploadedFile("form.pdf", PDF_BYTES), original_filename="form.pdf",
            file_size=len(PDF_BYTES), file_type=UploadedDocument.PDF, is_confidential=False,
            uploaded_by=self.hr_user,
        )
        self.assertTrue(can_view_document(self.supervisor, non_confidential))


@override_settings(MEDIA_ROOT="/tmp/bdh_hris_test_media")
class UploadViewTests(TestCase):
    def setUp(self):
        self.employee = make_employee("upl1", "EMP-UPL1")
        self.outsider = make_employee("uploutside1", "EMP-UPLOUT1")
        self.hr = make_employee("uplhr1", "EMP-UPLHR1", role=RoleAssignment.HR_PROCESSOR)
        self.vl = LeaveType.objects.get(code="VL")
        self.application = LeaveApplication.objects.create(
            employee=self.employee, leave_type=self.vl, start_date=date(2026, 4, 5),
            end_date=date(2026, 4, 5), number_of_days="1.00",
        )
        self.client = Client()
        self.upload_url = reverse(
            "documents:document_upload",
            kwargs={"app_label": "leave", "model_name": "leaveapplication", "object_id": self.application.pk},
        )

    def test_owner_can_upload(self):
        self.client.force_login(self.employee.user)
        f = SimpleUploadedFile("cert.pdf", PDF_BYTES, content_type="application/pdf")
        response = self.client.post(self.upload_url, {"file": f, "mark_confidential": "on"})
        self.assertEqual(UploadedDocument.objects.count(), 1)
        doc = UploadedDocument.objects.get()
        self.assertTrue(doc.is_confidential)
        self.assertEqual(doc.events.count(), 1)

    def test_outsider_cannot_upload(self):
        self.client.force_login(self.outsider.user)
        f = SimpleUploadedFile("cert.pdf", PDF_BYTES, content_type="application/pdf")
        response = self.client.post(self.upload_url, {"file": f})
        self.assertEqual(response.status_code, 403)

    def test_original_filename_is_not_the_stored_path(self):
        self.client.force_login(self.employee.user)
        f = SimpleUploadedFile("very original filename.pdf", PDF_BYTES, content_type="application/pdf")
        self.client.post(self.upload_url, {"file": f})
        doc = UploadedDocument.objects.get()
        self.assertEqual(doc.original_filename, "very original filename.pdf")
        self.assertNotIn("very original filename", doc.file.name)

    def test_invalid_file_type_rejected_by_view(self):
        self.client.force_login(self.employee.user)
        f = SimpleUploadedFile("virus.exe", b"MZ\x90\x00", content_type="application/octet-stream")
        self.client.post(self.upload_url, {"file": f})
        self.assertEqual(UploadedDocument.objects.count(), 0)

    def test_download_denied_to_outsider(self):
        self.client.force_login(self.employee.user)
        f = SimpleUploadedFile("cert.pdf", PDF_BYTES, content_type="application/pdf")
        self.client.post(self.upload_url, {"file": f})
        doc = UploadedDocument.objects.get()

        self.client.force_login(self.outsider.user)
        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        self.assertEqual(response.status_code, 403)

    def test_download_allowed_to_owner(self):
        self.client.force_login(self.employee.user)
        f = SimpleUploadedFile("cert.pdf", PDF_BYTES, content_type="application/pdf")
        self.client.post(self.upload_url, {"file": f})
        doc = UploadedDocument.objects.get()

        response = self.client.get(reverse("documents:document_download", args=[doc.pk]))
        self.assertEqual(response.status_code, 200)


@override_settings(MEDIA_ROOT="/tmp/bdh_hris_test_media")
class ReplaceViewTests(TestCase):
    def setUp(self):
        self.employee = make_employee("rep1", "EMP-REP1")
        self.vl = LeaveType.objects.get(code="VL")
        self.application = LeaveApplication.objects.create(
            employee=self.employee, leave_type=self.vl, start_date=date(2026, 4, 10),
            end_date=date(2026, 4, 10), number_of_days="1.00",
        )
        self.client = Client()
        self.client.force_login(self.employee.user)
        upload_url = reverse(
            "documents:document_upload",
            kwargs={"app_label": "leave", "model_name": "leaveapplication", "object_id": self.application.pk},
        )
        self.client.post(upload_url, {"file": SimpleUploadedFile("v1.pdf", PDF_BYTES, content_type="application/pdf")})
        self.original = UploadedDocument.objects.get()

    def test_replace_marks_old_inactive_and_links_supersession(self):
        response = self.client.post(
            reverse("documents:document_replace", args=[self.original.pk]),
            {"file": SimpleUploadedFile("v2.pdf", PDF_BYTES, content_type="application/pdf")},
        )
        self.original.refresh_from_db()
        self.assertFalse(self.original.is_active)
        self.assertIsNotNone(self.original.superseded_by)
        new_doc = self.original.superseded_by
        self.assertTrue(new_doc.is_active)
        self.assertEqual(UploadedDocument.objects.filter(is_active=True).count(), 1)

    def test_cannot_replace_an_already_replaced_document(self):
        self.client.post(
            reverse("documents:document_replace", args=[self.original.pk]),
            {"file": SimpleUploadedFile("v2.pdf", PDF_BYTES, content_type="application/pdf")},
        )
        response = self.client.post(
            reverse("documents:document_replace", args=[self.original.pk]),
            {"file": SimpleUploadedFile("v3.pdf", PDF_BYTES, content_type="application/pdf")},
        )
        self.assertEqual(response.status_code, 403)


class UploadFormLabelTests(TestCase):
    def test_requirement_dropdown_shows_plain_document_names(self):
        from cto.models import CTOCreditEntry

        from .forms import DocumentUploadForm

        ct = ContentType.objects.get_for_model(CTOCreditEntry)
        qs = DocumentRequirement.objects.filter(content_type=ct)
        self.assertTrue(qs.exists())  # seeded by documents/migrations/0002
        labels = [label for value, label in DocumentUploadForm(requirement_queryset=qs).fields["requirement"].choices if value]
        self.assertIn("Allowed to Work form", labels)
        for label in labels:
            self.assertNotIn("ctocreditentry", label)
