"""Exchange of Duty rules decided by the owner on 2026-11-26: same section
(any unit), Exchange groups by position record, equal length, rest /
straight duty / PM-to-AM checks, both unit heads on a cross-unit exchange,
and the exchange shown in both units' drafts. All data made up."""

from datetime import date, time
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment as R
from auditlog.aggregation import audit_rows
from employees.models import Employee, ExchangeGroup, Position
from employees.positions import title_key
from notifications.models import Notification
from orgstructure.models import Section, Unit
from schedules import rules as srules, services
from schedules.models import DutySchedule as S, ScheduleCell, ShiftCode

from .duty_checks import check_swap, check_taker
from .models import DutyExchangeRequest as X, ExchangeRules
from .schedule_checks import location_errors

YEAR, MONTH = 2027, 3  # March 2027; Mar 1 is a Monday


def d(day):
    return date(YEAR, MONTH, day)


def shift(code, start, end, hours):
    return ShiftCode.objects.get_or_create(code=code, defaults=dict(
        start_time=time(start), end_time=time(end), paid_hours=Decimal(hours), is_duty=True))[0]


def code(c):
    return ShiftCode.objects.get(code=c)


class Org(TestCase):
    def person(self, name, position, section=None, unit=None, role=None, role_section=None, role_unit=None,
               shift_hours=12):
        user = User.objects.create_user(name, password="x")
        e = Employee.objects.create(user=user, employee_id=name.upper(), surname=name.title(), first_name="T",
                                    position=position, date_hired=date(2024, 1, 1), employment_status="REGULAR",
                                    shift_hours=shift_hours)
        if section:
            e.sections.add(section)
        if unit:
            e.units.add(unit)
        if role:
            R.objects.create(employee=e, role=role, section=role_section, unit=role_unit)
        return e

    def setUp(self):
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.lab = Section.objects.get(name="Laboratory Section")
        self.er = Unit.objects.get(section=self.nursing, name="ER")
        self.peds = Unit.objects.get(section=self.nursing, name="Pediatric")
        n, er, peds = self.nursing, self.er, self.peds
        self.er_nurse = self.person("ernurse", "Nurse I", n, er)
        self.peds_nurse = self.person("pedsnurse", "CLINICAL NURSE", n, peds)
        self.nurse3 = self.person("nurse3", "Nurse III", n, er)
        self.midwife = self.person("midwife", "Midwife II", n, er)
        self.attendant = self.person("attendant", "Nursing Attendant I", n, er)
        self.clerk = self.person("clerk", "Clerical Support Aide II", n, er)
        self.clerk2 = self.person("clerk2", "Data Encoder I", n, peds)
        self.medtech = self.person("medtech", "Clinical Medical Technologist", self.lab)
        self.er_head = self.person("erhead", "Nurse II", n, er, R.SUPERVISOR, role_unit=er)
        self.peds_head = self.person("pedshead", "Nurse II", n, peds, R.SUPERVISOR, role_unit=peds)
        self.cn = self.person("chiefnurse", "Nurse II/ Chief Nurse", n, None, R.SUPERVISOR, role_section=n)
        self.hr = self.person("hrp", "HR Assistant", role=R.HR_PROCESSOR)
        self.hra = self.person("hra", "HRMO", role=R.HR_ADMINISTRATOR)
        self.ao = self.person("ao", "Administrative Officer V", role=R.ADMINISTRATIVE_OFFICER)
        self.coh = self.person("coh", "Chief of Hospital", role=R.CHIEF_OF_HOSPITAL)
        shift("8A-8P", 8, 20, 12)
        shift("5A-5P", 5, 17, 12)
        shift("3-11", 15, 23, 8)
        shift("7-3", 7, 15, 8)

    # -- schedules -------------------------------------------------------
    def draft(self, unit, preparer):
        return services.create_schedule(preparer.user, srules.preparer_role(preparer, unit=unit), YEAR, MONTH,
                                        unit=unit)

    def set_days(self, schedule, employee, mapping):
        row = schedule.rows.filter(employee=employee).first() or services.add_row(schedule, employee)
        for day, c in mapping.items():
            ScheduleCell.objects.filter(row=row, date=d(day)).update(shift=code(c) if c else None)

    def cell(self, schedule, employee, day):
        c = ScheduleCell.objects.filter(row__schedule=schedule, row__employee=employee, date=d(day)).first()
        return c.shift.code if c and c.shift_id else None

    # -- routing helpers ---------------------------------------------------
    def file(self, a, b, date_a=10, date_b=11, shift_a="7A-7P", shift_b="7P-7A", **extra):
        self.client.force_login(a.user)
        data = {"employee_b": b.pk, "date_a": d(date_a), "date_b": d(date_b), "reason": "family",
                "shift_a": code(shift_a).pk, "shift_b": code(shift_b).pk}
        data.update(extra)
        return self.client.post(reverse("exchange:exchange_apply"), data)

    def consent(self, x):
        self.client.force_login(x.employee_b.user)
        self.client.post(reverse("exchange:exchange_consent", args=[x.pk]), {"decision": "consent"})
        x.refresh_from_db()
        return x

    def act(self, who, x, action, notes=""):
        self.client.force_login(who.user)
        r = self.client.post(reverse("exchange:exchange_action", args=[x.pk]), {"action": action, "notes": notes})
        x.refresh_from_db()
        return r


