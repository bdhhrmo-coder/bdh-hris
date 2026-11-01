from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import RoleAssignment
from documents.models import DocumentRequirement, UploadedDocument
from employees.models import Employee
from notifications.models import Notification
from orgstructure.models import Section, Unit

from . import routing
from .balances import (
    compute_available_cto_balance,
    compute_credit,
    cto_used_in_month,
    exceeds_monthly_cap,
    filing_deadline_for,
    forms_illegal_consecutive_run,
    violates_filing_deadline,
)
from .claims import add_line, correct_day_type, credit_lines, submit_claim
from .models import (
    HOLIDAY,
    REST_DAY,
    WEEKDAY,
    CTOClaim,
    CTOClaimLine,
    CTOMultiplierRate,
    CTOUsageApplication,
    OvertimeRequest,
    RoutedRecord,
)
from .overtime import file_ot
from .permissions import is_cto_eligible

NURSING = "Nursing Service Section"


def make_employee(username, employee_id, shift_hours=Employee.SHIFT_8_HOUR, role=None, section=None, unit=None):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user,
        employee_id=employee_id,
        surname=username.title(),
        first_name="Test",
        date_hired=date(2024, 1, 1),
        employment_status="REGULAR",
        shift_hours=shift_hours,
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    if section:
        employee.sections.add(section)
    if unit:
        employee.units.add(unit)
    return employee


def make_rate(effective_date=date(2025, 12, 16), weekday="1.00", restday="1.50"):
    # The 0002_seed_cto_multiplier_rate migration already seeds a real row
    # at 2025-12-16; update_or_create avoids colliding with it.
    rate, _ = CTOMultiplierRate.objects.update_or_create(
        effective_date=effective_date,
        defaults=dict(weekday_multiplier=weekday, restday_holiday_multiplier=restday),
    )
    rate.refresh_from_db()
    return rate


def on(day):
    """Freeze 'today' for routing's approval date."""
    return patch("django.utils.timezone.localdate", return_value=day)


def ot_line(employee, work_date, hours="8", day_type=WEEKDAY):
    return {"employee": employee, "work_date": work_date, "approved_hours": Decimal(hours), "day_type": day_type}


def upload_all_required(claim, actor_user):
    content_type = ContentType.objects.get_for_model(claim)
    for req in DocumentRequirement.objects.filter(content_type=content_type, sub_type_value__in=["", claim.duty_type]):
        UploadedDocument.objects.create(
            content_type=content_type, object_id=claim.pk, requirement=req, file="secure_documents/x.pdf",
            original_filename="x.pdf", file_size=1, file_type=UploadedDocument.PDF, uploaded_by=actor_user,
        )


def approve_all(record, on_approved=lambda r: None):
    """Walk a pending record to the end, each step acted on by an eligible
    approver."""
    while record.status == RoutedRecord.PENDING:
        actor = sorted(routing.step_actors(record, record.current_step), key=lambda e: e.pk)[0]
        routing.approve_step(record, actor, actor.user, on_approved=on_approved)
        record.refresh_from_db()


