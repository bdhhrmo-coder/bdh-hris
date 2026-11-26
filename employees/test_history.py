"""Holidays and assignment history (PMS step 1, rebuilt 2026-11-26). All data made up."""

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment as R
from auditlog.aggregation import audit_rows
from orgstructure.models import Section, Unit

from . import history as H
from .holidays import add_holiday, add_working_days, is_working_day, void_holiday, working_days
from .models import AssignmentLine as L, Employee, Holiday, PersonnelActionType

TODAY = timezone.localdate()


def person(name, role=None, section=None, unit=None):
    user = User.objects.create_user(name, password="x")
    e = Employee.objects.create(user=user, employee_id=name.upper(), surname=name.title(), first_name="T",
                                position="Nurse I", date_hired=date(2024, 1, 1), employment_status="REGULAR")
    if section:
        e.sections.add(section)
    if unit:
        e.units.add(unit)
    if role:
        R.objects.create(employee=e, role=role)
    return e


class HolidayTests(TestCase):
    def setUp(self):
        self.hra = person("hra", R.HR_ADMINISTRATOR)

    def test_working_days_skip_weekends_and_holidays(self):
        mon = date(2027, 3, 1)  # a Monday
        self.assertEqual(working_days(mon, mon + timedelta(days=6)), 5)
        add_holiday(self.hra.user, date(2027, 3, 3), "Test holiday", Holiday.SPECIAL)
        self.assertFalse(is_working_day(date(2027, 3, 3)))
        self.assertEqual(working_days(mon, mon + timedelta(days=6)), 4)
        self.assertEqual(add_working_days(mon, 5), date(2027, 3, 9))  # Tue, Thu, Fri, Mon, Tue

    def test_one_active_per_date_void_with_reason(self):
        h = add_holiday(self.hra.user, date(2027, 4, 9), "Day of Valor", Holiday.REGULAR)
        with self.assertRaises(ValidationError):
            add_holiday(self.hra.user, date(2027, 4, 9), "Again", Holiday.REGULAR)
        with self.assertRaises(ValidationError):
            void_holiday(self.hra.user, h, "")
        void_holiday(self.hra.user, h, "Wrong date")
        add_holiday(self.hra.user, date(2027, 4, 9), "Day of Valor", Holiday.REGULAR)
        self.assertEqual(Holiday.objects.filter(date=date(2027, 4, 9)).count(), 2)
        self.assertIn("Holiday voided", [r["action"] for r in audit_rows(module="Holidays")])

    def test_page_hr_admin_only(self):
        hrp = person("hrp", R.HR_PROCESSOR)
        self.client.force_login(hrp.user)
        self.assertEqual(self.client.get(reverse("employees:holidays")).status_code, 403)
        self.client.force_login(self.hra.user)
        self.client.post(reverse("employees:holidays"), {"date": "2027-06-12", "name": "Independence Day",
                                                         "kind": Holiday.REGULAR})
        self.assertTrue(Holiday.objects.filter(date=date(2027, 6, 12)).exists())