class PositionAndGroupTests(Org):
    def test_seeded_groups_and_position_records(self):
        names = set(ExchangeGroup.objects.values_list("name", flat=True))
        self.assertTrue({"Nurse", "Nursing Attendant", "Midwife", "Medical Technologist", "Radiologic Technologist",
                         "Pharmacist"} <= names)
        self.assertEqual(Position.objects.get(title_key=title_key("Clinical Nurse")).exchange_group.name, "Nurse")
        self.assertEqual(Position.objects.get(title_key="nursing aide ii").exchange_group.name, "Nursing Attendant")
        self.assertEqual(Position.objects.get(title_key="ros clinical pharmacist iii").exchange_group.name,
                         "Pharmacist")
        self.assertIsNone(Position.objects.get(title_key="clerical support aide ii").exchange_group)

    def test_record_not_typed_text(self):
        self.er_nurse.position = "  nurse   I "
        self.er_nurse.save()
        self.assertEqual(self.er_nurse.position_ref, Position.objects.get(title_key="nurse i"))
        self.assertEqual(self.peds_nurse.position_ref.title_key, "clinical nurse")  # typed in capitals

    def test_er_and_pediatric_nurse_allowed(self):  # the real case
        self.assertEqual(location_errors(self.er_nurse, self.peds_nurse), [])

    def test_nurse_i_and_nurse_iii_allowed(self):
        self.assertEqual(location_errors(self.er_nurse, self.nurse3), [])

    def test_nurse_and_midwife_blocked(self):
        self.assertTrue(any("Exchange group" in e for e in location_errors(self.er_nurse, self.midwife)))

    def test_attendant_and_nurse_blocked(self):
        self.assertTrue(any("Exchange group" in e for e in location_errors(self.attendant, self.er_nurse)))

    def test_grouped_and_blank_blocked_two_blank_allowed(self):
        self.assertTrue(location_errors(self.er_nurse, self.clerk))
        self.assertEqual(location_errors(self.clerk, self.clerk2), [])  # different units, same section

    def test_different_sections_blocked_float_staff_allowed(self):
        self.assertTrue(any("same section" in e for e in location_errors(self.medtech, self.er_nurse)))
        floater = self.person("floater", "Nurse I", self.lab)
        self.assertTrue(location_errors(floater, self.er_nurse))
        floater.sections.add(self.nursing)
        self.assertEqual(location_errors(floater, self.er_nurse), [])