class Org:
    """A minimal hospital: one of each approving role, a supervisor of a
    non-Nursing section, and Nursing with a Chief Nurse and an ER Unit Head."""

    def __init__(self):
        make_rate()
        self.lab = Section.objects.get(name="Laboratory Section")
        self.nursing = Section.objects.get(name=NURSING)
        self.er = Unit.objects.get(section=self.nursing, name="ER")
        self.hr = make_employee("hr", "E-HR", role=RoleAssignment.HR_PROCESSOR)
        self.hr_admin = make_employee("hradmin", "E-HRA", role=RoleAssignment.HR_ADMINISTRATOR)
        self.ao = make_employee("ao", "E-AO", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("coh", "E-COH", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.lab_sup = make_employee("labsup", "E-LS", section=self.lab)
        RoleAssignment.objects.create(employee=self.lab_sup, role=RoleAssignment.SUPERVISOR, section=self.lab)
        self.lab_staff = make_employee("labstaff", "E-L1", section=self.lab)
        self.lab_staff2 = make_employee("labstaff2", "E-L2", section=self.lab)
        self.chief_nurse = make_employee("chiefnurse", "E-CN", section=self.nursing)
        RoleAssignment.objects.create(employee=self.chief_nurse, role=RoleAssignment.SUPERVISOR, section=self.nursing)
        self.er_head = make_employee("erhead", "E-ERH", section=self.nursing, unit=self.er)
        RoleAssignment.objects.create(employee=self.er_head, role=RoleAssignment.SUPERVISOR, unit=self.er)
        self.nurse = make_employee("nurse", "E-N1", section=self.nursing, unit=self.er)


def approved_ot(org, employee, work_date, hours="8", day_type=WEEKDAY, today=None):
    ot, _ = file_ot(org.hr, OvertimeRequest.CAPACITY_HR, "Coverage", "past", [ot_line(employee, work_date, hours, day_type)],
                    org.hr.user, today or work_date)
    approve_all(ot)
    return ot.lines.get()


def filed_claim(org, employee, lines, today, duty_type=CTOClaim.DUTY_REGULAR):
    """lines: [(ot_line or None, hours, work_date, day_type)]. Submits it."""
    claim = CTOClaim.objects.create(employee=employee, duty_type=duty_type, filed_by=employee.user)
    for ot, hours, work_date, day_type in lines:
        add_line(claim, Decimal(hours), today, ot_line=ot, work_date=work_date, day_type=day_type)
    upload_all_required(claim, employee.user)
    submit_claim(claim, employee.user, today)
    claim.refresh_from_db()
    return claim


class MultiplierConversionTests(TestCase):
    """CLAUDE.md §7: OT hours x 1.0 (weekday) or x 1.5 (rest day/holiday);
    day length is per-employee by shift (confirmed 2026-09-27)."""

    def setUp(self):
        self.rate = make_rate()

    def test_weekday_8_hour_shift(self):
        multiplier, hours, days = compute_credit(Decimal("8"), False, self.rate, Employee.SHIFT_8_HOUR)
        self.assertEqual(multiplier, Decimal("1.00"))
        self.assertEqual(hours, Decimal("8.00"))
        self.assertEqual(days, Decimal("1.00"))

    def test_restday_8_hour_shift(self):
        multiplier, hours, days = compute_credit(Decimal("8"), True, self.rate, Employee.SHIFT_8_HOUR)
        self.assertEqual(multiplier, Decimal("1.50"))
        self.assertEqual(hours, Decimal("12.00"))
        self.assertEqual(days, Decimal("1.50"))

    def test_half_day_4_hours_on_8_hour_shift(self):
        _, _, days = compute_credit(Decimal("4"), False, self.rate, Employee.SHIFT_8_HOUR)
        self.assertEqual(days, Decimal("0.50"))

    def test_weekday_12_hour_shift(self):
        multiplier, hours, days = compute_credit(Decimal("12"), False, self.rate, Employee.SHIFT_12_HOUR)
        self.assertEqual(multiplier, Decimal("1.00"))
        self.assertEqual(hours, Decimal("12.00"))
        self.assertEqual(days, Decimal("1.00"))

    def test_half_day_6_hours_on_12_hour_shift(self):
        _, _, days = compute_credit(Decimal("6"), False, self.rate, Employee.SHIFT_12_HOUR)
        self.assertEqual(days, Decimal("0.50"))

    def test_restday_12_hour_shift(self):
        multiplier, hours, days = compute_credit(Decimal("12"), True, self.rate, Employee.SHIFT_12_HOUR)
        self.assertEqual(multiplier, Decimal("1.50"))
        self.assertEqual(hours, Decimal("18.00"))
        self.assertEqual(days, Decimal("1.50"))


class MultiplierRateEffectiveDatingTests(TestCase):
    """A later multiplier change must never retroactively reinterpret an
    earlier claim — CLAUDE.md §7: 'must be configurable, not hardcoded.'"""

    def setUp(self):
        self.old_rate = make_rate(effective_date=date(2025, 1, 1), weekday="1.00", restday="1.25")
        self.new_rate = make_rate(effective_date=date(2025, 12, 16), weekday="1.00", restday="1.50")

    def test_active_as_of_picks_correct_historical_rate(self):
        self.assertEqual(CTOMultiplierRate.active_as_of(date(2025, 6, 1)), self.old_rate)
        self.assertEqual(CTOMultiplierRate.active_as_of(date(2025, 12, 16)), self.new_rate)
        self.assertEqual(CTOMultiplierRate.active_as_of(date(2026, 1, 1)), self.new_rate)

    def test_no_rate_before_any_effective_date(self):
        self.assertIsNone(CTOMultiplierRate.active_as_of(date(2024, 1, 1)))


class ChiefOfHospitalExclusionTests(TestCase):
    """CLAUDE.md §7: 'Not applicable to the Chief of Hospital.' Enforced
    structurally, not just left as a UI convention."""

    def setUp(self):
        self.coh = make_employee("coh1", "EMP-COH1", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.regular = make_employee("reg1", "EMP-REG1")
        self.rate = make_rate()
        self.hr_user = User.objects.create_user(username="hr_cto", password="x")

    def test_is_cto_eligible(self):
        self.assertFalse(is_cto_eligible(self.coh))
        self.assertTrue(is_cto_eligible(self.regular))

    def test_claim_clean_rejects_coh(self):
        claim = CTOClaim(employee=self.coh, filed_by=self.hr_user)
        with self.assertRaises(Exception):
            claim.full_clean()

    def test_usage_application_clean_rejects_coh(self):
        application = CTOUsageApplication(
            employee=self.coh, start_date=date(2026, 2, 1), end_date=date(2026, 2, 1),
            number_of_days=Decimal("1.00"),
        )
        with self.assertRaises(Exception):
            application.full_clean()


class MonthlyUsageCapTests(TestCase):
    """CLAUDE.md §7: max 5 CTO days/month — confirmed 2026-09-27 as a
    usage-side cap."""

    def setUp(self):
        self.employee = make_employee("cap1", "EMP-CAP1")
        self.hr_user = User.objects.create_user(username="hr_cap", password="x")

    def _record_usage(self, start, end, days, status=CTOUsageApplication.SUBMITTED):
        return CTOUsageApplication.objects.create(
            employee=self.employee, start_date=start, end_date=end, number_of_days=days, status=status,
        )

    def test_exactly_5_days_is_allowed(self):
        self._record_usage(date(2026, 3, 2), date(2026, 3, 2), Decimal("4"))
        self.assertFalse(exceeds_monthly_cap(self.employee, date(2026, 3, 20), Decimal("1")))

    def test_over_5_days_is_blocked(self):
        self._record_usage(date(2026, 3, 2), date(2026, 3, 2), Decimal("4.5"))
        self.assertTrue(exceeds_monthly_cap(self.employee, date(2026, 3, 20), Decimal("1")))

    def test_rejected_applications_do_not_count_against_cap(self):
        self._record_usage(date(2026, 3, 2), date(2026, 3, 2), Decimal("4.5"), status=CTOUsageApplication.REJECTED)
        self.assertFalse(exceeds_monthly_cap(self.employee, date(2026, 3, 20), Decimal("1")))

    def test_cap_is_per_calendar_month(self):
        self._record_usage(date(2026, 3, 30), date(2026, 3, 30), Decimal("5"))
        self.assertFalse(exceeds_monthly_cap(self.employee, date(2026, 4, 1), Decimal("5")))

    def test_cto_used_in_month_excludes_given_application(self):
        app = self._record_usage(date(2026, 3, 2), date(2026, 3, 2), Decimal("5"))
        self.assertEqual(cto_used_in_month(self.employee, 2026, 3, exclude_application=app), Decimal("0"))


class ConsecutiveDaysTests(TestCase):
    """CLAUDE.md §7: 'no 3+ consecutive days.' Documented interpretation
    (2026-09-27): checked against the full usage history, not just the
    single application, so two adjacent 2-day applications still count."""

    def setUp(self):
        self.employee = make_employee("run1", "EMP-RUN1")

    def test_single_2_day_application_is_allowed(self):
        self.assertFalse(forms_illegal_consecutive_run(self.employee, date(2026, 4, 1), date(2026, 4, 2)))

    def test_single_3_day_application_is_blocked(self):
        self.assertTrue(forms_illegal_consecutive_run(self.employee, date(2026, 4, 1), date(2026, 4, 3)))

    def test_two_adjacent_2_day_applications_form_illegal_run(self):
        CTOUsageApplication.objects.create(
            employee=self.employee, start_date=date(2026, 4, 1), end_date=date(2026, 4, 2),
            number_of_days=Decimal("2"), status=CTOUsageApplication.SUBMITTED,
        )
        self.assertTrue(forms_illegal_consecutive_run(self.employee, date(2026, 4, 3), date(2026, 4, 4)))

    def test_non_adjacent_applications_do_not_combine(self):
        CTOUsageApplication.objects.create(
            employee=self.employee, start_date=date(2026, 4, 1), end_date=date(2026, 4, 2),
            number_of_days=Decimal("2"), status=CTOUsageApplication.SUBMITTED,
        )
        self.assertFalse(forms_illegal_consecutive_run(self.employee, date(2026, 4, 5), date(2026, 4, 6)))

    def test_rejected_application_does_not_block_adjacency(self):
        CTOUsageApplication.objects.create(
            employee=self.employee, start_date=date(2026, 4, 1), end_date=date(2026, 4, 2),
            number_of_days=Decimal("2"), status=CTOUsageApplication.REJECTED,
        )
        self.assertFalse(forms_illegal_consecutive_run(self.employee, date(2026, 4, 3), date(2026, 4, 4)))


class FilingDeadlineAndUsageCutoffTests(TestCase):
    """CLAUDE.md §7: 'CTO filing deadline is Nov 30 each year; CTO usage
    dates cannot extend past Dec 15.'"""

    def test_filing_on_deadline_is_allowed(self):
        self.assertFalse(violates_filing_deadline(date(2026, 6, 1), date(2026, 11, 30)))

    def test_filing_after_deadline_is_blocked(self):
        self.assertTrue(violates_filing_deadline(date(2026, 6, 1), date(2026, 12, 1)))

    def test_november_work_deadline_is_same_year(self):
        self.assertEqual(filing_deadline_for(date(2026, 11, 29)), date(2026, 11, 30))

    def test_december_work_deadline_is_next_year(self):
        self.assertEqual(filing_deadline_for(date(2026, 12, 20)), date(2027, 11, 30))

    @override_settings(CTO_FILING_DEADLINE_MONTH_DAY=(10, 31))
    def test_filing_deadline_comes_from_settings(self):
        self.assertTrue(violates_filing_deadline(date(2026, 6, 1), date(2026, 11, 1)))




class OTRoutingTests(TestCase):
    """CLAUDE.md §7.1 OT routes, approver-is-subject rule, and the
    never-skip-HR/AO/COH-when-unassigned rule."""

    def setUp(self):
        self.org = Org()

    def test_supervisor_filed_batch_goes_hr_ao_coh(self):
        ot, _ = file_ot(self.org.lab_sup, "SUPERVISOR", "x", "", [ot_line(self.org.lab_staff, date(2026, 11, 5))],
                        self.org.lab_sup.user, date(2026, 11, 1))
        self.assertEqual(ot.route_steps(), ["HR", "AO", "COH"])

    def test_nursing_unit_head_batch_goes_through_chief_nurse(self):
        ot, _ = file_ot(self.org.er_head, "SUPERVISOR", "x", "", [ot_line(self.org.nurse, date(2026, 11, 5))],
                        self.org.er_head.user, date(2026, 11, 1))
        self.assertEqual(ot.route_steps(), ["CHIEF_NURSE", "HR", "AO", "COH"])
        self.assertEqual(ot.current_step, "CHIEF_NURSE")
        self.assertTrue(routing.can_act(self.org.chief_nurse, ot))

    def test_hr_filed_batch_goes_ao_coh(self):
        ot, _ = file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.lab_staff, date(2026, 11, 5))],
                        self.org.hr.user, date(2026, 11, 1))
        self.assertEqual(ot.route_steps(), ["AO", "COH"])

    def test_ao_filed_batch_waits_for_coh(self):
        ot, _ = file_ot(self.org.ao, "AO", "x", "", [ot_line(self.org.lab_staff, date(2026, 11, 5))],
                        self.org.ao.user, date(2026, 11, 1))
        self.assertEqual(ot.status, RoutedRecord.PENDING)
        self.assertEqual(ot.current_step, "COH")
        self.assertEqual(routing.step_actors(ot, "COH"), {self.org.coh})

    def test_no_coh_assigned_leaves_batch_pending_and_tells_hr_admin(self):
        RoleAssignment.objects.filter(role=RoleAssignment.CHIEF_OF_HOSPITAL).update(is_active=False)
        ot, _ = file_ot(self.org.ao, "AO", "x", "", [ot_line(self.org.lab_staff, date(2026, 11, 5))],
                        self.org.ao.user, date(2026, 11, 1))
        self.assertEqual(ot.status, RoutedRecord.PENDING)
        self.assertEqual(ot.current_step, "COH")
        self.assertFalse(ot.actions.filter(action="skip").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.org.hr_admin, message__contains="nobody can act").exists())

    def test_no_ao_assigned_waits_at_ao_not_skipped(self):
        RoleAssignment.objects.filter(role=RoleAssignment.ADMINISTRATIVE_OFFICER).update(is_active=False)
        ot, _ = file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.lab_staff, date(2026, 11, 5))],
                        self.org.hr.user, date(2026, 11, 1))
        self.assertEqual(ot.current_step, "AO")
        self.assertEqual(ot.status, RoutedRecord.PENDING)

    def test_approver_on_a_line_cannot_act_and_another_holder_takes_the_step(self):
        hr2 = make_employee("hr2", "E-HR2", role=RoleAssignment.HR_PROCESSOR)
        ot, _ = file_ot(self.org.lab_sup, "SUPERVISOR", "x", "",
                        [ot_line(self.org.lab_staff, date(2026, 11, 5)), ot_line(self.org.hr, date(2026, 11, 5))],
                        self.org.lab_sup.user, date(2026, 11, 1))
        self.assertEqual(ot.current_step, "HR")
        self.assertFalse(routing.can_act(self.org.hr, ot))
        self.assertTrue(routing.can_act(hr2, ot))
        with self.assertRaises(ValidationError):
            routing.approve_step(ot, self.org.hr, self.org.hr.user)

    def test_subject_conflict_skip_is_logged(self):
        # The only AO is on the batch: the AO step is skipped and logged.
        ot, _ = file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.ao, date(2026, 11, 5))],
                        self.org.hr.user, date(2026, 11, 1))
        self.assertEqual(ot.current_step, "COH")
        skip = ot.actions.get(action="skip")
        self.assertEqual(skip.step, "AO")
        self.assertIn("every holder is on this record", skip.notes)

    def test_batch_is_approved_as_a_whole(self):
        ot, _ = file_ot(self.org.hr, "HR", "x", "",
                        [ot_line(self.org.lab_staff, date(2026, 11, 5)), ot_line(self.org.lab_staff2, date(2026, 11, 5))],
                        self.org.hr.user, date(2026, 11, 1))
        approve_all(ot)
        self.assertEqual(ot.status, RoutedRecord.APPROVED)
        self.assertEqual(ot.actions.filter(action="approve").count(), 2)

    def test_return_needs_a_remark_and_goes_back_to_filer(self):
        ot, _ = file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.lab_staff, date(2026, 11, 5))],
                        self.org.hr.user, date(2026, 11, 1))
        with self.assertRaises(ValidationError):
            routing.return_record(ot, self.org.ao, self.org.ao.user, "  ")
        routing.return_record(ot, self.org.ao, self.org.ao.user, "Wrong date")
        self.assertEqual(ot.status, RoutedRecord.RETURNED)
        ot, _ = file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.lab_staff, date(2026, 11, 6))],
                        self.org.hr.user, date(2026, 11, 1), ot=ot)
        self.assertEqual(ot.current_step, "AO")  # chain restarts
        self.assertTrue(ot.actions.filter(action="resubmit").exists())

    def test_filer_cannot_add_themselves(self):
        with self.assertRaises(ValidationError):
            file_ot(self.org.lab_sup, "SUPERVISOR", "x", "", [ot_line(self.org.lab_sup, date(2026, 11, 5))],
                    self.org.lab_sup.user, date(2026, 11, 1))

    def test_coh_cannot_be_added(self):
        with self.assertRaises(ValidationError):
            file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.coh, date(2026, 11, 5))],
                    self.org.hr.user, date(2026, 11, 1))

    def test_employee_without_filing_role_cannot_file(self):
        with self.assertRaises(ValidationError):
            file_ot(self.org.lab_staff, "SUPERVISOR", "x", "", [ot_line(self.org.lab_staff2, date(2026, 11, 5))],
                    self.org.lab_staff.user, date(2026, 11, 1))

    def test_past_dates_need_a_reason(self):
        with self.assertRaises(ValidationError):
            file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.lab_staff, date(2026, 10, 5))],
                    self.org.hr.user, date(2026, 11, 1))
        file_ot(self.org.hr, "HR", "x", "Emergency, filed late", [ot_line(self.org.lab_staff, date(2026, 10, 5))],
                self.org.hr.user, date(2026, 11, 1))

    def test_warns_when_line_employee_is_on_the_route(self):
        _, warnings = file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.ao, date(2026, 11, 5))],
                              self.org.hr.user, date(2026, 11, 1))
        self.assertEqual(len(warnings), 1)

    def test_employees_supervisor_gets_notice_when_added_by_someone_else(self):
        file_ot(self.org.hr, "HR", "x", "", [ot_line(self.org.lab_staff, date(2026, 11, 5))],
                self.org.hr.user, date(2026, 11, 1))
        self.assertTrue(Notification.objects.filter(recipient=self.org.lab_sup, message__contains="added to an OT").exists())

    def test_oic_counts_as_chief_nurse_and_unit_head(self):
        oic = make_employee("oic", "E-OIC")
        RoleAssignment.objects.create(employee=oic, role=RoleAssignment.SUPERVISOR, section=self.org.nursing,
                                      is_oic=True, delegated_by=self.org.chief_nurse)
        oic_unit = make_employee("oicunit", "E-OICU")
        RoleAssignment.objects.create(employee=oic_unit, role=RoleAssignment.SUPERVISOR, unit=self.org.er,
                                      is_oic=True, delegated_by=self.org.er_head)
        self.assertIn(oic, routing.chief_nurses())
        self.assertIn(oic_unit, routing.unit_heads_of(self.org.nurse))


