import io
from datetime import date, time
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from employees.models import Employee
from leave.balances import compute_available_balance
from leave.models import LeaveCreditTransaction, LeaveType

from .deductions import sync_undertime_deduction, undertime_days
from .importer import import_biometric_file
from .models import AttendanceCorrectionRequest, AttendanceRecord, BiometricColumnMapping


def make_employee(username, employee_id, shift_hours=Employee.SHIFT_8_HOUR, role=None):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test",
        date_hired=date(2024, 1, 1), employment_status="REGULAR", shift_hours=shift_hours,
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    return employee


class UndertimeComputationTests(TestCase):
    """CLAUDE.md §9: attendance vs employees.Employee.shift_hours (built for
    the CTO engine, reused here)."""

    def setUp(self):
        self.hr_user = User.objects.create_user(username="hr_att1", password="x")
        self.employee = make_employee("att1", "EMP-ATT1")

    def test_full_shift_has_no_undertime(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 3, 2), time_in=time(8, 0), time_out=time(16, 0),
            recorded_by=self.hr_user,
        )
        self.assertEqual(record.undertime_minutes, 0)

    def test_leaving_early_computes_undertime(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 3, 2), time_in=time(8, 0), time_out=time(15, 30),
            recorded_by=self.hr_user,
        )
        self.assertEqual(record.undertime_minutes, 30)

    def test_overnight_shift_crossing_midnight(self):
        employee = make_employee("att2", "EMP-ATT2", shift_hours=Employee.SHIFT_12_HOUR)
        record = AttendanceRecord.objects.create(
            employee=employee, date=date(2026, 3, 2), time_in=time(19, 0), time_out=time(7, 0),
            recorded_by=self.hr_user,
        )
        self.assertEqual(record.undertime_minutes, 0)  # exactly 12 hours worked

    def test_missing_time_out_has_no_undertime_yet(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 3, 2), time_in=time(8, 0), recorded_by=self.hr_user,
        )
        self.assertEqual(record.undertime_minutes, 0)

    def test_absent_record_has_no_undertime(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 3, 2), is_absent=True, recorded_by=self.hr_user,
        )
        self.assertEqual(record.undertime_minutes, 0)

    def test_absent_with_times_is_invalid(self):
        record = AttendanceRecord(
            employee=self.employee, date=date(2026, 3, 2), is_absent=True, time_in=time(8, 0),
            recorded_by=self.hr_user,
        )
        with self.assertRaises(Exception):
            record.full_clean()


class UndertimeDeductionTests(TestCase):
    """CLAUDE.md §9: 'Undertime auto-deducts from leave credits' — confirmed
    2026-09-27 to charge VL."""

    def setUp(self):
        self.hr_user = User.objects.create_user(username="hr_ded1", password="x")
        self.employee = make_employee("ded1", "EMP-DED1")
        self.vl = LeaveType.objects.get(code="VL")

    def test_undertime_creates_vl_deduction(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 3, 2), time_in=time(8, 0), time_out=time(15, 30),
            recorded_by=self.hr_user,
        )
        sync_undertime_deduction(record, self.hr_user)
        self.assertEqual(record.undertime_transactions.count(), 1)
        txn = record.undertime_transactions.first()
        self.assertEqual(txn.leave_type, self.vl)
        self.assertLess(txn.days, 0)

    def test_undertime_days_fraction(self):
        record = AttendanceRecord(
            employee=self.employee, date=date(2026, 3, 2), time_in=time(8, 0), time_out=time(15, 30),
        )
        record.recompute_undertime()  # 30 min undertime on 480-min shift = 0.0625 -> 0.06
        self.assertEqual(undertime_days(record), Decimal("0.06"))

    def test_correcting_to_full_shift_removes_deduction(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 3, 2), time_in=time(8, 0), time_out=time(15, 30),
            recorded_by=self.hr_user,
        )
        sync_undertime_deduction(record, self.hr_user)
        self.assertEqual(record.undertime_transactions.count(), 1)

        record.time_out = time(16, 0)
        record.save()
        sync_undertime_deduction(record, self.hr_user)
        self.assertEqual(record.undertime_transactions.count(), 0)

    def test_resaving_unchanged_undertime_does_not_duplicate(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 3, 2), time_in=time(8, 0), time_out=time(15, 30),
            recorded_by=self.hr_user,
        )
        sync_undertime_deduction(record, self.hr_user)
        sync_undertime_deduction(record, self.hr_user)
        self.assertEqual(record.undertime_transactions.count(), 1)

    def test_deduction_reduces_vl_balance(self):
        balance_before = compute_available_balance(self.employee, self.vl, as_of_date=date(2026, 3, 2))
        record = AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 3, 2), time_in=time(8, 0), time_out=time(15, 30),
            recorded_by=self.hr_user,
        )
        sync_undertime_deduction(record, self.hr_user)
        balance_after = compute_available_balance(self.employee, self.vl, as_of_date=date(2026, 3, 2))
        self.assertEqual(balance_before - balance_after, Decimal("0.06"))