class LengthAndDutyTests(Org):
    def setUp(self):
        super().setUp()
        self.s = self.draft(self.er, self.er_head)
        self.x = self.er_nurse  # 12-hour pattern

    def errs(self, give, take, shift_code):
        return check_taker(self.x, d(give), d(take), code(shift_code))

    def test_unequal_length_blocked(self):
        errors, _, _ = check_swap(self.er_nurse, d(10), code("8-5"), self.peds_nurse, d(11), code("7A-7P"))
        self.assertTrue(any("different lengths" in e and "file OT" in e for e in errors))
        self.assertEqual(check_swap(self.er_nurse, d(10), code("7A-7P"), self.peds_nurse, d(11),
                                    code("8A-8P"))[0], [])

    def test_unequal_length_blocked_on_the_form(self):
        self.file(self.er_nurse, self.peds_nurse, shift_a="8-5", shift_b="7A-7P")
        self.assertFalse(X.objects.exists())

    def test_night_ending_7am_then_day_same_morning_blocked(self):
        self.set_days(self.s, self.x, {9: "7P-7A"})
        errors, _ = self.errs(20, 10, "7A-7P")
        self.assertTrue(errors and "straight duty" in errors[0])

    def test_day_ending_7pm_then_night_from_7pm_blocked(self):
        self.set_days(self.s, self.x, {10: "7A-7P"})
        errors, _ = self.errs(20, 10, "7P-7A")
        self.assertTrue(errors and "straight duty" in errors[0])

    def test_monday_night_then_tuesday_8am_blocked_pm_to_am(self):
        self.set_days(self.s, self.x, {8: "7P-7A"})  # Mon Mar 8
        errors, _ = self.errs(20, 9, "8A-8P")
        self.assertTrue(errors and "PM to AM" in errors[0])

    def test_pm_duty_to_7am_blocked_even_with_8_hours_rest(self):
        self.x.shift_hours = 8
        self.set_days(self.s, self.x, {8: "3-11"})
        errors, _ = self.errs(20, 9, "7-3")  # 23:00 -> 07:00 = 8 h, rest met
        self.assertTrue(errors and "PM to AM" in errors[0])

    def test_less_than_12_hours_after_a_12_hour_duty_blocked(self):
        self.set_days(self.s, self.x, {8: "7A-7P"})
        errors, _ = self.errs(20, 9, "5A-5P")  # 10 h rest
        self.assertTrue(errors and "Blocked (rest)" in errors[0])

    def test_allowed_patterns(self):
        cases = [({8: "7A-7P"}, 9, "7A-7P"),   # day 12s on consecutive days (12 h gap)
                 ({8: "7A-7P"}, 9, "7P-7A"),   # Monday day, Tuesday night
                 ({8: "7P-7A"}, 10, "7A-7P")]  # Monday night, AM duty two days later
        for mapping, take, c in cases:
            self.set_days(self.s, self.x, {day: None for day in range(1, 32)})
            self.set_days(self.s, self.x, mapping)
            self.assertEqual(self.errs(20, take, c)[0], [], (mapping, take, c))
        self.x.shift_hours = 8
        self.set_days(self.s, self.x, {day: None for day in range(1, 32)})
        self.set_days(self.s, self.x, {8: "8-5"})
        self.assertEqual(self.errs(20, 9, "8-5")[0], [])  # 8-hour AM, then AM next day

    def test_duty_given_up_is_removed_first(self):
        self.set_days(self.s, self.x, {9: "7P-7A"})
        self.assertEqual(self.errs(9, 10, "7A-7P")[0], [])

    def test_fourth_twelve_hour_day_in_a_row_is_a_warning(self):
        self.set_days(self.s, self.x, {8: "7A-7P", 9: "7A-7P", 10: "7A-7P"})
        errors, warnings = self.errs(20, 11, "7A-7P")
        self.assertEqual(errors, [])
        self.assertTrue(warnings and "4 duty days in a row" in warnings[0])

    def test_problem_already_in_the_schedule_does_not_block(self):
        self.set_days(self.s, self.x, {3: "7P-7A", 4: "7A-7P"})  # an old PM-to-AM, nothing to do with the swap
        self.assertEqual(self.errs(20, 10, "7A-7P")[0], [])

    def test_settings_are_used(self):
        r = ExchangeRules.get()
        r.rest_after_12 = Decimal("9")
        r.save()
        self.set_days(self.s, self.x, {8: "7A-7P"})
        self.assertEqual(self.errs(20, 9, "5A-5P")[0], [])