class OTBatchFormTests(TestCase):
    """The OT form's 'Add row' button only raises TOTAL_FORMS; the server
    must accept any number of rows."""

    def setUp(self):
        self.org = Org()
        self.staff = [make_employee(f"bulk{i}", f"E-B{i}", section=self.org.lab) for i in range(12)]

    def test_twelve_line_batch(self):
        data = {"filed_as": "HR", "purpose": "Unit-wide coverage", "past_date_reason": "",
                "lines-TOTAL_FORMS": "13", "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "200"}
        for i, emp in enumerate(self.staff):
            data.update({f"lines-{i}-employee": emp.pk, f"lines-{i}-work_date": "2099-01-05",
                         f"lines-{i}-approved_hours": "4", f"lines-{i}-day_type": WEEKDAY})
        client = Client()
        client.force_login(self.org.hr.user)  # row 12 left blank on purpose
        response = client.post(reverse("cto:ot_new"), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OvertimeRequest.objects.get().lines.count(), 12)


class ClaimRoutingTests(TestCase):
    def setUp(self):
        self.org = Org()

    def _claim(self, employee):
        ot = approved_ot(self.org, employee, date(2026, 10, 5), today=date(2026, 10, 1))
        return filed_claim(self.org, employee, [(ot, "8", None, None)], date(2026, 10, 6))

    def test_claim_route(self):
        claim = self._claim(self.org.lab_staff)
        self.assertEqual(claim.route_steps(), ["SUPERVISOR", "HR", "AO", "COH"])
        self.assertTrue(routing.can_act(self.org.lab_sup, claim))

    def test_nursing_claim_route_unit_head_then_chief_nurse(self):
        claim = self._claim(self.org.nurse)
        self.assertEqual(claim.route_steps(), ["SUPERVISOR", "CHIEF_NURSE", "HR", "AO", "COH"])
        self.assertEqual(routing.step_actors(claim, "SUPERVISOR"), {self.org.er_head})
        self.assertEqual(routing.step_actors(claim, "CHIEF_NURSE"), {self.org.chief_nurse})

    def test_chief_nurse_own_claim_skips_the_chief_nurse_step(self):
        claim = self._claim(self.org.chief_nurse)
        # No Unit Head covers the Chief Nurse (no unit), so that step is skipped too.
        self.assertEqual(claim.current_step, "HR")
        self.assertEqual(set(claim.actions.filter(action="skip").values_list("step", flat=True)),
                         {"SUPERVISOR", "CHIEF_NURSE"})

    def test_ao_own_claim_skips_only_the_ao_step(self):
        RoleAssignment.objects.create(employee=self.org.lab_sup, role=RoleAssignment.SUPERVISOR)  # covers anyone
        claim = self._claim(self.org.ao)
        approve_all(claim, on_approved=credit_lines)
        self.assertEqual(claim.status, RoutedRecord.APPROVED)
        self.assertEqual(list(claim.actions.filter(action="skip").values_list("step", flat=True)), ["AO"])
        self.assertEqual(list(claim.actions.filter(action="approve").values_list("step", flat=True)),
                         ["SUPERVISOR", "HR", "COH"])

    def test_second_ao_takes_the_ao_step_on_ao_own_claim(self):
        RoleAssignment.objects.create(employee=self.org.lab_sup, role=RoleAssignment.SUPERVISOR)
        ao2 = make_employee("ao2", "E-AO2", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        claim = self._claim(self.org.ao)
        approve_all(claim, on_approved=credit_lines)
        approvals = list(claim.actions.filter(action="approve").values_list("step", "acted_by"))
        self.assertIn(("AO", ao2.user.pk), approvals)
        self.assertFalse(claim.actions.filter(action="skip").exists())

    def test_exception_claim_goes_ao_coh(self):
        claim = CTOClaim.objects.create(employee=self.org.lab_staff, filed_by=self.org.hr_admin.user,
                                        exception_reason="Certified emergency duty")
        add_line(claim, Decimal("8"), date(2026, 10, 6), work_date=date(2026, 10, 5), day_type=WEEKDAY)
        upload_all_required(claim, self.org.hr_admin.user)
        submit_claim(claim, self.org.hr_admin.user, date(2026, 10, 6))
        self.assertEqual(claim.route_steps(), ["AO", "COH"])

    def test_return_without_remark_is_refused(self):
        claim = self._claim(self.org.lab_staff)
        with self.assertRaises(ValidationError):
            routing.return_record(claim, self.org.lab_sup, self.org.lab_sup.user, "")


class ClaimRuleTests(TestCase):
    def setUp(self):
        self.org = Org()
        self.emp = self.org.lab_staff

    def test_approved_ot_alone_credits_nothing(self):
        approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 12, 1)), Decimal("0"))

    def test_actual_hours_above_approved_are_rejected(self):
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), hours="4", today=date(2026, 10, 1))
        claim = CTOClaim.objects.create(employee=self.emp, filed_by=self.emp.user)
        with self.assertRaises(ValidationError):
            add_line(claim, Decimal("5"), date(2026, 10, 6), ot_line=ot)

    def test_ot_line_cannot_be_on_two_live_claims(self):
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        first = CTOClaim.objects.create(employee=self.emp, filed_by=self.emp.user)
        add_line(first, Decimal("8"), date(2026, 10, 6), ot_line=ot)
        second = CTOClaim.objects.create(employee=self.emp, filed_by=self.emp.user)
        with self.assertRaises(ValidationError):
            add_line(second, Decimal("8"), date(2026, 10, 6), ot_line=ot)

    def test_24_hour_cap_across_ot_and_medical_transport(self):
        day = date(2026, 10, 5)
        ot = approved_ot(self.org, self.emp, day, hours="16", today=date(2026, 10, 1))
        filed_claim(self.org, self.emp, [(ot, "16", None, None)], date(2026, 10, 6))
        mt = CTOClaim.objects.create(employee=self.emp, duty_type=CTOClaim.DUTY_MEDICAL_TRANSPORT, filed_by=self.emp.user)
        add_line(mt, Decimal("8"), date(2026, 10, 6), work_date=day, day_type=WEEKDAY)  # 24 total: OK
        mt2 = CTOClaim.objects.create(employee=self.emp, duty_type=CTOClaim.DUTY_MEDICAL_TRANSPORT, filed_by=self.emp.user)
        with self.assertRaises(ValidationError):
            add_line(mt2, Decimal("0.5"), date(2026, 10, 6), work_date=day, day_type=WEEKDAY)

    def test_line_past_its_deadline_cannot_be_added(self):
        claim = CTOClaim.objects.create(employee=self.emp, duty_type=CTOClaim.DUTY_MEDICAL_TRANSPORT, filed_by=self.emp.user)
        with self.assertRaises(ValidationError):
            add_line(claim, Decimal("8"), date(2026, 12, 1), work_date=date(2026, 10, 5), day_type=WEEKDAY)

    def test_documents_are_required_to_submit(self):
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = CTOClaim.objects.create(employee=self.emp, filed_by=self.emp.user)
        add_line(claim, Decimal("8"), date(2026, 10, 6), ot_line=ot)
        with self.assertRaises(ValidationError):
            submit_claim(claim, self.emp.user, date(2026, 10, 6))

    def test_nov_29_ot_filed_nov_30_is_credited_dec_1_as_this_years_cto(self):
        ot = approved_ot(self.org, self.emp, date(2026, 11, 29), today=date(2026, 11, 20))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 11, 30))
        with on(date(2026, 11, 30)):
            approve_all(claim, on_approved=credit_lines)
        line = claim.lines.get()
        self.assertEqual(line.effective_date, date(2026, 12, 1))
        self.assertEqual(line.year_earned, 2026)
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 11, 30)), Decimal("0"))
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 12, 1)), Decimal("1.00"))

    def test_december_ot_is_credited_jan_1_as_next_years_cto(self):
        ot = approved_ot(self.org, self.emp, date(2026, 12, 3), today=date(2026, 12, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 12, 4))
        with on(date(2026, 12, 10)):
            approve_all(claim, on_approved=credit_lines)
        line = claim.lines.get()
        self.assertEqual(line.effective_date, date(2027, 1, 1))
        self.assertEqual(line.year_earned, 2027)
        self.assertEqual(filing_deadline_for(line.work_date), date(2027, 11, 30))

    def test_late_coh_approval_sets_effective_date_after_dec_15(self):
        # Carry-over without a reason for such credits is Stage 2 (forfeiture job).
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 11, 28))
        with on(date(2026, 12, 20)):
            approve_all(claim, on_approved=credit_lines)
        line = claim.lines.get()
        self.assertEqual(line.effective_date, date(2026, 12, 20))
        self.assertEqual(line.year_earned, 2026)

    def test_returned_claim_keeps_its_first_filing_date(self):
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 11, 28))
        routing.return_record(claim, self.org.lab_sup, self.org.lab_sup.user, "Attach a clearer DTR")
        submit_claim(claim, self.emp.user, date(2026, 12, 3))  # after Nov 30
        claim.refresh_from_db()
        self.assertEqual(claim.first_filed_on, date(2026, 11, 28))
        self.assertEqual(claim.status, RoutedRecord.PENDING)
        self.assertEqual(claim.current_step, "SUPERVISOR")

    def test_rest_day_credit_uses_rate_in_force_on_the_workday(self):
        make_rate(effective_date=date(2026, 10, 10), weekday="1.00", restday="2.00")
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), day_type=REST_DAY, today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 10, 20))
        with on(date(2026, 10, 25)):
            approve_all(claim, on_approved=credit_lines)
        line = claim.lines.get()
        self.assertEqual(line.multiplier_applied, Decimal("1.50"))
        self.assertEqual(line.credited_days, Decimal("1.50"))

    def test_day_type_correction_recalculates_and_leaves_ot_line_unchanged(self):
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 10, 6))
        routing.approve_step(claim, self.org.lab_sup, self.org.lab_sup.user)
        claim.refresh_from_db()
        line = claim.lines.get()
        with self.assertRaises(ValidationError):  # remark required
            correct_day_type(line, HOLIDAY, "", self.org.hr, self.org.hr.user)
        correct_day_type(line, HOLIDAY, "Oct 5 was a declared holiday", self.org.hr, self.org.hr.user)
        line.refresh_from_db()
        ot.refresh_from_db()
        self.assertEqual(line.day_type, HOLIDAY)
        self.assertEqual(line.credited_days, Decimal("1.50"))
        self.assertEqual(ot.day_type, WEEKDAY)
        self.assertTrue(claim.actions.filter(action="correct_day_type", notes__contains="declared holiday").exists())

    def test_day_type_cannot_be_corrected_outside_hr_step(self):
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 10, 6))
        with self.assertRaises(ValidationError):
            correct_day_type(claim.lines.get(), HOLIDAY, "x", self.org.hr, self.org.hr.user)

    def test_one_claim_with_several_lines(self):
        ot1 = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        ot2 = approved_ot(self.org, self.emp, date(2026, 11, 5), today=date(2026, 11, 1))
        claim = filed_claim(self.org, self.emp, [(ot1, "8", None, None), (ot2, "4", None, None)], date(2026, 11, 6))
        with on(date(2026, 11, 10)):
            approve_all(claim, on_approved=credit_lines)
        self.assertEqual(sorted(claim.lines.values_list("effective_date", flat=True)),
                         [date(2026, 11, 10), date(2026, 12, 1)])