class ManualOverridesBiometricTests(TestCase):
    """CLAUDE.md §9: 'Biometric data is advisory, not authoritative.'"""

    def setUp(self):
        self.hr_user = User.objects.create_user(username="hr_adv1", password="x")
        self.employee = make_employee("adv1", "EMP-ADV1")

    def test_import_skips_manually_corrected_record(self):
        AttendanceRecord.objects.create(
            employee=self.employee, date=date(2026, 4, 1), time_in=time(8, 0), time_out=time(16, 0),
            source=AttendanceRecord.SOURCE_MANUAL, recorded_by=self.hr_user,
        )
        mapping = BiometricColumnMapping.get_active()
        csv_content = f"Employee ID,Date,Time In,Time Out\nEMP-ADV1,2026-04-01,09:00:00,12:00:00\n"
        batch = import_biometric_file(
            io.BytesIO(csv_content.encode("utf-8")), "test.csv", mapping, self.hr_user
        )
        self.assertEqual(batch.skipped_count, 1)
        self.assertEqual(batch.imported_count, 0)
        record = AttendanceRecord.objects.get(employee=self.employee, date=date(2026, 4, 1))
        self.assertEqual(record.time_in, time(8, 0))  # untouched


class BiometricImportTests(TestCase):
    def setUp(self):
        self.hr_user = User.objects.create_user(username="hr_imp1", password="x")
        self.employee = make_employee("imp1", "EMP-IMP1")
        self.mapping = BiometricColumnMapping.get_active()

    def test_csv_import_creates_records(self):
        csv_content = "Employee ID,Date,Time In,Time Out\nEMP-IMP1,2026-04-02,08:00:00,16:00:00\n"
        batch = import_biometric_file(io.BytesIO(csv_content.encode("utf-8")), "att.csv", self.mapping, self.hr_user)
        self.assertEqual(batch.imported_count, 1)
        record = AttendanceRecord.objects.get(employee=self.employee, date=date(2026, 4, 2))
        self.assertEqual(record.time_in, time(8, 0))
        self.assertEqual(record.time_out, time(16, 0))
        self.assertEqual(record.source, AttendanceRecord.SOURCE_BIOMETRIC)

    def test_unknown_employee_id_is_logged_as_error_not_fatal(self):
        csv_content = "Employee ID,Date,Time In,Time Out\nNO-SUCH-ID,2026-04-02,08:00:00,16:00:00\n"
        batch = import_biometric_file(io.BytesIO(csv_content.encode("utf-8")), "att.csv", self.mapping, self.hr_user)
        self.assertEqual(batch.error_count, 1)
        self.assertIn("NO-SUCH-ID", batch.error_log)

    def test_reimport_updates_biometric_sourced_record(self):
        csv_content = "Employee ID,Date,Time In,Time Out\nEMP-IMP1,2026-04-02,08:00:00,16:00:00\n"
        import_biometric_file(io.BytesIO(csv_content.encode("utf-8")), "att.csv", self.mapping, self.hr_user)
        csv_content_2 = "Employee ID,Date,Time In,Time Out\nEMP-IMP1,2026-04-02,08:05:00,16:00:00\n"
        import_biometric_file(io.BytesIO(csv_content_2.encode("utf-8")), "att2.csv", self.mapping, self.hr_user)
        record = AttendanceRecord.objects.get(employee=self.employee, date=date(2026, 4, 2))
        self.assertEqual(record.time_in, time(8, 5))


