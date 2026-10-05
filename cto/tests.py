from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import RoleAssignment
from employees.models import Employee

from documents.models import DocumentRequirement, UploadedDocument
from official_requests.models import OfficialRequest

from .balances import (
    compute_available_cto_balance,
    compute_credit,
    cto_used_in_month,
    exceeds_monthly_cap,
    filed_too_early,
    filing_window_opens,
    forms_illegal_consecutive_run,
    violates_filing_deadline,
    violates_usage_cutoff,
)
from .claims import claim_submission_errors, claimable_ot_requests, credit_claim
from .models import CTOCreditEntry, CTOCreditTransaction, CTOMultiplierRate, CTOUsageApplication
from .permissions import is_cto_eligible


def make_employee(username, employee_id, shift_hours=Employee.SHIFT_8_HOUR, role=None):
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
    return employee


def make_rate(effective_date=date(2025, 12, 16), weekday="1.00", restday="1.50"):
    # The 0002_seed_cto_multiplier_rate migration already seeds a real row
    # at 2025-12-16, so tests that want full control over exactly which
    # rates exist replace it via update_or_create rather than colliding
    # with it on the unique effective_date constraint.
    rate, _ = CTOMultiplierRate.objects.update_or_create(
        effective_date=effective_date,
        defaults=dict(weekday_multiplier=weekday, restday_holiday_multiplier=restday),
    )
    rate.refresh_from_db()  # DecimalField values stay str in memory until reloaded
    return rate


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

    def test_credit_entry_clean_rejects_coh(self):
        entry = CTOCreditEntry(
            employee=self.coh, work_date=date(2026, 1, 5), hours_worked=Decimal("8"),
            shift_hours_used=8, multiplier_applied=Decimal("1.00"),
            credited_hours=Decimal("8.00"), credited_days=Decimal("1.00"), recorded_by=self.hr_user,
        )
        with self.assertRaises(Exception):
            entry.full_clean()

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

    def test_usage_ending_on_cutoff_is_allowed(self):
        self.assertFalse(violates_usage_cutoff(date(2026, 12, 15)))

    def test_usage_ending_after_cutoff_is_blocked(self):
        self.assertTrue(violates_usage_cutoff(date(2026, 12, 16)))


class LedgerBalanceTests(TestCase):
    def setUp(self):
        self.employee = make_employee("ledger1", "EMP-LED1")
        self.hr_user = User.objects.create_user(username="hr_ledger_cto", password="x")

    def test_balance_reflects_earned_and_used(self):
        CTOCreditTransaction.objects.create(
            employee=self.employee, transaction_type=CTOCreditTransaction.EARNED,
            days=Decimal("2.00"), transaction_date=date(2026, 1, 5), created_by=self.hr_user,
        )
        CTOCreditTransaction.objects.create(
            employee=self.employee, transaction_type=CTOCreditTransaction.USED,
            days=Decimal("-0.50"), transaction_date=date(2026, 1, 10), created_by=self.hr_user,
        )
        self.assertEqual(compute_available_cto_balance(self.employee, as_of_date=date(2026, 1, 31)), Decimal("1.50"))

    def test_balance_as_of_date_ignores_later_transactions(self):
        CTOCreditTransaction.objects.create(
            employee=self.employee, transaction_type=CTOCreditTransaction.EARNED,
            days=Decimal("2.00"), transaction_date=date(2026, 1, 5), created_by=self.hr_user,
        )
        CTOCreditTransaction.objects.create(
            employee=self.employee, transaction_type=CTOCreditTransaction.EARNED,
            days=Decimal("1.00"), transaction_date=date(2026, 3, 1), created_by=self.hr_user,
        )
        self.assertEqual(compute_available_cto_balance(self.employee, as_of_date=date(2026, 1, 31)), Decimal("2.00"))