class UsageAllocationTests(TestCase):
    """Per-line credit: oldest first, pending usage held aside, and use
    dated on or before Dec 15 stays against that year's credit even if
    approved later."""

    def setUp(self):
        self.org = Org()
        self.emp = self.org.lab_staff
        RoleAssignment.objects.create(employee=self.org.lab_sup, role=RoleAssignment.SUPERVISOR)
        self.client = Client()

    def _credit(self, work_date, hours, approved_on):
        ot = approved_ot(self.org, self.emp, work_date, hours=hours, today=work_date)
        claim = filed_claim(self.org, self.emp, [(ot, hours, None, None)], work_date)
        with on(approved_on):
            approve_all(claim, on_approved=credit_lines)
        return claim.lines.get()

    def _apply(self, start, end, days, today):
        self.client.force_login(self.emp.user)
        with on(today):
            return self.client.post(reverse("cto:cto_apply"), {
                "start_date": start, "end_date": end, "number_of_days": days, "reason": "",
            })

    def test_oldest_credit_is_used_first_and_pending_days_are_held(self):
        old = self._credit(date(2026, 8, 3), "8", date(2026, 8, 10))   # effective Sep 1
        new = self._credit(date(2026, 9, 7), "8", date(2026, 9, 10))   # effective Oct 1
        self._apply("2026-11-10", "2026-11-11", "1.5", date(2026, 11, 2))
        app = CTOUsageApplication.objects.get()
        self.assertEqual(app.status, CTOUsageApplication.SUBMITTED)
        self.assertEqual(old.allocations.get().days, Decimal("1.00"))
        self.assertEqual(new.allocations.get().days, Decimal("0.50"))
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 11, 2)), Decimal("0.50"))

    def test_insufficient_balance_is_refused_at_filing(self):
        self._credit(date(2026, 8, 3), "8", date(2026, 8, 10))
        self._apply("2026-11-10", "2026-11-11", "2", date(2026, 11, 2))
        self.assertFalse(CTOUsageApplication.objects.exists())

    def test_returned_usage_releases_the_hold(self):
        self._credit(date(2026, 8, 3), "8", date(2026, 8, 10))
        self._apply("2026-11-10", "2026-11-10", "1", date(2026, 11, 2))
        app = CTOUsageApplication.objects.get()
        self.client.force_login(self.org.lab_sup.user)
        self.client.post(reverse("cto:cto_action", args=[app.pk]), {"action": "return"})
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 11, 2)), Decimal("1.00"))

    def test_use_dated_by_dec_15_approved_later_counts_against_that_year(self):
        line = self._credit(date(2026, 8, 3), "8", date(2026, 8, 10))
        self._apply("2026-12-14", "2026-12-14", "1", date(2026, 12, 10))
        app = CTOUsageApplication.objects.get()
        for user, action in [(self.org.lab_sup, "endorse"), (self.org.hr, "process"),
                             (self.org.ao, "recommend"), (self.org.coh, "approve")]:
            self.client.force_login(user.user)
            with on(date(2026, 12, 20)):
                self.client.post(reverse("cto:cto_action", args=[app.pk]), {"action": action})
        app.refresh_from_db()
        self.assertEqual(app.status, CTOUsageApplication.APPROVED)
        self.assertEqual(line.allocations.get().days, Decimal("1.00"))
        self.assertEqual(line.year_earned, 2026)

    def test_credit_not_yet_effective_cannot_be_used(self):
        self._credit(date(2026, 11, 3), "8", date(2026, 11, 5))  # effective Dec 1
        self._apply("2026-11-20", "2026-11-20", "1", date(2026, 11, 10))
        self.assertFalse(CTOUsageApplication.objects.exists())


class PageSmokeTests(TestCase):
    """Every new page renders for the people who use it."""

    def setUp(self):
        self.org = Org()
        emp = self.org.lab_staff
        ot = approved_ot(self.org, emp, date(2026, 10, 5), today=date(2026, 10, 1))
        self.ot = ot.request
        self.claim = filed_claim(self.org, emp, [(ot, "8", None, None)], date(2026, 10, 6))
        self.client = Client()

    def _get(self, employee, name, *args):
        self.client.force_login(employee.user)
        response = self.client.get(reverse(name, args=args))
        self.assertEqual(response.status_code, 200, name)
        return response

    def test_pages(self):
        self._get(self.org.hr, "cto:ot_new")
        self._get(self.org.hr, "cto:ot_detail", self.ot.pk)
        self._get(self.org.lab_sup, "cto:cto_queue")
        self._get(self.org.lab_sup, "cto:claim_detail", self.claim.pk)
        self._get(self.org.lab_staff, "cto:my_cto")
        self._get(self.org.lab_staff, "cto:claim_new")
        self._get(self.org.lab_staff, "cto:cto_apply")
        self._get(self.org.hr_admin, "cto:exception_claim_new")

    def test_ot_form_has_add_row_template(self):
        response = self._get(self.org.hr, "cto:ot_new")
        self.assertContains(response, "__prefix__")
        self.assertContains(response, 'name="lines-TOTAL_FORMS"')

    def test_draft_claim_page_shows_add_line_form(self):
        draft = CTOClaim.objects.create(employee=self.org.lab_staff2, filed_by=self.org.lab_staff2.user)
        response = self._get(self.org.lab_staff2, "cto:claim_detail", draft.pk)
        self.assertContains(response, "add_line")

    def test_employee_cannot_open_ot_form(self):
        self.client.force_login(self.org.lab_staff.user)
        self.assertEqual(self.client.get(reverse("cto:ot_new")).status_code, 403)