class CrossUnitRouteTests(Org):
    def to_consent(self, a=None, b=None):
        self.file(a or self.er_nurse, b or self.peds_nurse)
        return self.consent(X.objects.get())

    def test_both_unit_heads_then_chief_nurse_then_chain_and_both_drafts(self):
        er_draft, peds_draft = self.draft(self.er, self.er_head), self.draft(self.peds, self.peds_head)
        self.set_days(er_draft, self.er_nurse, {10: "7A-7P"})
        self.set_days(peds_draft, self.peds_nurse, {11: "7P-7A"})
        x = self.to_consent()
        self.assertTrue(x.new_rules and x.is_cross_unit)
        for head in (self.er_head, self.peds_head):
            self.assertTrue(Notification.objects.filter(recipient=head, url="/exchange/queue/").exists())
        self.assertEqual(self.act(self.cn, x, "endorse").status_code, 403)  # not before the unit heads
        self.act(self.peds_head, x, "endorse")
        self.assertEqual(x.status, X.ENDORSED_BY_ONE_UNIT_HEAD)
        self.assertEqual(self.act(self.peds_head, x, "endorse").status_code, 403)  # not twice
        self.assertTrue(Notification.objects.filter(recipient=self.er_head, message__contains="other unit head")
                        .exists())
        self.act(self.er_head, x, "endorse")
        self.assertEqual(x.status, X.ENDORSED_BY_UNIT_HEAD)
        self.act(self.cn, x, "endorse")
        self.assertEqual(x.status, X.ENDORSED_BY_SUPERVISOR)
        self.act(self.hr, x, "process")
        self.act(self.ao, x, "recommend")
        self.act(self.coh, x, "approve")
        self.assertEqual(x.status, X.APPROVED)
        # ER draft: the ER nurse is OFF and the Pediatric nurse works her shift there.
        self.assertEqual((self.cell(er_draft, self.er_nurse, 10), self.cell(er_draft, self.peds_nurse, 10)),
                         ("OFF", "7A-7P"))
        self.assertEqual((self.cell(peds_draft, self.peds_nurse, 11), self.cell(peds_draft, self.er_nurse, 11)),
                         ("OFF", "7P-7A"))
        self.assertNotEqual(self.cell(peds_draft, self.peds_nurse, 10), "7A-7P")  # own schedule: not on duty
        actions = [r["action"] for r in audit_rows(module="Exchange of Duty")]
        self.assertIn("Endorsed by the first of two unit heads", actions)

    def test_draft_created_later_gets_both_sides_and_locks(self):
        from .regular import locked_cells

        x = self.to_consent()
        X.objects.filter(pk=x.pk).update(status=X.RECOMMENDED_BY_AO)
        self.act(self.coh, x, "approve")
        er_draft = self.draft(self.er, self.er_head)
        self.assertEqual(self.cell(er_draft, self.peds_nurse, 10), "7A-7P")
        self.assertIn((self.peds_nurse.pk, d(10)), locked_cells(er_draft))
        peds_draft = self.draft(self.peds, self.peds_head)
        self.assertNotIn((self.peds_nurse.pk, d(10)), locked_cells(peds_draft))
        self.assertEqual(self.cell(peds_draft, self.er_nurse, 11), "7P-7A")

    def test_second_unit_head_return_needs_a_remark(self):
        x = self.to_consent()
        self.act(self.er_head, x, "endorse")
        self.act(self.peds_head, x, "return")
        self.assertEqual(x.status, X.ENDORSED_BY_ONE_UNIT_HEAD)  # nothing changed without a remark
        self.act(self.peds_head, x, "return", "Short-staffed that day")
        self.assertEqual(x.status, X.RETURNED)

    def test_unit_head_who_is_a_party(self):
        x = self.to_consent(a=self.er_head, b=self.peds_nurse)
        self.assertEqual(self.act(self.er_head, x, "endorse").status_code, 403)
        self.act(self.peds_head, x, "endorse")  # only the other side has a unit step
        self.assertEqual(x.status, X.ENDORSED_BY_UNIT_HEAD)
        self.act(self.cn, x, "endorse")
        self.assertEqual(x.status, X.ENDORSED_BY_SUPERVISOR)
        self.client.force_login(self.er_head.user)
        self.assertNotIn(x, self.client.get(reverse("exchange:exchange_queue")).context["requests"])

    def test_oic_covers_the_absent_unit_head(self):
        oic = self.person("eroic", "Nurse II", self.nursing, self.er)
        R.objects.create(employee=oic, role=R.SUPERVISOR, unit=self.er, is_oic=True, delegated_by=self.er_head,
                         start_date=date(2026, 1, 1), end_date=date(2099, 12, 31))
        x = self.to_consent()
        self.assertEqual(self.act(self.er_head, x, "endorse").status_code, 403)  # view only while the OIC acts
        self.act(oic, x, "endorse")
        self.assertEqual(x.status, X.ENDORSED_BY_ONE_UNIT_HEAD)
        self.act(self.peds_head, x, "endorse")
        self.assertEqual(x.status, X.ENDORSED_BY_UNIT_HEAD)

    def test_same_unit_route_unchanged(self):
        x = self.to_consent(a=self.er_nurse, b=self.nurse3)
        self.assertFalse(x.is_cross_unit)
        self.act(self.er_head, x, "endorse")
        self.assertEqual(x.status, X.ENDORSED_BY_UNIT_HEAD)

    def test_older_request_keeps_the_old_route(self):
        x = X.objects.create(employee_a=self.er_nurse, date_a=d(10), employee_b=self.peds_nurse, date_b=d(11),
                             status=X.SUBMITTED, kind=X.KIND_REGULAR, shift_a=code("7A-7P"),
                             shift_b=code("7P-7A"))
        self.assertFalse(x.new_rules)
        self.act(self.peds_head, x, "endorse")
        self.assertEqual(x.status, X.ENDORSED_BY_UNIT_HEAD)  # one unit head, as before

    def test_fourth_exchange_in_a_month_blocked(self):
        for i, (da, db) in enumerate(((1, 2), (3, 4), (5, 6))):
            X.objects.create(employee_a=self.er_nurse, date_a=d(da), employee_b=self.nurse3, date_b=d(db),
                             status=X.SUBMITTED)
        self.file(self.er_nurse, self.peds_nurse, date_a=20, date_b=21)
        self.assertEqual(X.objects.count(), 3)

    def test_warning_shown_to_unit_heads(self):
        er_draft = self.draft(self.er, self.er_head)
        self.set_days(er_draft, self.er_nurse, {10: "7A-7P", 12: "7A-7P", 13: "7A-7P", 14: "7A-7P"})
        self.file(self.er_nurse, self.peds_nurse, date_a=10, date_b=11, shift_a="7A-7P", shift_b="7A-7P")
        x = self.consent(X.objects.get())
        self.assertTrue(x.warning_list)
        self.client.force_login(self.er_head.user)
        self.assertContains(self.client.get(reverse("exchange:exchange_queue")), "duty days in a row")