def lines_post(*lines, **extra):
    """POST data for the correction lines formset. Each line is a dict of
    date/time_in/time_out/overnight/is_absent/reason_category/reason."""
    data = {"lines-TOTAL_FORMS": str(len(lines)), "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "10"}
    for i, line in enumerate(lines):
        for k, v in line.items():
            if v is True:
                v = "on"
            if v not in (False, None):
                data[f"lines-{i}-{k}"] = v
    data.update(extra)
    return data


def line(day="2026-05-05", time_in="08:00", time_out="16:00", **kw):
    return {"date": day, "time_in": time_in, "time_out": time_out, **kw}


class MinorCorrectionFlowTests(TestCase):
    """CLAUDE.md §3/§9: HR Processor files -> HR Administrator authorizes;
    an HR Administrator filing it themselves is self-authorizing."""

    def setUp(self):
        self.employee = make_employee("minor1", "EMP-MINOR1")
        self.processor = make_employee("minorproc1", "EMP-MINORPROC1", role=RoleAssignment.HR_PROCESSOR)
        self.admin_hr = make_employee("minoradmin1", "EMP-MINORADMIN1", role=RoleAssignment.HR_ADMINISTRATOR)
        self.client = Client()

    def file(self, who, *lines):
        self.client.force_login(who.user)
        lines = lines or (line("2026-05-01", reason="Device misread"),)
        return self.client.post(reverse("attendance:minor_correction_create"),
                                lines_post(*lines, employee=self.employee.pk))

    def test_processor_filed_correction_awaits_authorization(self):
        self.file(self.processor)
        correction = AttendanceCorrectionRequest.objects.get()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.SUBMITTED)
        self.assertEqual(correction.validator, "")
        self.assertFalse(AttendanceRecord.objects.filter(employee=self.employee).exists())

    def test_hr_administrator_filing_is_self_authorized(self):
        self.file(self.admin_hr, line("2026-05-01", reason="Device misread"), line("2026-05-02", reason="Same"))
        correction = AttendanceCorrectionRequest.objects.get()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.APPROVED)
        self.assertEqual(AttendanceRecord.objects.filter(employee=self.employee, source=AttendanceRecord.SOURCE_MANUAL).count(), 2)

    def test_hr_administrator_authorizes_pending_minor_request(self):
        self.file(self.processor)
        correction = AttendanceCorrectionRequest.objects.get()
        self.client.force_login(self.admin_hr.user)
        self.client.post(reverse("attendance:correction_action", args=[correction.pk]), {"action": "approve"})
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.APPROVED)
        self.assertTrue(AttendanceRecord.objects.filter(employee=self.employee, date=date(2026, 5, 1)).exists())

    def test_processor_cannot_self_authorize(self):
        self.file(self.processor)
        correction = AttendanceCorrectionRequest.objects.get()
        response = self.client.post(reverse("attendance:correction_action", args=[correction.pk]), {"action": "approve"})
        self.assertEqual(response.status_code, 403)

    def test_minor_lines_need_a_reason(self):
        self.file(self.processor, line("2026-05-01"))
        self.assertFalse(AttendanceCorrectionRequest.objects.exists())

    def test_returned_minor_is_fixed_by_the_processor_who_filed_it(self):
        self.file(self.processor)
        correction = AttendanceCorrectionRequest.objects.get()
        self.client.force_login(self.admin_hr.user)
        self.client.post(reverse("attendance:correction_action", args=[correction.pk]),
                         {"action": "return", "notes": "Wrong date"})
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.RETURNED)
        self.client.force_login(self.employee.user)  # the employee did not file it
        self.assertEqual(self.client.get(reverse("attendance:correction_edit", args=[correction.pk])).status_code, 403)
        self.client.force_login(self.processor.user)
        self.client.post(reverse("attendance:correction_edit", args=[correction.pk]),
                         lines_post(line("2026-05-03", reason="Device misread")))
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.SUBMITTED)
        self.assertEqual([l.date for l in correction.lines.all()], [date(2026, 5, 3)])

    def test_regular_employee_cannot_file_minor_correction(self):
        response = self.file(self.employee)
        self.assertEqual(response.status_code, 403)