class HardeningTests(TestCase):
    def setUp(self):
        self.org = Org()
        self.emp = self.org.lab_staff

    # 1. same employee + date on another live OT request
    def test_same_employee_and_date_blocked_on_second_pending_or_approved_request(self):
        file_ot(self.org.hr, "HR", "x", "", [ot_line(self.emp, date(2026, 11, 5))], self.org.hr.user, date(2026, 11, 1))
        with self.assertRaises(ValidationError):  # first is pending
            file_ot(self.org.lab_sup, "SUPERVISOR", "x", "", [ot_line(self.emp, date(2026, 11, 5))],
                    self.org.lab_sup.user, date(2026, 11, 1))
        approve_all(OvertimeRequest.objects.get())
        with self.assertRaises(ValidationError):  # first is approved
            file_ot(self.org.lab_sup, "SUPERVISOR", "x", "", [ot_line(self.emp, date(2026, 11, 5))],
                    self.org.lab_sup.user, date(2026, 11, 1))

    def test_returned_request_does_not_block_and_its_own_lines_are_not_duplicates(self):
        ot, _ = file_ot(self.org.hr, "HR", "x", "", [ot_line(self.emp, date(2026, 11, 5))], self.org.hr.user, date(2026, 11, 1))
        routing.return_record(ot, self.org.ao, self.org.ao.user, "fix hours")
        file_ot(self.org.hr, "HR", "x", "", [ot_line(self.emp, date(2026, 11, 5), "4")], self.org.hr.user,
                date(2026, 11, 1), ot=ot)  # re-filing the same line on the same request is fine

    # 2. HR Administrator cannot file an exception claim for themselves
    def test_hr_admin_cannot_file_exception_claim_for_self(self):
        from .claims import create_exception_claim

        with self.assertRaises(ValidationError):
            create_exception_claim(self.org.hr_admin, self.org.hr_admin, CTOClaim.DUTY_REGULAR, "x", self.org.hr_admin.user)
        with self.assertRaises(ValidationError):  # HR Processor is not enough
            create_exception_claim(self.org.hr, self.emp, CTOClaim.DUTY_REGULAR, "x", self.org.hr.user)
        with self.assertRaises(ValidationError):  # reason required
            create_exception_claim(self.org.hr_admin, self.emp, CTOClaim.DUTY_REGULAR, " ", self.org.hr_admin.user)
        claim = create_exception_claim(self.org.hr_admin, self.emp, CTOClaim.DUTY_REGULAR, "Emergency", self.org.hr_admin.user)
        self.assertTrue(claim.is_exception())

    # 3. approval + crediting is all-or-nothing
    def test_missing_rate_at_coh_approval_leaves_claim_pending_and_uncredited(self):
        from .claims import approve_claim

        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 10, 6))
        for actor in (self.org.lab_sup, self.org.hr, self.org.ao):
            approve_claim(claim, actor, actor.user)
            claim.refresh_from_db()
        self.assertEqual(claim.current_step, "COH")
        CTOMultiplierRate.objects.all().delete()
        with self.assertRaises(ValidationError):
            approve_claim(claim, self.org.coh, self.org.coh.user)
        claim.refresh_from_db()
        self.assertEqual(claim.status, RoutedRecord.PENDING)
        self.assertEqual(claim.current_step, "COH")
        self.assertIsNone(claim.approved_on)
        self.assertFalse(claim.actions.filter(step="COH").exists())
        line = claim.lines.get()
        self.assertEqual(line.state, CTOClaimLine.PENDING)
        self.assertIsNone(line.effective_date)

    # 5. NURSING_SECTION_NAME matches a real section
    def test_nursing_section_setting_matches_a_section(self):
        from django.conf import settings

        self.assertTrue(Section.objects.filter(name=settings.NURSING_SECTION_NAME).exists())

    # 7. no deletes from admin
    def test_admin_cannot_delete_ot_requests_claims_or_lines(self):
        from django.contrib import admin
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser("root", "r@x.test", "x")
        for model in (OvertimeRequest, CTOClaim):
            self.assertFalse(admin.site._registry[model].has_delete_permission(request))
        for model_admin in (admin.site._registry[OvertimeRequest], admin.site._registry[CTOClaim]):
            for inline in model_admin.get_inline_instances(request):
                if inline.model in (CTOClaimLine,) or inline.model.__name__ == "OvertimeLine":
                    self.assertFalse(inline.has_delete_permission(request))


class PermissionViewTests(TestCase):
    """6. Who can view and act on OT requests and claims."""

    def setUp(self):
        self.org = Org()
        self.outsider = make_employee("outsider", "E-OUT")
        self.ot, _ = file_ot(self.org.lab_sup, "SUPERVISOR", "x", "", [ot_line(self.org.lab_staff, date(2099, 1, 5))],
                             self.org.lab_sup.user, date(2099, 1, 1))
        self.client = Client()

    def _as(self, employee):
        self.client.force_login(employee.user)
        return self.client

    def test_who_can_view_an_ot_request(self):
        url = reverse("cto:ot_detail", args=[self.ot.pk])
        for allowed in (self.org.lab_sup, self.org.hr, self.org.ao, self.org.coh):
            self.assertEqual(self._as(allowed).get(url).status_code, 200, allowed)
        self.assertEqual(self._as(self.outsider).get(url).status_code, 403)

    def test_only_the_current_step_can_approve_an_ot_request(self):
        url = reverse("cto:ot_action", args=[self.ot.pk])
        for wrong in (self.org.ao, self.org.coh, self.outsider, self.org.lab_sup, self.org.lab_staff):
            self._as(wrong).post(url, {"action": "approve"})
            self.ot.refresh_from_db()
            self.assertEqual(self.ot.current_step, "HR", wrong)
        self._as(self.org.hr).post(url, {"action": "approve"})
        self.ot.refresh_from_db()
        self.assertEqual(self.ot.current_step, "AO")

    def test_only_the_filer_can_edit_a_returned_ot_request(self):
        routing.return_record(self.ot, self.org.hr, self.org.hr.user, "wrong hours")
        url = reverse("cto:ot_edit", args=[self.ot.pk])
        self.assertEqual(self._as(self.org.hr).get(url).status_code, 403)
        self.assertEqual(self._as(self.org.lab_staff).get(url).status_code, 403)
        self.assertEqual(self._as(self.org.lab_sup).get(url).status_code, 200)

    def test_pending_ot_request_cannot_be_edited(self):
        self.assertEqual(self._as(self.org.lab_sup).get(reverse("cto:ot_edit", args=[self.ot.pk])).status_code, 404)

    def test_who_can_view_and_act_on_a_claim(self):
        ot = approved_ot(self.org, self.org.lab_staff, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.org.lab_staff, [(ot, "8", None, None)], date(2026, 10, 6))
        view = reverse("cto:claim_detail", args=[claim.pk])
        act = reverse("cto:claim_action", args=[claim.pk])
        for allowed in (self.org.lab_staff, self.org.lab_sup, self.org.hr, self.org.ao, self.org.coh):
            self.assertEqual(self._as(allowed).get(view).status_code, 200, allowed)
        self.assertEqual(self._as(self.outsider).get(view).status_code, 403)
        for wrong in (self.org.lab_staff, self.org.hr, self.outsider):
            self._as(wrong).post(act, {"action": "approve"})
            claim.refresh_from_db()
            self.assertEqual(claim.current_step, "SUPERVISOR", wrong)
        # The claimant cannot edit lines once submitted.
        self.assertEqual(self._as(self.org.lab_staff).post(act, {"action": "add_line"}).status_code, 403)
        self._as(self.org.lab_sup).post(act, {"action": "approve"})
        claim.refresh_from_db()
        self.assertEqual(claim.current_step, "HR")

    def test_returned_claim_can_be_edited_only_by_the_claimant(self):
        ot = approved_ot(self.org, self.org.lab_staff, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.org.lab_staff, [(ot, "8", None, None)], date(2026, 10, 6))
        routing.return_record(claim, self.org.lab_sup, self.org.lab_sup.user, "clearer DTR")
        act = reverse("cto:claim_action", args=[claim.pk])
        line = claim.lines.get()
        self.assertEqual(self._as(self.org.lab_sup).post(act, {"action": "remove_line", "line": line.pk}).status_code, 403)
        self._as(self.org.lab_staff).post(act, {"action": "remove_line", "line": line.pk})
        self.assertFalse(claim.lines.exists())


class UsageLimitViewTests(TestCase):
    """8. The 5-days-per-month and no-3-consecutive-days limits still apply
    at filing, on top of the per-line balance."""

    def setUp(self):
        self.org = Org()
        self.emp = self.org.lab_staff
        for month in (6, 7, 8):  # plenty of credit
            ot = approved_ot(self.org, self.emp, date(2026, month, 3), hours="24", today=date(2026, month, 3))
            claim = filed_claim(self.org, self.emp, [(ot, "24", None, None)], date(2026, month, 3))
            with on(date(2026, month, 10)):
                approve_all(claim, on_approved=credit_lines)
        self.client = Client()
        self.client.force_login(self.emp.user)

    def _apply(self, start, end, days):
        with on(date(2026, 10, 1)):
            self.client.post(reverse("cto:cto_apply"), {"start_date": start, "end_date": end, "number_of_days": days})

    def test_more_than_5_days_in_a_month_is_refused(self):
        self._apply("2026-10-05", "2026-10-06", "2")
        self._apply("2026-10-12", "2026-10-13", "2")
        self._apply("2026-10-19", "2026-10-20", "2")  # would be 6
        self.assertEqual(CTOUsageApplication.objects.count(), 2)

    def test_three_consecutive_days_are_refused(self):
        self._apply("2026-10-05", "2026-10-06", "2")
        self._apply("2026-10-07", "2026-10-07", "1")
        self.assertEqual(CTOUsageApplication.objects.count(), 1)