class AssignmentHistoryTests(TestCase):
    def setUp(self):
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.lab = Section.objects.get(name="Laboratory Section")
        self.er = Unit.objects.get(section=self.nursing, name="ER")
        self.opd = Unit.objects.get(section=self.nursing, name="OPD")
        self.hrp = person("hrp", R.HR_PROCESSOR)
        self.hra = person("hra", R.HR_ADMINISTRATOR)
        self.hra2 = person("hra2", R.HR_ADMINISTRATOR)
        self.nurse = person("nurse", section=self.nursing, unit=self.er)
        self.transfer = PersonnelActionType.objects.get(name="Transfer")

    def test_sixteen_action_types_loaded(self):
        self.assertEqual(PersonnelActionType.objects.count(), 16)
        self.assertTrue(PersonnelActionType.objects.filter(name="Secondment").exists())

    def test_encode_verify_sets_membership(self):
        line = H.encode(self.hrp, self.hrp.user, self.nurse, self.nursing, self.opd, self.transfer, TODAY,
                        reference="OO-TEST-1")
        self.assertEqual(set(self.nurse.units.all()), {self.er})  # nothing changes before verification
        with self.assertRaises(ValidationError):
            H.verify(self.hrp, self.hrp.user, line)  # an HR Processor does not verify
        H.verify(self.hra, self.hra.user, line)
        self.assertEqual(set(self.nurse.units.all()), {self.opd})
        self.assertEqual(set(self.nurse.sections.all()), {self.nursing})

    def test_close_is_verified_too(self):
        H.load_opening_lines(self.hra, self.hra.user, date(2026, 1, 1))
        line = L.objects.get(employee=self.nurse)
        self.assertEqual(set(self.nurse.units.all()), {self.er})  # opening lines change nobody's membership
        H.request_close(self.hrp, self.hrp.user, line, TODAY, "OO-TEST-2")
        H.verify_close(self.hra, self.hra.user, line)
        line.refresh_from_db()
        self.assertEqual(line.effective_to, TODAY)
        self.assertFalse(self.nurse.units.exists())

    def test_never_on_own_record_and_never_own_encoding(self):
        with self.assertRaises(ValidationError):
            H.encode(self.hra, self.hra.user, self.hra, self.lab, None, self.transfer, TODAY)
        line = H.encode(self.hra, self.hra.user, self.nurse, self.lab, None, self.transfer, TODAY)
        with self.assertRaises(ValidationError):
            H.verify(self.hra, self.hra.user, line)  # not what you encoded
        on_hra2 = H.encode(self.hrp, self.hrp.user, self.hra2, self.lab, None, self.transfer, TODAY)
        with self.assertRaises(ValidationError):
            H.verify(self.hra2, self.hra2.user, on_hra2)  # not your own record
        H.verify(self.hra2, self.hra2.user, line)

    def test_no_future_dates_no_overlaps_unit_in_section(self):
        with self.assertRaises(ValidationError):
            H.encode(self.hrp, self.hrp.user, self.nurse, self.lab, None, self.transfer, TODAY + timedelta(days=1))
        with self.assertRaises(ValidationError):
            H.encode(self.hrp, self.hrp.user, self.nurse, self.lab, self.er, self.transfer, TODAY)
        H.encode(self.hrp, self.hrp.user, self.nurse, self.lab, None, self.transfer, TODAY)
        with self.assertRaises(ValidationError):
            H.encode(self.hrp, self.hrp.user, self.nurse, self.lab, None, self.transfer, TODAY)

    def test_edit_form_locks_sections_once_lines_exist(self):
        from .forms import EmployeeForm

        self.assertFalse(EmployeeForm(instance=self.nurse, acting_employee=self.hra).fields["sections"].disabled)
        H.load_opening_lines(self.hra, self.hra.user, date(2026, 1, 1))
        self.assertTrue(EmployeeForm(instance=self.nurse, acting_employee=self.hra).fields["units"].disabled)

    def test_pages_and_audit(self):
        self.client.force_login(self.hrp.user)
        r = self.client.get(reverse("employees:assignments") + "?q=nurse")
        self.assertContains(r, self.nurse.full_name)
        self.assertContains(r, "Assignment History")  # menu
        self.client.post(reverse("employees:employee_assignments", args=[self.nurse.pk]),
                         {"what": "encode", "section": self.lab.pk, "action_type": self.transfer.pk,
                          "effective_from": TODAY.isoformat()})
        self.assertTrue(L.objects.filter(employee=self.nurse, status=L.PENDING).exists())
        self.client.force_login(self.nurse.user)
        self.assertEqual(self.client.get(reverse("employees:employee_assignments", args=[self.nurse.pk])).status_code,
                         200)  # own record, read only
        self.assertEqual(self.client.get(reverse("employees:employee_assignments", args=[self.hra.pk])).status_code,
                         403)
        self.assertIn("Line encoded", [r["action"] for r in audit_rows(module="Assignment History")])