class ForfeitureCommandTests(TestCase):
    def setUp(self):
        self.employee = make_employee("forfeit1", "EMP-FOR1")
        self.exempt_employee = make_employee("forfeit2", "EMP-FOR2")
        self.coh = make_employee("forfeit_coh", "EMP-FORCOH", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        self.actor = User.objects.create_user(username="hr_forfeit", password="x")
        for emp in (self.employee, self.exempt_employee):
            CTOCreditTransaction.objects.create(
                employee=emp, transaction_type=CTOCreditTransaction.EARNED,
                days=Decimal("3.00"), transaction_date=date(2026, 2, 1), created_by=self.actor,
            )

    def test_forfeits_remaining_balance_at_year_end(self):
        call_command("forfeit_expired_cto", "--year", "2026", "--actor-username", "hr_forfeit", stdout=StringIO())
        self.assertEqual(
            compute_available_cto_balance(self.employee, as_of_date=date(2026, 12, 31)), Decimal("0.00")
        )

    def test_exempted_employee_keeps_balance(self):
        call_command(
            "forfeit_expired_cto", "--year", "2026", "--actor-username", "hr_forfeit",
            "--exempt", "EMP-FOR2", "--exempt-reason", "COH exception memo 2026-12-01", stdout=StringIO(),
        )
        self.assertEqual(
            compute_available_cto_balance(self.exempt_employee, as_of_date=date(2026, 12, 31)), Decimal("3.00")
        )
        self.assertEqual(
            compute_available_cto_balance(self.employee, as_of_date=date(2026, 12, 31)), Decimal("0.00")
        )

    def test_chief_of_hospital_is_skipped_entirely(self):
        CTOCreditTransaction.objects.create(
            employee=self.coh, transaction_type=CTOCreditTransaction.ADJUSTMENT,
            days=Decimal("1.00"), transaction_date=date(2026, 2, 1), created_by=self.actor,
        )
        call_command("forfeit_expired_cto", "--year", "2026", "--actor-username", "hr_forfeit", stdout=StringIO())
        self.assertEqual(compute_available_cto_balance(self.coh, as_of_date=date(2026, 12, 31)), Decimal("1.00"))

    def test_dry_run_writes_nothing(self):
        call_command(
            "forfeit_expired_cto", "--year", "2026", "--actor-username", "hr_forfeit", "--dry-run", stdout=StringIO()
        )
        self.assertEqual(
            compute_available_cto_balance(self.employee, as_of_date=date(2026, 12, 31)), Decimal("3.00")
        )

    def test_running_twice_is_idempotent(self):
        call_command("forfeit_expired_cto", "--year", "2026", "--actor-username", "hr_forfeit", stdout=StringIO())
        call_command("forfeit_expired_cto", "--year", "2026", "--actor-username", "hr_forfeit", stdout=StringIO())
        self.assertEqual(
            compute_available_cto_balance(self.employee, as_of_date=date(2026, 12, 31)), Decimal("0.00")
        )


class CTORoutingPermissionTests(TestCase):
    """CTO usage routing reuses leave.permissions' role checks — CLAUDE.md
    §6.2: Employee -> Supervisor -> HR -> AO -> COH."""

    def setUp(self):
        self.employee = make_employee("route1", "EMP-ROUTE1")
        self.supervisor = make_employee("routesup1", "EMP-ROUTESUP1", role=RoleAssignment.SUPERVISOR)
        self.hr = make_employee("routehr1", "EMP-ROUTEHR1", role=RoleAssignment.HR_PROCESSOR)
        self.ao = make_employee("routeao1", "EMP-ROUTEAO1", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("routecoh1", "EMP-ROUTECOH1", role=RoleAssignment.CHIEF_OF_HOSPITAL)
        # Hospital-wide Supervisor assignment (no section/unit) makes
        # is_supervisor_of() true for any target employee — see
        # leave.permissions.is_supervisor_of.

        self.application = CTOUsageApplication.objects.create(
            employee=self.employee, start_date=date(2026, 5, 1), end_date=date(2026, 5, 1),
            number_of_days=Decimal("1.00"), status=CTOUsageApplication.SUBMITTED,
        )
        self.client = Client()

    def test_supervisor_can_endorse_submitted_application(self):
        self.client.force_login(self.supervisor.user)
        response = self.client.post(
            reverse("cto:cto_action", args=[self.application.pk]), {"action": "endorse"}
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, CTOUsageApplication.ENDORSED_BY_SUPERVISOR)

    def test_hr_cannot_act_before_supervisor_endorsement(self):
        self.client.force_login(self.hr.user)
        response = self.client.post(
            reverse("cto:cto_action", args=[self.application.pk]), {"action": "process"}
        )
        self.assertEqual(response.status_code, 403)

    def test_full_chain_to_approval_creates_used_transaction(self):
        CTOCreditTransaction.objects.create(
            employee=self.employee, transaction_type=CTOCreditTransaction.EARNED,
            days=Decimal("5.00"), transaction_date=date(2026, 1, 1),
            created_by=User.objects.create_user(username="seed_hr_route", password="x"),
        )
        self.client.force_login(self.supervisor.user)
        self.client.post(reverse("cto:cto_action", args=[self.application.pk]), {"action": "endorse"})
        self.client.force_login(self.hr.user)
        self.client.post(reverse("cto:cto_action", args=[self.application.pk]), {"action": "process"})
        self.client.force_login(self.ao.user)
        self.client.post(reverse("cto:cto_action", args=[self.application.pk]), {"action": "recommend"})
        self.client.force_login(self.coh.user)
        self.client.post(reverse("cto:cto_action", args=[self.application.pk]), {"action": "approve"})

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, CTOUsageApplication.APPROVED)
        self.assertEqual(compute_available_cto_balance(self.employee), Decimal("4.00"))

    def test_approval_blocked_when_balance_insufficient(self):
        self.client.force_login(self.supervisor.user)
        self.client.post(reverse("cto:cto_action", args=[self.application.pk]), {"action": "endorse"})
        self.client.force_login(self.hr.user)
        self.client.post(reverse("cto:cto_action", args=[self.application.pk]), {"action": "process"})
        self.client.force_login(self.ao.user)
        self.client.post(reverse("cto:cto_action", args=[self.application.pk]), {"action": "recommend"})
        self.client.force_login(self.coh.user)
        self.client.post(reverse("cto:cto_action", args=[self.application.pk]), {"action": "approve"})

        self.application.refresh_from_db()
        self.assertEqual(self.application.status, CTOUsageApplication.RECOMMENDED_BY_AO)


class FilingWindowTests(TestCase):
    """Confirmed 2026-09-28: a claim is filed no earlier than the month
    after the workday, and no later than that year's filing deadline; both
    year-end dates come from settings, not code."""

    def test_window_opens_on_first_of_next_month(self):
        self.assertEqual(filing_window_opens(date(2026, 3, 15)), date(2026, 4, 1))
        self.assertEqual(filing_window_opens(date(2026, 3, 31)), date(2026, 4, 1))

    def test_december_work_rolls_into_january(self):
        self.assertEqual(filing_window_opens(date(2026, 12, 5)), date(2027, 1, 1))

    def test_same_month_filing_is_too_early(self):
        self.assertTrue(filed_too_early(date(2026, 3, 5), date(2026, 3, 31)))
        self.assertFalse(filed_too_early(date(2026, 3, 5), date(2026, 4, 1)))

    def test_november_work_cannot_be_filed_in_time(self):
        work = date(2026, 11, 10)
        filed = filing_window_opens(work)
        self.assertTrue(violates_filing_deadline(work, filed))

    @override_settings(CTO_FILING_DEADLINE_MONTH_DAY=(10, 31))
    def test_filing_deadline_comes_from_settings(self):
        self.assertTrue(violates_filing_deadline(date(2026, 6, 1), date(2026, 11, 1)))

    @override_settings(CTO_USAGE_CUTOFF_MONTH_DAY=(12, 10))
    def test_usage_cutoff_comes_from_settings(self):
        self.assertTrue(violates_usage_cutoff(date(2026, 12, 11)))


def make_approved_ot(employee, start, end, hours, restday=False):
    return OfficialRequest.objects.create(
        request_type=OfficialRequest.OT_RESTDAY_HOLIDAY, employee=employee, start_date=start, end_date=end,
        purpose="Test OT", hours_requested=Decimal(hours), is_restday_or_holiday=restday,
        status=OfficialRequest.APPROVED,
    )


def make_draft_claim(ot, work_date, hours, actor_user, restday=False):
    return CTOCreditEntry.objects.create(
        employee=ot.employee, ot_request=ot, work_date=work_date, hours_worked=Decimal(hours),
        is_restday_or_holiday=restday, shift_hours_used=8, multiplier_applied="1.00",
        credited_hours=Decimal(hours), credited_days="1.00", recorded_by=actor_user,
    )


def upload_all_required(entry, actor_user):
    """Attach one active document per configured requirement (the §7 set
    is seeded by documents/migrations/0002)."""
    content_type = ContentType.objects.get_for_model(entry)
    for req in DocumentRequirement.objects.filter(content_type=content_type, sub_type_value__in=["", entry.duty_type]):
        UploadedDocument.objects.create(
            content_type=content_type, object_id=entry.pk, requirement=req, file="secure_documents/x.pdf",
            original_filename="x.pdf", file_size=1, file_type=UploadedDocument.PDF, uploaded_by=actor_user,
        )


class CTOClaimSubmissionTests(TestCase):
    """Settled 2026-09-28: approved OT never credits CTO by itself; a claim
    credits only on submission, once every §7 check passes."""

    def setUp(self):
        make_rate(weekday="1.00", restday="1.50")
        self.hr = make_employee("claim_hr", "EMP-CL-1", role=RoleAssignment.HR_PROCESSOR)
        self.employee = make_employee("claim_emp", "EMP-CL-2")
        self.ot = make_approved_ot(self.employee, date(2026, 3, 6), date(2026, 3, 7), "16")
        self.ok_day = date(2026, 4, 10)

    def test_approved_ot_alone_credits_nothing(self):
        self.assertEqual(compute_available_cto_balance(self.employee), Decimal("0"))
        self.assertFalse(CTOCreditEntry.objects.exists())

    def test_missing_documents_block_submission(self):
        entry = make_draft_claim(self.ot, date(2026, 3, 6), "8", self.hr.user)
        errors = credit_claim(entry, self.hr.user, self.ok_day)
        self.assertTrue(any("Required documents" in e for e in errors))
        self.assertEqual(compute_available_cto_balance(self.employee), Decimal("0"))

    def test_same_month_submission_is_blocked(self):
        entry = make_draft_claim(self.ot, date(2026, 3, 6), "8", self.hr.user)
        upload_all_required(entry, self.hr.user)
        errors = claim_submission_errors(entry, date(2026, 3, 31))
        self.assertTrue(any("month after the workday" in e for e in errors))

    def test_submission_after_deadline_is_blocked(self):
        entry = make_draft_claim(self.ot, date(2026, 3, 6), "8", self.hr.user)
        upload_all_required(entry, self.hr.user)
        errors = claim_submission_errors(entry, date(2026, 12, 1))
        self.assertTrue(any("filing deadline" in e for e in errors))

    def test_complete_claim_credits_with_configured_multiplier(self):
        entry = make_draft_claim(self.ot, date(2026, 3, 7), "8", self.hr.user, restday=True)
        upload_all_required(entry, self.hr.user)
        self.assertEqual(credit_claim(entry, self.hr.user, self.ok_day), [])
        entry.refresh_from_db()
        self.assertEqual(entry.status, CTOCreditEntry.CREDITED)
        self.assertEqual(entry.multiplier_applied, Decimal("1.50"))
        self.assertEqual(entry.credited_days, Decimal("1.50"))  # 8h x 1.5 / 8h shift
        self.assertEqual(entry.credited_by, self.hr.user)
        ledger = CTOCreditTransaction.objects.get(credit_entry=entry)
        self.assertEqual(ledger.days, Decimal("1.50"))

    def test_multiplier_is_read_from_rate_table(self):
        make_rate(weekday="1.25", restday="2.00")  # policy change, same effective date
        entry = make_draft_claim(self.ot, date(2026, 3, 6), "8", self.hr.user)
        upload_all_required(entry, self.hr.user)
        credit_claim(entry, self.hr.user, self.ok_day)
        entry.refresh_from_db()
        self.assertEqual(entry.multiplier_applied, Decimal("1.25"))

    def test_claim_cannot_be_credited_twice(self):
        entry = make_draft_claim(self.ot, date(2026, 3, 6), "8", self.hr.user)
        upload_all_required(entry, self.hr.user)
        credit_claim(entry, self.hr.user, self.ok_day)
        self.assertEqual(credit_claim(entry, self.hr.user, self.ok_day), ["This claim has already been credited."])
        self.assertEqual(CTOCreditTransaction.objects.filter(credit_entry=entry).count(), 1)

    def test_hours_over_approved_total_are_blocked(self):
        make_draft_claim(self.ot, date(2026, 3, 6), "10", self.hr.user)
        entry = make_draft_claim(self.ot, date(2026, 3, 7), "8", self.hr.user)
        upload_all_required(entry, self.hr.user)
        errors = claim_submission_errors(entry, self.ok_day)
        self.assertTrue(any("approved hours" in e for e in errors))

    def test_fully_claimed_ot_is_no_longer_listed(self):
        self.assertIn(self.ot, claimable_ot_requests())
        make_draft_claim(self.ot, date(2026, 3, 6), "8", self.hr.user)
        make_draft_claim(self.ot, date(2026, 3, 7), "8", self.hr.user)
        self.assertNotIn(self.ot, claimable_ot_requests())


class _FakeDate(date):
    fixed_today = date(2026, 4, 10)

    @classmethod
    def today(cls):
        return cls.fixed_today


class CTOClaimViewTests(TestCase):
    def setUp(self):
        from unittest.mock import patch

        patcher = patch("cto.views.date", _FakeDate)
        patcher.start()
        self.addCleanup(patcher.stop)
        make_rate()
        self.hr = make_employee("claimv_hr", "EMP-CV-1", role=RoleAssignment.HR_PROCESSOR)
        self.hr_admin = make_employee("claimv_hra", "EMP-CV-2", role=RoleAssignment.HR_ADMINISTRATOR)
        self.employee = make_employee("claimv_emp", "EMP-CV-3")
        self.ot = make_approved_ot(self.employee, date(2026, 3, 6), date(2026, 3, 7), "16")
        self.client.force_login(self.hr.user)

    def _file(self, work_date="2026-03-06", hours="8", restday=False):
        return self.client.post(reverse("cto:claim_create", args=[self.ot.pk]), {
            "work_date": work_date, "duty_type": CTOCreditEntry.DUTY_REGULAR, "hours_worked": hours,
            "is_restday_or_holiday": restday, "notes": "",
        })

    def test_filing_creates_linked_draft_without_credit(self):
        self._file()
        entry = CTOCreditEntry.objects.get()
        self.assertEqual(entry.ot_request, self.ot)
        self.assertEqual(entry.status, CTOCreditEntry.DRAFT)
        self.assertFalse(CTOCreditTransaction.objects.exists())

    def test_work_date_outside_ot_is_rejected(self):
        self._file(work_date="2026-03-08")
        self.assertFalse(CTOCreditEntry.objects.exists())

    def test_one_claim_per_workday(self):
        self._file(hours="4")
        self._file(hours="4")
        self.assertEqual(CTOCreditEntry.objects.count(), 1)

    def test_hours_beyond_approved_total_rejected_at_filing(self):
        self._file(work_date="2026-03-06", hours="10")
        self._file(work_date="2026-03-07", hours="8")
        self.assertEqual(CTOCreditEntry.objects.count(), 1)

    def test_cannot_file_from_unapproved_ot(self):
        self.ot.status = OfficialRequest.RECOMMENDED_BY_AO
        self.ot.save()
        self.assertEqual(self._file().status_code, 404)

    def test_employee_cannot_file_or_submit(self):
        self.client.force_login(self.employee.user)
        self.assertEqual(self._file().status_code, 403)

    def test_submit_credits_only_with_documents(self):
        self._file()
        entry = CTOCreditEntry.objects.get()
        self.client.post(reverse("cto:claim_submit", args=[entry.pk]))
        self.assertEqual(compute_available_cto_balance(self.employee), Decimal("0"))
        upload_all_required(entry, self.hr.user)
        self.client.post(reverse("cto:claim_submit", args=[entry.pk]))
        entry.refresh_from_db()
        self.assertEqual(entry.status, CTOCreditEntry.CREDITED)
        self.assertEqual(compute_available_cto_balance(self.employee), Decimal("1.00"))

    def test_exception_claim_is_hr_admin_only_and_needs_reason(self):
        data = {
            "employee": self.employee.pk, "work_date": "2026-03-10", "duty_type": CTOCreditEntry.DUTY_REGULAR,
            "hours_worked": "8", "is_restday_or_holiday": False, "exception_reason": "", "notes": "",
        }
        self.assertEqual(self.client.post(reverse("cto:credit_entry_create"), data).status_code, 403)
        self.client.force_login(self.hr_admin.user)
        self.client.post(reverse("cto:credit_entry_create"), data)
        self.assertFalse(CTOCreditEntry.objects.exists())
        data["exception_reason"] = "Certified emergency duty"
        self.client.post(reverse("cto:credit_entry_create"), data)
        entry = CTOCreditEntry.objects.get()
        self.assertIsNone(entry.ot_request)
        self.assertEqual(entry.status, CTOCreditEntry.DRAFT)
        self.assertFalse(CTOCreditTransaction.objects.exists())