class OpeningBalanceTests(TestCase):
    """CLAUDE.md §7.1 Option A."""

    TODAY = date(2026, 11, 10)

    def setUp(self):
        self.org = Org()
        self.emp = self.org.lab_staff

    def _add(self, actor=None, employee=None, days="2", earned=date(2026, 6, 1), reason="Paper CTO card p.3", today=None):
        from .opening import add_opening_line

        actor = actor or self.org.hr_admin
        return add_opening_line(actor, employee or self.emp, Decimal(days), earned, reason, actor.user, today or self.TODAY)

    def test_several_lines_each_with_own_date_and_year_count_from_entry(self):
        a = self._add(days="1.5", earned=date(2026, 3, 2))
        b = self._add(days="2", earned=date(2026, 9, 14), reason="Logbook Sept")
        for line in (a, b):
            self.assertEqual(line.state, CTOClaimLine.CREDITED)
            self.assertEqual(line.effective_date, self.TODAY)
            self.assertEqual(line.year_earned, 2026)
        self.assertEqual(a.claim, b.claim)
        self.assertTrue(a.claim.is_opening)
        self.assertEqual(compute_available_cto_balance(self.emp, self.TODAY), Decimal("3.50"))
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 11, 9)), Decimal("0"))
        self.assertEqual(a.claim.actions.filter(action="opening_line_added").count(), 2)

    def test_reason_required(self):
        with self.assertRaises(ValidationError):
            self._add(reason="  ")

    def test_date_earned_must_be_in_2026(self):
        with self.assertRaises(ValidationError):
            self._add(earned=date(2025, 12, 20))

    def test_hr_admin_cannot_enter_for_self_and_others_cannot_enter(self):
        with self.assertRaises(ValidationError):
            self._add(employee=self.org.hr_admin)
        for actor in (self.org.hr, self.org.ao, self.org.lab_sup):  # another HR Admin exists for these targets
            with self.assertRaises(ValidationError):
                self._add(actor=actor)

    def test_ao_enters_when_no_other_hr_administrator(self):
        line = self._add(actor=self.org.ao, employee=self.org.hr_admin)
        self.assertEqual(line.claim.employee, self.org.hr_admin)
        hr_admin2 = make_employee("hradmin2", "E-HRA2", role=RoleAssignment.HR_ADMINISTRATOR)
        with self.assertRaises(ValidationError):  # now another HR Admin exists
            self._add(actor=self.org.ao, employee=self.org.hr_admin)
        self._add(actor=hr_admin2, employee=self.org.hr_admin)

    def test_void_with_reason_then_re_enter(self):
        from .opening import void_opening_line

        line = self._add(days="3")
        with self.assertRaises(ValidationError):
            void_opening_line(self.org.hr_admin, line, "", self.org.hr_admin.user, self.TODAY)
        void_opening_line(self.org.hr_admin, line, "Typed 3 instead of 2", self.org.hr_admin.user, self.TODAY)
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.VOIDED)
        self.assertEqual(line.credited_days, Decimal("3"))  # not edited in place
        self._add(days="2")
        self.assertEqual(compute_available_cto_balance(self.emp, self.TODAY), Decimal("2"))
        self.assertTrue(line.claim.actions.filter(action="opening_line_voided", notes__contains="Typed 3").exists())

    def test_void_blocked_when_days_are_held_against_the_line(self):
        from .opening import void_opening_line

        line = self._add(days="2")
        client = Client()
        client.force_login(self.emp.user)
        with on(self.TODAY):
            client.post(reverse("cto:cto_apply"), {"start_date": "2026-11-20", "end_date": "2026-11-20",
                                                   "number_of_days": "1"})
        self.assertTrue(line.allocations.exists())
        with self.assertRaises(ValidationError):
            void_opening_line(self.org.hr_admin, line, "wrong", self.org.hr_admin.user, self.TODAY)

    def test_locked_after_the_cutoff_date(self):
        from .opening import void_opening_line

        line = self._add(today=date(2026, 11, 30))  # the cutoff day itself is still open
        with self.assertRaises(ValidationError):
            self._add(today=date(2026, 12, 1))
        with self.assertRaises(ValidationError):
            void_opening_line(self.org.hr_admin, line, "late fix", self.org.hr_admin.user, date(2026, 12, 1))

    @override_settings(CTO_OPENING_BALANCE_DATE=date(2026, 10, 31))
    def test_lock_date_comes_from_settings(self):
        with self.assertRaises(ValidationError):
            self._add(today=date(2026, 11, 1))

    def test_one_opening_record_per_employee_in_the_database(self):
        from django.db import IntegrityError

        self._add()
        with self.assertRaises(IntegrityError):
            CTOClaim.objects.create(employee=self.emp, is_opening=True, filed_by=self.org.hr_admin.user,
                                    status=RoutedRecord.APPROVED)

    def test_opening_record_never_shows_as_a_pending_claim(self):
        self._add()
        client = Client()
        for viewer in (self.org.lab_sup, self.org.hr, self.org.hr_admin, self.org.ao, self.org.coh):
            client.force_login(viewer.user)
            response = client.get(reverse("cto:cto_queue"))
            self.assertEqual(response.context["pending_claims"], [], viewer)
        client.force_login(self.emp.user)
        response = client.get(reverse("cto:my_cto"))
        self.assertFalse(any(c.is_opening for c in response.context["claims"]))
        self.assertEqual(len(response.context["credit_lines"]), 1)  # shown as credit instead

    def test_opening_lines_show_with_readable_labels_in_audit_log(self):
        from auditlog.aggregation import audit_rows
        from .opening import void_opening_line

        line = self._add()
        void_opening_line(self.org.hr_admin, line, "wrong", self.org.hr_admin.user, self.TODAY)
        actions = {r["action"] for r in audit_rows(employee_id=self.emp.pk)}
        self.assertIn("Opening CTO line added", actions)
        self.assertIn("Opening CTO line voided", actions)

    def test_opening_page(self):
        client = Client()
        client.force_login(self.org.hr_admin.user)
        with on(self.TODAY):
            response = client.post(reverse("cto:opening_balances"), {
                "employee": self.emp.pk, "action": "add", "days": "1", "date_earned": "2026-05-04",
                "reason": "Paper card",
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get(reverse("cto:opening_balances") + f"?employee={self.emp.pk}").status_code, 200)
        client.force_login(self.emp.user)
        self.assertEqual(client.get(reverse("cto:opening_balances")).status_code, 403)


class NursingSectionSettingTests(TestCase):
    def setUp(self):
        self.org = Org()

    @override_settings(NURSING_SECTION_NAME="Nursing Services (renamed)")
    def test_claim_submission_fails_loudly_when_section_name_matches_nothing(self):
        ot = approved_ot(self.org, self.org.nurse, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = CTOClaim.objects.create(employee=self.org.nurse, filed_by=self.org.nurse.user)
        add_line(claim, Decimal("8"), date(2026, 10, 6), ot_line=ot)
        upload_all_required(claim, self.org.nurse.user)
        with self.assertRaises(ValidationError) as ctx:
            submit_claim(claim, self.org.nurse.user, date(2026, 10, 6))
        self.assertIn("HR Administrator", str(ctx.exception))
        claim.refresh_from_db()
        self.assertEqual(claim.status, RoutedRecord.DRAFT)
        self.assertIsNone(claim.first_filed_on)

    @override_settings(NURSING_SECTION_NAME="Nursing Services (renamed)")
    def test_supervisor_ot_filing_fails_loudly_too(self):
        with self.assertRaises(ValidationError):
            file_ot(self.org.er_head, "SUPERVISOR", "x", "", [ot_line(self.org.nurse, date(2099, 1, 5))],
                    self.org.er_head.user, date(2099, 1, 1))
        self.assertFalse(OvertimeRequest.objects.exists())


class ExceptionClaimDeadlineTests(TestCase):
    """Decided 2026-11-01 (Q4): exception claims skip the filing deadline;
    other claims don't."""

    def setUp(self):
        self.org = Org()

    def test_exception_claim_accepts_old_work(self):
        from .claims import create_exception_claim

        make_rate(effective_date=date(2025, 1, 1))
        claim = create_exception_claim(self.org.hr_admin, self.org.lab_staff, CTOClaim.DUTY_REGULAR,
                                       "2025 paper CTO card, memo 12", self.org.hr_admin.user)
        add_line(claim, Decimal("8"), date(2026, 12, 2), work_date=date(2025, 3, 10), day_type=WEEKDAY)
        upload_all_required(claim, self.org.hr_admin.user)
        submit_claim(claim, self.org.hr_admin.user, date(2026, 12, 2))
        claim.refresh_from_db()
        self.assertEqual(claim.status, RoutedRecord.PENDING)

    def test_ordinary_medical_transport_claim_still_enforces_the_deadline(self):
        claim = CTOClaim.objects.create(employee=self.org.lab_staff, duty_type=CTOClaim.DUTY_MEDICAL_TRANSPORT,
                                        filed_by=self.org.lab_staff.user)
        with self.assertRaises(ValidationError):
            add_line(claim, Decimal("8"), date(2026, 12, 2), work_date=date(2026, 3, 10), day_type=WEEKDAY)


class FreshDatabaseSeedTests(TestCase):
    """A fresh database (migrations only) has what CTO needs on day one."""

    def test_multiplier_rate_seeded(self):
        rate = CTOMultiplierRate.active_as_of(date(2026, 11, 1))
        self.assertEqual((rate.weekday_multiplier, rate.restday_holiday_multiplier), (Decimal("1.00"), Decimal("1.50")))

    def test_claim_document_requirements_seeded(self):
        ct = ContentType.objects.get_for_model(CTOClaim)
        regular = set(DocumentRequirement.objects.filter(content_type=ct, sub_type_value="REGULAR")
                      .values_list("label", flat=True))
        self.assertEqual(regular, {"Allowed to Work form", "DTR/logbook copy", "OT Accomplishment Report"})
        mt = DocumentRequirement.objects.filter(content_type=ct, sub_type_value="MEDICAL_TRANSPORT")
        self.assertEqual(mt.count(), 3)
        self.assertEqual(mt.filter(alternative_group="cert_or_logbook").count(), 2)


class DryRunChecklistTests(TestCase):
    """Behaviour the dry-run checklist asks testers to confirm."""

    def setUp(self):
        self.org = Org()
        self.emp = self.org.lab_staff

    def test_claim_for_a_future_workday_is_refused(self):
        ot = approved_ot(self.org, self.emp, date(2026, 11, 6), today=date(2026, 11, 3))
        claim = CTOClaim.objects.create(employee=self.emp, filed_by=self.emp.user)
        with self.assertRaises(ValidationError):
            add_line(claim, Decimal("8"), date(2026, 11, 3), ot_line=ot)
        add_line(claim, Decimal("8"), date(2026, 11, 6), ot_line=ot)  # fine once the day arrives

    def test_same_day_claim_is_credited_from_the_first_of_next_month(self):
        ot = approved_ot(self.org, self.emp, date(2026, 11, 3), today=date(2026, 11, 3))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 11, 3))
        with on(date(2026, 11, 3)):
            approve_all(claim, on_approved=credit_lines)
        self.assertEqual(claim.lines.get().effective_date, date(2026, 12, 1))
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 11, 30)), Decimal("0"))

    def test_usage_ending_after_dec_15_is_refused(self):
        from .opening import add_opening_line

        add_opening_line(self.org.hr_admin, self.emp, Decimal("3"), date(2026, 6, 1), "card", self.org.hr_admin.user,
                         date(2026, 11, 10))
        client = Client()
        client.force_login(self.emp.user)
        with on(date(2026, 12, 1)):
            client.post(reverse("cto:cto_apply"), {"start_date": "2026-12-16", "end_date": "2026-12-16",
                                                   "number_of_days": "1"})
        self.assertFalse(CTOUsageApplication.objects.exists())


# --- Stage 2: carry-over, year-end, reminders, separation -----------------

def opening_credit(org, employee, days="2", earned=date(2026, 6, 1), entered=date(2026, 11, 10)):
    from .opening import add_opening_line

    actor = org.hr_admin if employee.pk != org.hr_admin.pk else org.ao
    return add_opening_line(actor, employee, Decimal(days), earned, "paper card", actor.user, entered)


def apply_cto(employee, start, end, days, today):
    client = Client()
    client.force_login(employee.user)
    with on(today):
        return client.post(reverse("cto:cto_apply"), {"start_date": start, "end_date": end, "number_of_days": days})


