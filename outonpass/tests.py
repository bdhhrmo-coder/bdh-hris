"""Out-on-Pass stage 1: allowance, balance, minutes and the filing checks (CLAUDE.md 13B)."""

from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from accounts.models import RoleAssignment
from employees.models import Employee
from orgstructure.models import Section, Unit
from schedules.models import DutySchedule, ScheduleCell, ScheduleRow, ShiftCode

from . import rules
from .models import CospContractPeriod, OutOnPass as P, PassOpeningUsage

# A fixed "now": Monday Feb 1, 2027, 7:00 AM. Passes below are later that day or later in Feb.
NOW = timezone.make_aware(datetime(2027, 2, 1, 7, 0))
FEB = date(2027, 2, 3)


def at(d, h, m=0, s=0):
    return timezone.make_aware(datetime.combine(d, time(h, m, s)))


def emp(username, section=None, unit=None, status="REGULAR", roles=()):
    user = User.objects.create_user(username=username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(),
                                first_name="T", employment_status=status)
    if section:
        e.sections.add(section)
    if unit:
        e.units.add(unit)
    for role in roles:
        RoleAssignment.objects.create(employee=e, role=role)
    return e


class Base(TestCase):
    def setUp(self):
        self.lab = Section.objects.get(name="Laboratory Section")
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.er, _ = Unit.objects.get_or_create(section=self.nursing, name="ER")
        self.hr_user = User.objects.create_user("hr", password="x")
        self.a = emp("staffa", self.lab)

    def make(self, e, d, out, back, status=P.APPROVED, kind=P.ORDINARY, charged=None):
        return P.objects.create(employee=e, kind=kind, pass_date=d, time_out=out, time_in=back, reason_type=P.REASON_GOVT_BANK, reason="Bank",
                                status=status, minutes_charged=charged, filed_by=e.user)

    def check(self, e, d, out, back, kind=P.ORDINARY, **kw):
        return {v.code for v in rules.check_pass(e, kind, d, out, back, now=NOW, **kw)}


class MinutesTests(TestCase):
    def test_actual_minutes_no_rounding_seconds_ignored(self):
        d = date(2027, 2, 3)
        self.assertEqual(rules.charged_minutes(at(d, 10, 5, 59), at(d, 10, 47, 10)), (42, 0))
        # 5 minutes used is 5 minutes, not 15.
        self.assertEqual(rules.charged_minutes(at(d, 10, 0), at(d, 10, 5)), (5, 0))

    def test_grace_period_not_excess_but_charged(self):
        d = date(2027, 2, 3)
        self.assertEqual(rules.charged_minutes(at(d, 9, 0), at(d, 11, 5)), (125, 0))
        self.assertEqual(rules.charged_minutes(at(d, 9, 0), at(d, 11, 6)), (126, 6))

    def test_planned_minutes_past_midnight(self):
        self.assertEqual(rules.planned_minutes(time(23, 30), time(0, 45)), 75)


class AllowanceTests(Base):
    def test_regular_twelve_hours(self):
        self.assertEqual(rules.allowance_minutes(self.a, 2027), 720)

    def test_cosp_full_months_capped_and_renewal_adds(self):
        c = emp("cosp", self.lab, status="COSP")
        self.assertEqual(rules.allowance_minutes(c, 2027), 0)  # no contract entered yet
        CospContractPeriod.objects.create(employee=c, start_date=date(2026, 7, 1), end_date=date(2027, 6, 30),
                                          entered_by=self.hr_user)
        self.assertEqual(rules.allowance_minutes(c, 2027), 6 * 60)  # counted from Jan 1, 2027 only
        CospContractPeriod.objects.create(employee=c, start_date=date(2027, 7, 1), end_date=date(2027, 12, 31),
                                          entered_by=self.hr_user)
        self.assertEqual(rules.allowance_minutes(c, 2027), 12 * 60)

    def test_cosp_partial_month_not_counted(self):
        c = emp("cosp", self.lab, status="COSP")
        CospContractPeriod.objects.create(employee=c, start_date=date(2027, 1, 15), end_date=date(2027, 3, 31),
                                          entered_by=self.hr_user)
        self.assertEqual(rules.allowance_minutes(c, 2027), 120)  # Jan 15-Mar 14 is 2 full months

    def test_balance_counts_opening_completed_and_held(self):
        PassOpeningUsage.objects.create(employee=self.a, year=2027, minutes_used=90, passes_used_in_month=1,
                                        reason="Google Sheets tracker", entered_by=self.hr_user)
        self.make(self.a, date(2027, 1, 20), time(9), time(10), status=P.COMPLETED, charged=62)
        self.make(self.a, FEB, time(13), time(14, 30), status=P.SUBMITTED)  # held: 90
        self.make(self.a, date(2027, 2, 5), time(13), time(14), status=P.REJECTED)  # not counted
        self.assertEqual(rules.balance_minutes(self.a, 2027), 720 - 90 - 62 - 90)
        self.assertEqual(rules.passes_in_month(self.a, 2027, 1), 2)

    def test_years_are_separate(self):
        self.make(self.a, date(2026, 12, 10), time(9), time(11), status=P.COMPLETED, charged=120)
        self.assertEqual(rules.balance_minutes(self.a, 2027), 720)