class FormalCorrectionFlowTests(TestCase):
    """Missed Log Justification Form, BDH-ADM-AO-01F10 Rev. 2 (owner
    decision 2026-10-06): Employee -> ICTU Staff (Offline / Failed Attempt
    / Wrong Button) or HR (Attended activity / Others) -> AO. No Supervisor
    step and no COH step."""

    def setUp(self):
        self.employee = make_employee("formal1", "EMP-FORMAL1")
        self.supervisor = make_employee("formalsup1", "EMP-FORMALSUP1", role=RoleAssignment.SUPERVISOR)
        self.ictu = make_employee("formalictu1", "EMP-FORMALICTU1", role=RoleAssignment.ICTU_STAFF)
        self.hr = make_employee("formalhr1", "EMP-FORMALHR1", role=RoleAssignment.HR_PROCESSOR)
        self.ao = make_employee("formalao1", "EMP-FORMALAO1", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.client = Client()

    def file(self, category="FAILED_ATTEMPT", reason="", day="2026-05-05"):
        return self.file_lines(line(day, reason_category=category, reason=reason))

    def file_lines(self, *lines):
        self.client.force_login(self.employee.user)
        return self.client.post(reverse("attendance:formal_correction_apply"), lines_post(*lines))

    def act(self, who, correction, action, notes=""):
        self.client.force_login(who.user)
        return self.client.post(reverse("attendance:correction_action", args=[correction.pk]),
                                {"action": action, "notes": notes})

    def test_employee_files_own_formal_correction(self):
        self.file()
        correction = AttendanceCorrectionRequest.objects.get()
        self.assertEqual(correction.correction_type, AttendanceCorrectionRequest.FORMAL)
        self.assertEqual(correction.status, AttendanceCorrectionRequest.SUBMITTED)
        self.assertEqual(correction.validator, "ICTU")
        self.assertEqual(correction.lines.get().reason_category, "FAILED_ATTEMPT")

    def test_reason_category_must_be_chosen(self):
        self.file_lines(line(reason="x"))
        self.assertFalse(AttendanceCorrectionRequest.objects.exists())

    def test_attended_activity_and_others_need_details(self):
        for category in ("ATTENDED_ACTIVITY", "OTHERS"):
            self.file(category=category, reason="")
        self.assertFalse(AttendanceCorrectionRequest.objects.exists())
        self.file(category="ATTENDED_ACTIVITY", reason="DOH orientation")
        self.assertTrue(AttendanceCorrectionRequest.objects.exists())

    def test_system_reasons_are_validated_by_ictu_then_approved_by_ao(self):
        for category in ("OFFLINE", "FAILED_ATTEMPT", "WRONG_ENTRY"):
            AttendanceCorrectionRequest.objects.all().delete()
            AttendanceRecord.objects.all().delete()
            self.file(category=category)
            correction = AttendanceCorrectionRequest.objects.get()
            self.assertEqual(self.act(self.hr, correction, "validate").status_code, 403)  # HR is not the validator
            self.act(self.ictu, correction, "validate")
            correction.refresh_from_db()
            self.assertEqual(correction.status, AttendanceCorrectionRequest.VALIDATED)
            self.act(self.ao, correction, "approve")
            correction.refresh_from_db()
            self.assertEqual(correction.status, AttendanceCorrectionRequest.APPROVED)
            self.assertTrue(AttendanceRecord.objects.filter(employee=self.employee, date=date(2026, 5, 5)).exists())

    def test_activity_and_other_reasons_are_validated_by_hr(self):
        self.file(category="OTHERS", reason="Biometric was relocated during repairs")
        correction = AttendanceCorrectionRequest.objects.get()
        self.assertEqual(self.act(self.ictu, correction, "validate").status_code, 403)
        self.act(self.hr, correction, "validate")
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.VALIDATED)

    def test_supervisor_and_coh_have_no_step(self):
        coh = make_employee("formalcoh1", "EMP-FORMALCOH1", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.file()
        correction = AttendanceCorrectionRequest.objects.get()
        self.assertEqual(self.act(self.supervisor, correction, "endorse").status_code, 403)
        self.assertEqual(self.act(self.supervisor, correction, "validate").status_code, 403)
        self.act(self.ictu, correction, "validate")
        self.assertEqual(self.act(coh, correction, "approve").status_code, 403)

    def test_ao_cannot_approve_before_validation(self):
        self.file()
        correction = AttendanceCorrectionRequest.objects.get()
        self.assertEqual(self.act(self.ao, correction, "approve").status_code, 403)

    def test_queues_show_each_validator_only_their_requests(self):
        from .permissions import visible_correction_requests_for

        self.file(category="OFFLINE", day="2026-05-05")
        self.file(category="OTHERS", reason="Seminar", day="2026-05-06")
        ictu_queue = visible_correction_requests_for(self.ictu)
        hr_queue = visible_correction_requests_for(self.hr)
        self.assertEqual([c.validator for c in ictu_queue], ["ICTU"])
        self.assertEqual([c.validator for c in hr_queue], ["HR"])
        self.assertFalse(visible_correction_requests_for(self.supervisor).exists())

    def test_old_chain_requests_can_still_finish(self):
        correction = AttendanceCorrectionRequest.objects.create(
            correction_type="FORMAL", employee=self.employee, validator="HR",
            status=AttendanceCorrectionRequest.ENDORSED_BY_SUPERVISOR, filed_by=self.employee.user,
        )
        correction.lines.create(date=date(2026, 5, 6), time_in=time(8, 0), reason_category="OTHERS", reason="Old")
        self.act(self.hr, correction, "process")
        self.act(self.ao, correction, "approve")
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.APPROVED)

    def test_ictu_staff_sidebar_shows_only_the_correction_queue(self):
        self.client.force_login(self.ictu.user)
        html = self.client.get(reverse("notifications:notification_list")).content.decode()
        self.assertIn("Attendance Correction Queue", html)
        self.assertNotIn("Leave Queue", html)