class CarryOverReasonTests(TestCase):
    def setUp(self):
        from .carryover import reason_submitters, submit_reason

        self.org = Org()
        self.emp = self.org.lab_staff
        self.line = opening_credit(self.org, self.emp)
        self.submitters = reason_submitters
        self.submit = submit_reason

    def test_use_deadline_is_dec_15_of_year_earned(self):
        self.assertEqual(self.line.use_deadline, date(2026, 12, 15))

    def test_who_submits(self):
        org = self.org
        self.assertEqual(self.submitters(org.lab_staff), {org.lab_sup})
        self.assertEqual(self.submitters(org.nurse), {org.er_head})            # Unit Head
        self.assertEqual(self.submitters(org.er_head), {org.chief_nurse})      # no other Unit Head -> Chief Nurse
        self.assertEqual(self.submitters(org.chief_nurse), {org.hr_admin})     # no one covers -> HR Administrator
        self.assertEqual(self.submitters(org.hr_admin), {org.ao})              # HR Admin's own -> AO
        self.assertEqual(self.submitters(org.ao), {org.hr_admin})              # AO's own -> HR Admin
        for e in (org.lab_staff, org.nurse, org.er_head, org.chief_nurse, org.hr_admin, org.ao):
            self.assertNotIn(e, self.submitters(e))

    def test_submit_by_dec_16_only_and_once(self):
        with self.assertRaises(ValidationError):  # not a submitter for this employee
            self.submit(self.org.er_head, self.emp, 2026, "x", date(2026, 12, 5))
        with self.assertRaises(ValidationError):  # too late
            self.submit(self.org.lab_sup, self.emp, 2026, "x", date(2026, 12, 17))
        with self.assertRaises(ValidationError):  # blank
            self.submit(self.org.lab_sup, self.emp, 2026, " ", date(2026, 12, 5))
        self.submit(self.org.lab_sup, self.emp, 2026, "Short-staffed in December", date(2026, 12, 16))
        with self.assertRaises(ValidationError):  # one per employee per year
            self.submit(self.org.lab_sup, self.emp, 2026, "again", date(2026, 12, 16))

    def test_submitting_notifies_employee_and_coh(self):
        self.submit(self.org.lab_sup, self.emp, 2026, "Short-staffed", date(2026, 12, 5))
        self.assertTrue(Notification.objects.filter(recipient=self.emp, message__contains="carry").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.org.coh, message__contains="awaits your decision").exists())

    def test_only_coh_decides(self):
        from .carryover import decide_reason

        record = self.submit(self.org.lab_sup, self.emp, 2026, "x", date(2026, 12, 5))
        with self.assertRaises(ValidationError):
            decide_reason(self.org.ao, record, True, "", date(2026, 12, 6))


class YearEndTests(TestCase):
    def setUp(self):
        self.org = Org()
        self.emp = self.org.lab_staff

    def _year_end(self, today=date(2026, 12, 17)):
        from .carryover import run_year_end

        return run_year_end(today)

    def _reason(self, today=date(2026, 12, 10)):
        from .carryover import submit_reason

        return submit_reason(self.org.lab_sup, self.emp, 2026, "Short-staffed", today)

    def _decide(self, record, approve, today):
        from .carryover import decide_reason

        return decide_reason(self.org.coh, record, approve, "", today)

    def test_does_nothing_before_dec_17(self):
        line = opening_credit(self.org, self.emp)
        self.assertEqual(self._year_end(date(2026, 12, 16)), {"carried": 0, "pending": 0, "forfeited": 0})
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CREDITED)

    def test_missed_dec_17_self_heals_on_a_later_day(self):
        line = opening_credit(self.org, self.emp)
        self._year_end(date(2027, 1, 5))
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.FORFEITED)

    def test_stale_credit_after_separation_cleared_is_closed_out(self):
        from .separation import set_separation

        line = opening_credit(self.org, self.emp)
        set_separation(self.org.hr, self.emp, Employee.SEPARATION_RESIGNED, date(2027, 3, 1), "memo", self.org.hr.user)
        self._year_end()  # skipped while separating
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CREDITED)
        set_separation(self.org.hr, self.emp, "", None, "withdrew resignation", self.org.hr.user)
        self.emp.refresh_from_db()
        self._year_end(date(2027, 2, 1))  # 2026 deadline long past, no reason
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.FORFEITED)

    def test_no_reason_by_dec_16_is_forfeited_on_dec_17_and_employee_told(self):
        line = opening_credit(self.org, self.emp, days="2")
        apply_cto(self.emp, "2026-12-01", "2026-12-01", "0.5", date(2026, 11, 20))  # 0.5 held
        self.assertEqual(self._year_end()["forfeited"], 1)
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.FORFEITED)
        self.assertEqual(line.closed_days, Decimal("1.50"))
        self.assertTrue(Notification.objects.filter(recipient=self.emp, message__contains="forfeited").exists())
        self.assertEqual(self._year_end()["forfeited"], 0)  # idempotent

    def test_fully_used_credit_is_untouched(self):
        line = opening_credit(self.org, self.emp, days="1")
        apply_cto(self.emp, "2026-12-01", "2026-12-01", "1", date(2026, 11, 20))
        self._year_end()
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CREDITED)

    def test_use_dated_by_dec_15_approved_after_counts_and_is_not_forfeited(self):
        line = opening_credit(self.org, self.emp, days="1")
        apply_cto(self.emp, "2026-12-14", "2026-12-14", "1", date(2026, 12, 10))
        self._year_end()  # application still pending: its hold protects the day
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CREDITED)

    def test_pending_reason_holds_credit_until_coh_decides(self):
        line = opening_credit(self.org, self.emp)
        record = self._reason()
        self.assertEqual(self._year_end()["pending"], 1)
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CARRY_PENDING)
        self.assertEqual(compute_available_cto_balance(self.emp, date(2026, 12, 18)), Decimal("0"))  # not usable
        self._decide(record, True, date(2026, 12, 20))
        line.refresh_from_db()
        self.assertEqual((line.state, line.use_deadline), (CTOClaimLine.CREDITED, date(2027, 12, 15)))

    def test_coh_rejection_after_dec_15_forfeits_at_once(self):
        line = opening_credit(self.org, self.emp)
        record = self._reason()
        self._year_end()
        self._decide(record, False, date(2026, 12, 20))
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.FORFEITED)
        self.assertTrue(Notification.objects.filter(recipient=self.emp, message__contains="rejected").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.org.lab_sup, message__contains="rejected").exists())

    def test_early_rejection_leaves_credit_usable_until_dec_15_then_forfeited(self):
        from .carryover import submit_reason

        line = opening_credit(self.org, self.emp)
        record = self._reason(date(2026, 12, 5))
        self._decide(record, False, date(2026, 12, 8))
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CREDITED)
        apply_cto(self.emp, "2026-12-14", "2026-12-14", "1", date(2026, 12, 9))
        self.assertEqual(CTOUsageApplication.objects.count(), 1)
        with self.assertRaises(ValidationError):  # rejection is final
            submit_reason(self.org.lab_sup, self.emp, 2026, "please", date(2026, 12, 10))
        self._year_end()
        line.refresh_from_db()
        self.assertEqual((line.state, line.closed_days), (CTOClaimLine.FORFEITED, Decimal("1.00")))

    def test_approved_reason_carries_and_needs_a_new_reason_next_year(self):
        from .carryover import run_year_end, submit_reason

        line = opening_credit(self.org, self.emp)
        self._decide(self._reason(), True, date(2026, 12, 12))
        self.assertEqual(self._year_end()["carried"], 1)
        line.refresh_from_db()
        self.assertEqual(line.use_deadline, date(2027, 12, 15))
        apply_cto(self.emp, "2027-01-11", "2027-01-11", "1", date(2027, 1, 4))  # usable next year
        self.assertEqual(CTOUsageApplication.objects.count(), 1)
        counts = run_year_end(date(2027, 12, 17))  # no 2027 reason
        line.refresh_from_db()
        self.assertEqual((counts["forfeited"], line.state), (1, CTOClaimLine.FORFEITED))

    def test_credit_effective_after_dec_15_carries_over_without_a_reason(self):
        ot = approved_ot(self.org, self.emp, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 11, 28))
        with on(date(2026, 12, 20)):
            approve_all(claim, on_approved=credit_lines)
        line = claim.lines.get()
        self.assertEqual(line.use_deadline, date(2027, 12, 15))
        self._year_end(date(2026, 12, 21))
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CREDITED)
        apply_cto(self.emp, "2026-12-28", "2026-12-28", "1", date(2026, 12, 21))  # usable after Dec 15
        self.assertEqual(CTOUsageApplication.objects.count(), 1)

    def test_december_work_is_not_at_risk_this_year(self):
        ot = approved_ot(self.org, self.emp, date(2026, 12, 2), today=date(2026, 12, 1))
        claim = filed_claim(self.org, self.emp, [(ot, "8", None, None)], date(2026, 12, 3))
        with on(date(2026, 12, 10)):
            approve_all(claim, on_approved=credit_lines)
        self._year_end()
        line = claim.lines.get()
        self.assertEqual((line.state, line.use_deadline), (CTOClaimLine.CREDITED, date(2027, 12, 15)))