class CheckTests(Base):
    def test_clean_pass(self):
        self.assertEqual(self.check(self.a, FEB, time(10), time(11, 30)), set())

    def test_after_the_fact_refused_except_emergency(self):
        self.assertIn("after_the_fact", self.check(self.a, date(2027, 1, 29), time(10), time(11)))
        self.assertEqual(self.check(self.a, date(2027, 1, 29), time(10), time(11), kind=P.EMERGENCY), set())
        self.assertIn("emergency_future", self.check(self.a, FEB, time(10), time(11), kind=P.EMERGENCY))

    def test_over_two_hours_and_balance(self):
        self.assertIn("over_2_hours", self.check(self.a, FEB, time(9), time(11, 1)))
        PassOpeningUsage.objects.create(employee=self.a, year=2027, minutes_used=700,
                                        reason="tracker", entered_by=self.hr_user)
        self.assertIn("balance", self.check(self.a, FEB, time(9), time(10)))
        # An exception lifts both limits.
        self.assertEqual(self.check(self.a, FEB, time(9), time(11, 30), kind=P.EXCEPTION), set())

    def test_exception_not_needed(self):
        self.assertIn("exception_not_needed", self.check(self.a, FEB, time(9), time(10), kind=P.EXCEPTION))

    def test_one_a_day(self):
        self.make(self.a, FEB, time(8), time(8, 30), status=P.COMPLETED, charged=30)
        self.assertIn("one_a_day", self.check(self.a, FEB, time(14), time(15)))

    def test_monthly_limits_and_third_pass(self):
        self.make(self.a, date(2027, 2, 2), time(9), time(10))
        self.make(self.a, date(2027, 2, 4), time(9), time(10))
        d = date(2027, 2, 10)
        self.assertIn("third_pass", self.check(self.a, d, time(9), time(10)))
        self.assertIn("third_proof", self.check(self.a, d, time(9), time(10), kind=P.THIRD))
        self.assertEqual(self.check(self.a, d, time(9), time(10), kind=P.THIRD, has_proof=True), set())
        self.make(self.a, date(2027, 2, 8), time(9), time(10), kind=P.THIRD)
        self.assertIn("max_month", self.check(self.a, d, time(9), time(10), kind=P.THIRD, has_proof=True))

    def test_third_pass_not_needed(self):
        self.assertIn("third_not_needed",
                      self.check(self.a, FEB, time(9), time(10), kind=P.THIRD, has_proof=True))

    def test_returned_and_rejected_do_not_count(self):
        self.make(self.a, date(2027, 2, 2), time(9), time(10), status=P.RETURNED)
        self.make(self.a, date(2027, 2, 4), time(9), time(10), status=P.REJECTED)
        self.assertEqual(self.check(self.a, date(2027, 2, 10), time(9), time(10)), set())

    def test_resubmission_excludes_itself(self):
        p = self.make(self.a, FEB, time(10), time(11), status=P.SUBMITTED)
        self.assertEqual(self.check(self.a, FEB, time(10), time(11), exclude_pk=p.pk), set())

    def test_unit_limit(self):
        b, c, d = (emp(n, self.nursing, self.er) for n in ("nb", "nc", "nd"))
        self.make(b, FEB, time(9), time(10, 30))
        self.make(c, FEB, time(10), time(11))
        self.assertIn("unit_limit", self.check(d, FEB, time(10, 15), time(10, 45)))
        self.assertNotIn("unit_limit", self.check(d, FEB, time(11), time(12)))  # c is back at 11:00
        # Another unit is not affected.
        opd, _ = Unit.objects.get_or_create(section=self.nursing, name="OPD")
        e = emp("ne", self.nursing, opd)
        self.assertNotIn("unit_limit", self.check(e, FEB, time(10, 15), time(10, 45)))

    def test_shift_first_and_last_hour_from_approved_schedule(self):
        shift = ShiftCode.objects.create(code="T8-5", start_time=time(8), end_time=time(17), is_duty=True)
        sched = DutySchedule.objects.create(section=self.lab, year=2027, month=2, status=DutySchedule.APPROVED,
                                            prepared_by=self.hr_user, preparer_role="HR", cutoff_date=date(2027, 1, 20))
        row = ScheduleRow.objects.create(schedule=sched, employee=self.a)
        ScheduleCell.objects.create(row=row, date=FEB, shift=shift)
        self.assertIn("first_hour", self.check(self.a, FEB, time(8, 30), time(9, 30)))
        self.assertIn("last_hour", self.check(self.a, FEB, time(15, 30), time(16, 30)))
        self.assertEqual(self.check(self.a, FEB, time(9), time(10)), set())
        self.assertFalse(rules.needs_shift_confirmation(self.a, FEB))
        self.assertTrue(rules.needs_shift_confirmation(self.a, date(2027, 3, 3)))

    def test_night_shift_from_previous_day(self):
        shift = ShiftCode.objects.create(code="T7P-7A", start_time=time(19), end_time=time(7), is_duty=True)
        sched = DutySchedule.objects.create(section=self.lab, year=2027, month=2, status=DutySchedule.RECORDED,
                                            prepared_by=self.hr_user, preparer_role="HR", cutoff_date=date(2027, 1, 20))
        row = ScheduleRow.objects.create(schedule=sched, employee=self.a)
        ScheduleCell.objects.create(row=row, date=date(2027, 2, 2), shift=shift)
        # Shift ends 7:00 AM on Feb 3: 6:15-6:45 is in its last hour.
        self.assertIn("last_hour", self.check(self.a, FEB, time(6, 15), time(6, 45)))

    def test_coh_pass_is_checked_like_anyone_elses(self):
        coh = emp("coh", roles=[RoleAssignment.CHIEF_OF_HOSPITAL])
        self.assertEqual(self.check(coh, FEB, time(10), time(11)), set())
        self.assertIn("over_2_hours", self.check(coh, FEB, time(9), time(12)))