class ScheduleSubmitFlagTests(Org):
    def test_rest_problem_next_to_an_exchange_day_is_flagged_not_blocked(self):
        x = X.objects.create(employee_a=self.er_nurse, date_a=d(10), employee_b=self.nurse3, date_b=d(11),
                             status=X.APPROVED, kind=X.KIND_REGULAR, shift_a=code("7A-7P"), shift_b=code("7A-7P"),
                             new_rules=True)
        er_draft = self.draft(self.er, self.er_head)
        self.assertEqual(self.cell(er_draft, self.nurse3, 10), "7A-7P")
        self.set_days(er_draft, self.nurse3, {9: "7P-7A"})  # the preparer later puts a night before it
        flags = services.submit(er_draft, self.er_head.user)
        er_draft.refresh_from_db()
        self.assertEqual(er_draft.status, S.SUBMITTED)
        self.assertTrue(flags and "straight duty" in flags[0])
        self.assertIsNotNone(x)


class SettingsPageTests(Org):
    def test_positions_page_hr_admin_only_and_logged(self):
        url = reverse("exchange:positions")
        self.client.force_login(self.hr.user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.hra.user)
        r = self.client.get(url)
        self.assertContains(r, "Clerical Support Aide II")
        self.assertContains(r, "Positions and Exchange Groups")  # menu
        clerk_pos = self.clerk.position_ref
        nurse = ExchangeGroup.objects.get(name="Nurse")
        self.client.post(url, {"what": "save", f"group_{clerk_pos.pk}": str(nurse.pk)})
        clerk_pos.refresh_from_db()
        self.assertEqual(clerk_pos.exchange_group, nurse)
        self.assertTrue(any(r["action"] == "Exchange group changed"
                            for r in audit_rows(module="Exchange of Duty")))
        self.client.post(url, {"what": "add_group", "name": "Physician"})
        self.assertTrue(ExchangeGroup.objects.filter(name="Physician").exists())

    def test_rules_page(self):
        url = reverse("exchange:rules")
        self.client.force_login(self.er_head.user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.hra.user)
        self.client.post(url, {"rest_after_8": "8", "rest_after_12": "11", "pm_from": "12:00",
                               "consecutive_8": "5", "consecutive_12": "3"})
        self.assertEqual(ExchangeRules.get().rest_after_12, Decimal("11"))
        self.assertTrue(any(r["action"] == "Rule changed" for r in audit_rows(module="Exchange of Duty")))