class ReminderTests(TestCase):
    def setUp(self):
        from .carryover import send_reminders

        self.org = Org()
        opening_credit(self.org, self.org.lab_staff)
        opening_credit(self.org, self.org.nurse)
        self.send = send_reminders

    def test_only_on_configured_dates(self):
        self.assertIsNone(self.send(date(2026, 12, 2)))
        with override_settings(CTO_CARRYOVER_REMINDER_DATES=[(12, 2)]):
            self.assertIsNotNone(self.send(date(2026, 12, 2)))

    def test_recipients(self):
        from .carryover import submit_reason

        submit_reason(self.org.er_head, self.org.nurse, 2026, "x", date(2026, 12, 1))
        Notification.objects.all().delete()
        summary = self.send(date(2026, 12, 10))
        self.assertEqual(summary["at_risk_without_reason"], 1)  # nurse already has a reason
        self.assertTrue(Notification.objects.filter(recipient=self.org.lab_sup, message__contains=self.org.lab_staff.full_name).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.org.er_head).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.org.hr, message__contains="at-risk").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.org.coh, message__contains="1 reason").exists())

    def test_command_runs(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with on(date(2026, 12, 3)):
            call_command("cto_carryover_reminders", stdout=out)
        self.assertIn("Not a reminder date", out.getvalue())


class SeparationTests(TestCase):
    def setUp(self):
        self.org = Org()
        self.cosp = self.org.lab_staff
        self.cosp.employment_status = Employee.EMPLOYMENT_STATUS_COSP
        self.cosp.save()

    def _set(self, status, last_day, actor=None, employee=None):
        from .separation import set_separation

        actor = actor or self.org.hr
        return set_separation(actor, employee or self.cosp, status, last_day, "Memo 45", actor.user)

    def test_hr_only_valid_combinations_and_audit_logged(self):
        with self.assertRaises(ValidationError):
            self._set(Employee.SEPARATION_NOT_RENEWING, date(2027, 1, 15), actor=self.org.lab_sup)
        with self.assertRaises(ValidationError):  # COSP cannot "retire"
            self._set(Employee.SEPARATION_RETIRED, date(2027, 1, 15))
        with self.assertRaises(ValidationError):  # last day required
            self._set(Employee.SEPARATION_NOT_RENEWING, None)
        self._set(Employee.SEPARATION_NOT_RENEWING, date(2027, 1, 15))
        history = set(self.cosp.edit_history.values_list("field_name", flat=True))
        self.assertEqual(history, {"separation_status", "last_day_of_service"})
        with self.assertRaises(ValidationError):  # Regular cannot be "not renewing"
            self._set(Employee.SEPARATION_NOT_RENEWING, date(2027, 1, 15), employee=self.org.lab_staff2)

    def test_hr_cannot_set_own_separation_status(self):
        with self.assertRaises(ValidationError):
            self._set(Employee.SEPARATION_RESIGNED, date(2027, 1, 15), actor=self.org.hr, employee=self.org.hr)
        self._set(Employee.SEPARATION_RESIGNED, date(2027, 1, 15), actor=self.org.hr_admin, employee=self.org.hr)

    def test_cosp_uses_credit_past_dec_15_without_limits_then_it_lapses(self):
        from .carryover import run_year_end
        from .separation import run_separation_lapse

        line = opening_credit(self.org, self.cosp, days="8")
        ot = approved_ot(self.org, self.cosp, date(2026, 11, 20), today=date(2026, 11, 20))
        claim = filed_claim(self.org, self.cosp, [(ot, "8", None, None)], date(2026, 11, 20))
        with on(date(2026, 11, 25)):
            approve_all(claim, on_approved=credit_lines)  # effective Dec 1
        self._set(Employee.SEPARATION_NOT_RENEWING, date(2026, 12, 31))
        # Effective-date wait waived: Dec 1 credit usable on Nov 26.
        self.assertEqual(compute_available_cto_balance(self.cosp, date(2026, 11, 26)), Decimal("9.00"))
        # Past Dec 15, 3 consecutive days, more than 5 in the month: all allowed.
        apply_cto(self.cosp, "2026-12-21", "2026-12-23", "3", date(2026, 12, 1))
        apply_cto(self.cosp, "2026-12-28", "2026-12-30", "3", date(2026, 12, 1))
        self.assertEqual(CTOUsageApplication.objects.count(), 2)
        apply_cto(self.cosp, "2027-01-04", "2027-01-04", "1", date(2026, 12, 1))  # after last day
        self.assertEqual(CTOUsageApplication.objects.count(), 2)
        run_year_end(date(2026, 12, 17))  # deadline waived: nothing forfeited
        line.refresh_from_db()
        self.assertEqual(line.state, CTOClaimLine.CREDITED)
        self.assertEqual(run_separation_lapse(date(2026, 12, 31)), (0, 0))  # last day itself: not yet
        lines, _ = run_separation_lapse(date(2027, 1, 1))
        self.assertGreaterEqual(lines, 1)
        self.assertEqual(compute_available_cto_balance(self.cosp, date(2027, 1, 1)), Decimal("0"))
        self.assertTrue(Notification.objects.filter(recipient=self.cosp, message__contains="lapsed").exists())

    def test_pending_claim_lapses_after_last_day(self):
        from .separation import run_separation_lapse

        ot = approved_ot(self.org, self.cosp, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.cosp, [(ot, "8", None, None)], date(2026, 10, 6))
        self._set(Employee.SEPARATION_RESIGNED, date(2026, 10, 31))
        run_separation_lapse(date(2026, 11, 1))
        claim.refresh_from_db()
        self.assertEqual(claim.status, RoutedRecord.LAPSED)
        self.assertTrue(claim.actions.filter(action="lapse").exists())

    def test_ot_line_after_last_day_is_blocked(self):
        self._set(Employee.SEPARATION_RESIGNED, date(2026, 11, 15))
        with self.assertRaises(ValidationError):
            file_ot(self.org.hr, "HR", "x", "", [ot_line(self.cosp, date(2026, 11, 16))], self.org.hr.user,
                    date(2026, 11, 1))

    def test_clearing_restores_normal_rules(self):
        ot = approved_ot(self.org, self.cosp, date(2026, 11, 20), today=date(2026, 11, 20))
        claim = filed_claim(self.org, self.cosp, [(ot, "8", None, None)], date(2026, 11, 20))
        with on(date(2026, 11, 25)):
            approve_all(claim, on_approved=credit_lines)
        self._set(Employee.SEPARATION_NOT_RENEWING, date(2026, 12, 31))
        self.assertEqual(compute_available_cto_balance(self.cosp, date(2026, 11, 26)), Decimal("1.00"))
        self._set("", None)
        self.cosp.refresh_from_db()
        self.assertEqual(compute_available_cto_balance(self.cosp, date(2026, 11, 26)), Decimal("0"))  # waits for Dec 1
        apply_cto(self.cosp, "2026-12-21", "2026-12-21", "1", date(2026, 12, 1))  # Dec 15 deadline back
        self.assertFalse(CTOUsageApplication.objects.exists())

    def test_separating_employees_claims_are_flagged_in_queue(self):
        ot = approved_ot(self.org, self.cosp, date(2026, 10, 5), today=date(2026, 10, 1))
        filed_claim(self.org, self.cosp, [(ot, "8", None, None)], date(2026, 10, 6))
        self._set(Employee.SEPARATION_NOT_RENEWING, date(2026, 12, 31))
        client = Client()
        client.force_login(self.org.lab_sup.user)
        self.assertContains(client.get(reverse("cto:cto_queue")), "Separating")


class Stage2PageTests(TestCase):
    def setUp(self):
        self.org = Org()
        opening_credit(self.org, self.org.lab_staff)
        self.client = Client()

    def test_carry_over_page_submit_and_decide(self):
        self.client.force_login(self.org.lab_sup.user)
        with on(date(2026, 12, 5)):
            self.assertContains(self.client.get(reverse("cto:carry_over")), self.org.lab_staff.full_name)
            self.client.post(reverse("cto:carry_over"), {"action": "submit", "employee": self.org.lab_staff.pk,
                                                         "reason": "Short-staffed"})
        from .models import CTOCarryOverReason

        record = CTOCarryOverReason.objects.get()
        self.client.force_login(self.org.coh.user)
        with on(date(2026, 12, 6)):
            self.assertContains(self.client.get(reverse("cto:carry_over")), "awaiting your decision")
            self.client.post(reverse("cto:carry_over"), {"action": "approve", "reason_id": record.pk})
        record.refresh_from_db()
        self.assertEqual(record.status, CTOCarryOverReason.APPROVED)

    def test_employee_sees_no_one_elses_rows(self):
        self.client.force_login(self.org.lab_staff2.user)
        with on(date(2026, 12, 5)):
            self.assertNotContains(self.client.get(reverse("cto:carry_over")), self.org.lab_staff.full_name)

    def test_separation_page_hr_only(self):
        self.client.force_login(self.org.hr.user)
        self.assertEqual(self.client.get(reverse("cto:separation")).status_code, 200)
        self.client.force_login(self.org.lab_sup.user)
        self.assertEqual(self.client.get(reverse("cto:separation")).status_code, 403)

    def test_commands(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with on(date(2026, 12, 17)):
            call_command("cto_year_end", stdout=out)
            call_command("cto_separation_lapse", stdout=out)
        self.assertIn("1 forfeited", out.getvalue())


class UseDeadlineBackfillTests(TestCase):
    def setUp(self):
        self.org = Org()

    def test_backfill_fills_missing_deadlines(self):
        from .carryover import fill_missing_use_deadlines

        normal = opening_credit(self.org, self.org.lab_staff)
        ot = approved_ot(self.org, self.org.lab_staff2, date(2026, 10, 5), today=date(2026, 10, 1))
        claim = filed_claim(self.org, self.org.lab_staff2, [(ot, "8", None, None)], date(2026, 11, 28))
        with on(date(2026, 12, 20)):
            approve_all(claim, on_approved=credit_lines)
        late = claim.lines.get()
        CTOClaimLine.objects.update(use_deadline=None)  # as on a dry-run-1 database
        self.assertEqual(fill_missing_use_deadlines(CTOClaimLine), 2)
        normal.refresh_from_db()
        late.refresh_from_db()
        self.assertEqual(normal.use_deadline, date(2026, 12, 15))
        self.assertEqual(late.use_deadline, date(2027, 12, 15))  # took effect after Dec 15
        self.assertEqual(fill_missing_use_deadlines(CTOClaimLine), 0)

    def test_no_credited_line_is_left_without_a_use_deadline(self):
        from .opening import void_opening_line

        opening_credit(self.org, self.org.lab_staff)
        void_opening_line(self.org.hr_admin, opening_credit(self.org, self.org.lab_staff, days="1"), "dup",
                          self.org.hr_admin.user, date(2026, 11, 10))
        for emp, work in ((self.org.lab_staff2, date(2026, 10, 5)), (self.org.nurse, date(2026, 12, 2))):
            ot = approved_ot(self.org, emp, work, today=work)
            claim = filed_claim(self.org, emp, [(ot, "8", None, None)], work)
            with on(date(2026, 12, 20)):
                approve_all(claim, on_approved=credit_lines)
        self.assertTrue(CTOClaimLine.objects.filter(state=CTOClaimLine.CREDITED).exists())
        self.assertFalse(CTOClaimLine.objects.filter(state=CTOClaimLine.CREDITED, use_deadline__isnull=True).exists())