class MultiDateCorrectionTests(TestCase):
    """Batch 2, Item 6 (owner decisions 2026-10-07): 1-10 dates in one
    request, whole-request approval, return with a remark, edit and
    resubmit from the first step, history kept."""

    def setUp(self):
        from notifications.models import Notification

        self.Notification = Notification
        self.employee = make_employee("multi1", "EMP-MULTI1")
        self.ictu = make_employee("multiictu", "EMP-MULTIICTU", role=RoleAssignment.ICTU_STAFF)
        self.ao = make_employee("multiao", "EMP-MULTIAO", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.client = Client()

    def file(self, *lines):
        self.client.force_login(self.employee.user)
        return self.client.post(reverse("attendance:formal_correction_apply"), lines_post(*lines))

    def act(self, who, correction, action, notes=""):
        self.client.force_login(who.user)
        return self.client.post(reverse("attendance:correction_action", args=[correction.pk]),
                                {"action": action, "notes": notes})

    def ok_line(self, day, **kw):
        return line(day, reason_category=kw.pop("reason_category", "OFFLINE"), **kw)

    def test_several_dates_are_one_request_with_lines(self):
        self.file(self.ok_line("2026-05-04"), self.ok_line("2026-05-05", reason_category="FAILED_ATTEMPT"),
                  self.ok_line("2026-05-06", time_in="", time_out="17:00"))
        correction = AttendanceCorrectionRequest.objects.get()
        self.assertEqual(correction.lines.count(), 3)
        self.assertEqual(correction.dates_summary, "May 04 – May 06, 2026 (3 dates)")

    def test_whole_request_approval_writes_every_date(self):
        self.file(self.ok_line("2026-05-04"), self.ok_line("2026-05-05"))
        correction = AttendanceCorrectionRequest.objects.get()
        self.act(self.ictu, correction, "validate")
        self.act(self.ao, correction, "approve")  # remark optional on approval
        self.assertEqual(AttendanceRecord.objects.filter(employee=self.employee).count(), 2)

    def test_limits_one_to_ten_lines(self):
        self.file()
        self.file(*[self.ok_line(f"2026-05-{d:02d}") for d in range(1, 12)])  # 11 lines
        self.assertFalse(AttendanceCorrectionRequest.objects.exists())
        self.file(*[self.ok_line(f"2026-05-{d:02d}") for d in range(1, 11)])  # 10 lines
        self.assertEqual(AttendanceCorrectionRequest.objects.get().lines.count(), 10)

    def test_no_future_dates(self):
        from datetime import timedelta
        from django.utils import timezone

        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        r = self.file(self.ok_line(tomorrow))
        self.assertContains(r, "Future dates")
        self.assertFalse(AttendanceCorrectionRequest.objects.exists())

    def test_no_duplicate_dates_in_one_request(self):
        r = self.file(self.ok_line("2026-05-04"), self.ok_line("2026-05-04"))
        self.assertContains(r, "listed more than once")
        self.assertFalse(AttendanceCorrectionRequest.objects.exists())

    def test_no_date_that_already_has_a_pending_correction(self):
        self.file(self.ok_line("2026-05-04"))
        r = self.file(self.ok_line("2026-05-04"), self.ok_line("2026-05-07"))
        self.assertContains(r, "already have a pending correction")
        self.assertEqual(AttendanceCorrectionRequest.objects.count(), 1)

    def test_time_out_must_be_after_time_in_unless_overnight(self):
        r = self.file(self.ok_line("2026-05-04", time_in="19:00", time_out="07:00"))
        self.assertContains(r, "Time out must be later than time in")
        self.file(self.ok_line("2026-05-04", time_in="19:00", time_out="07:00", overnight=True))
        self.assertTrue(AttendanceCorrectionRequest.objects.get().lines.get().overnight)

    def test_ictu_and_hr_reasons_cannot_be_mixed(self):
        r = self.file(self.ok_line("2026-05-04"), self.ok_line("2026-05-05", reason_category="OTHERS", reason="Seminar"))
        self.assertContains(r, "separate requests")
        self.assertFalse(AttendanceCorrectionRequest.objects.exists())

    def test_return_needs_a_remark(self):
        self.file(self.ok_line("2026-05-04"))
        correction = AttendanceCorrectionRequest.objects.get()
        self.act(self.ictu, correction, "return", notes="   ")
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.SUBMITTED)

    def test_return_edit_resubmit_starts_over_and_keeps_history(self):
        self.file(self.ok_line("2026-05-04"), self.ok_line("2026-05-05"))
        correction = AttendanceCorrectionRequest.objects.get()
        self.act(self.ictu, correction, "validate")
        self.act(self.ao, correction, "return", notes="May 5 time out looks wrong")
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.RETURNED)
        note = self.Notification.objects.filter(recipient=self.employee).latest("created_at")
        self.assertIn("May 5 time out looks wrong", note.message)

        # employee sees the remark, edits (drops one date, fixes another) and resubmits the SAME request
        self.client.force_login(self.employee.user)
        page = self.client.get(reverse("attendance:correction_edit", args=[correction.pk]))
        self.assertContains(page, "May 5 time out looks wrong")
        self.client.post(reverse("attendance:correction_edit", args=[correction.pk]),
                         lines_post(self.ok_line("2026-05-05", time_out="17:00")))
        correction.refresh_from_db()
        self.assertEqual(AttendanceCorrectionRequest.objects.count(), 1)
        self.assertEqual(correction.status, AttendanceCorrectionRequest.SUBMITTED)  # back to the first step
        self.assertEqual([l.date for l in correction.lines.all()], [date(2026, 5, 5)])
        self.assertEqual(self.act(self.ao, correction, "approve").status_code, 403)  # must be validated again
        self.act(self.ictu, correction, "validate")
        self.act(self.ao, correction, "approve")
        correction.refresh_from_db()
        self.assertEqual(correction.status, AttendanceCorrectionRequest.APPROVED)
        self.assertEqual(list(correction.actions.values_list("action", flat=True)),
                         ["submit", "validate", "return", "resubmit", "validate", "approve"])
        detail = self.client.get(reverse("attendance:correction_detail", args=[correction.pk]))
        self.assertContains(detail, "May 5 time out looks wrong")
        # printed stamps show the current round only
        self.assertEqual([a.action for a in correction.current_cycle_actions()], ["resubmit", "validate", "approve"])

    def test_only_returned_requests_can_be_edited_and_only_by_the_employee(self):
        self.file(self.ok_line("2026-05-04"))
        correction = AttendanceCorrectionRequest.objects.get()
        self.client.force_login(self.employee.user)
        r = self.client.post(reverse("attendance:correction_edit", args=[correction.pk]),
                             lines_post(self.ok_line("2026-05-01")))
        self.assertRedirects(r, reverse("attendance:correction_detail", args=[correction.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(correction.lines.get().date, date(2026, 5, 4))
        self.client.force_login(self.ictu.user)
        self.assertEqual(self.client.get(reverse("attendance:correction_edit", args=[correction.pk])).status_code, 403)

    def test_detail_page_shows_lines_and_whole_request_buttons(self):
        self.file(self.ok_line("2026-05-04"), self.ok_line("2026-05-05"))
        correction = AttendanceCorrectionRequest.objects.get()
        self.client.force_login(self.ictu.user)
        page = self.client.get(reverse("attendance:correction_detail", args=[correction.pk])).content.decode()
        self.assertIn("Dates (2)", page)
        self.assertIn('value="validate"', page)
        self.assertIn('value="return"', page)
        self.client.force_login(self.ao.user)  # not their turn yet: no buttons
        page = self.client.get(reverse("attendance:correction_detail", args=[correction.pk])).content.decode()
        self.assertNotIn('value="approve"', page)
