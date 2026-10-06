import io
from datetime import date
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from cto.balances import compute_available_cto_balance
from employees.models import Employee, EmployeeEditHistory
from leave.balances import compute_available_balance
from leave.models import LeaveType

from . import balance_import, employee_import
from .template import build_template

HR_SECTION = "Human Resources Management Section"


def make_employee(username, employee_id, *roles, status="REGULAR", hired=date(2020, 1, 1)):
    user = User.objects.create_user(username=username, password="pw-12345678")
    emp = Employee.objects.create(user=user, employee_id=employee_id, surname=username.title(), first_name="Test",
                                  employment_status=status, date_hired=hired)
    for role in roles:
        RoleAssignment.objects.create(employee=emp, role=role)
    return emp


def workbook_file(sheet, columns, rows):
    """An uploaded .xlsx with `sheet` holding the header + rows (dicts)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append([header for _, header in columns])
    for row in rows:
        ws.append([row.get(key) for key, _ in columns])
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile("import.xlsx", buf.getvalue(),
                              content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


EMP_COLS = [(k, h) for k, h, _ in employee_import.COLUMNS]
NEW_ROW = {"employee_id": "EMP-0101", "surname": "Dela Cruz", "first_name": "Juana", "employment_status": "Regular",
           "date_hired": "2019-06-03", "sections": HR_SECTION, "position": "Nurse I"}


class EmployeeImportTests(TestCase):
    def setUp(self):
        self.hradmin = make_employee("imp_hradmin", "EMP-9902", RoleAssignment.HR_ADMINISTRATOR)
        self.processor = make_employee("imp_proc", "EMP-9903", RoleAssignment.HR_PROCESSOR)

    def plan(self, rows, actor=None):
        return employee_import.build_plan(workbook_file(employee_import.SHEET, EMP_COLS, rows), actor or self.hradmin)

    def test_new_employee_gets_login_with_temporary_password_and_must_change(self):
        plans = self.plan([NEW_ROW])
        self.assertEqual(plans[0].errors, [])
        created, updated, creds = employee_import.apply_plan(plans, self.hradmin.user, "f.xlsx")
        self.assertEqual((created, updated, len(creds)), (1, 0, 1))
        emp = Employee.objects.get(employee_id="EMP-0101")
        self.assertEqual(emp.user.username, "EMP-0101")
        self.assertTrue(emp.must_change_password)
        self.assertTrue(emp.user.check_password(creds[0]["password"]))
        self.assertEqual([s.name for s in emp.sections.all()], [HR_SECTION])
        self.assertTrue(emp.has_role(RoleAssignment.EMPLOYEE))

    def test_missing_required_values_and_bad_values_block_everything(self):
        rows = [dict(NEW_ROW), {"employee_id": "EMP-0102", "surname": "X", "employment_status": "Permanent",
                                "date_hired": "31/31/2020", "sections": "No Such Section"}]
        plans = self.plan(rows)
        errors = " ".join(plans[1].errors)
        self.assertIn("First Name is required", errors)
        self.assertIn('Employment Status "Permanent"', errors)
        self.assertIn("is not a date", errors)
        self.assertIn('Section "No Such Section" not found', errors)
        with self.assertRaises(AssertionError):
            employee_import.apply_plan(plans, self.hradmin.user, "f.xlsx")
        self.assertFalse(Employee.objects.filter(employee_id="EMP-0101").exists())

    def test_duplicate_ids_in_file_are_errors(self):
        plans = self.plan([NEW_ROW, NEW_ROW])
        self.assertIn("also on row 2", " ".join(plans[1].errors))

    def test_existing_employee_is_updated_blanks_never_erase_and_history_is_written(self):
        emp = make_employee("imp_exist", "EMP-0200")
        emp.position, emp.telephone_mobile = "Nurse I", "0917"
        emp.save()
        plans = self.plan([{"employee_id": "EMP-0200", "position": "Nurse II"}])
        self.assertEqual(plans[0].action, "update")
        employee_import.apply_plan(plans, self.hradmin.user, "f.xlsx")
        emp.refresh_from_db()
        self.assertEqual(emp.position, "Nurse II")
        self.assertEqual(emp.telephone_mobile, "0917")  # blank cell did not erase
        h = EmployeeEditHistory.objects.get(employee=emp)
        self.assertEqual((h.old_value, h.new_value), ("Nurse I", "Nurse II"))
        self.assertFalse(emp.must_change_password)  # existing login untouched

    def test_unchanged_row_is_no_change(self):
        make_employee("imp_same", "EMP-0201")
        self.assertEqual(self.plan([{"employee_id": "EMP-0201"}])[0].action, "no change")

    def test_hr_admin_can_add_roles_supervisor_scoped_to_section(self):
        row = dict(NEW_ROW, roles="Supervisor; ICTU Staff")
        plans = self.plan([row])
        self.assertEqual(plans[0].errors, [])
        employee_import.apply_plan(plans, self.hradmin.user, "f.xlsx")
        emp = Employee.objects.get(employee_id="EMP-0101")
        sup = emp.role_assignments.get(role=RoleAssignment.SUPERVISOR)
        self.assertEqual(sup.section.name, HR_SECTION)
        self.assertTrue(emp.has_role(RoleAssignment.ICTU_STAFF))

    def test_processor_cannot_assign_roles(self):
        plans = self.plan([dict(NEW_ROW, roles="HR Administrator")], actor=self.processor)
        self.assertIn("Only an HR Administrator may assign roles", " ".join(plans[0].errors))

    def test_system_administrator_never_by_import(self):
        plans = self.plan([dict(NEW_ROW, roles="System Administrator")])
        self.assertIn("System Administrator cannot be given by import", " ".join(plans[0].errors))

    def test_username_already_taken_is_an_error(self):
        User.objects.create_user(username="EMP-0101", password="x")
        self.assertIn("already used", " ".join(self.plan([NEW_ROW])[0].errors))

    def test_template_has_all_sheets_and_columns(self):
        wb = build_template()
        self.assertEqual(wb.sheetnames, ["Instructions", "Employees", "Opening Balances", "Sections and Units"])
        headers = [c.value for c in wb["Employees"][1]]
        self.assertEqual(headers, [h for _, h in EMP_COLS])
        listed = [c.value for c in wb["Opening Balances"]["A"]][1:]
        self.assertIn("EMP-9902", listed)


BAL_COLS = balance_import.COLUMNS


class OpeningBalanceImportTests(TestCase):
    def setUp(self):
        self.hr = make_employee("bal_hr", "EMP-9910", RoleAssignment.HR_ADMINISTRATOR)
        self.emp = make_employee("bal_emp", "EMP-0300", hired=date(2016, 1, 1))  # ~10 years of accrual
        self.cosp = make_employee("bal_cosp", "EMP-0301", status="COSP", hired=date(2026, 1, 1))
        self.today = date(2026, 10, 6)

    def plan(self, rows):
        return balance_import.build_plan(workbook_file(balance_import.SHEET, BAL_COLS, rows), today=self.today)

    def test_balance_set_to_leave_card_value(self):
        vl = LeaveType.objects.get(code="VL")
        as_of = date(2026, 9, 30)
        self.assertGreater(compute_available_balance(self.emp, vl, as_of), Decimal("100"))  # overstated
        plans = self.plan([{"employee_id": "EMP-0300", "as_of": "2026-09-30", "VL": 12.375, "CTO": 2}])
        self.assertEqual(plans[0].errors, [])
        balance_import.apply_plan(plans, self.hr.user, "b.xlsx")
        self.assertEqual(compute_available_balance(self.emp, vl, as_of), Decimal("12.38"))
        self.assertEqual(compute_available_cto_balance(self.emp, as_of), Decimal("2"))

    def test_importing_same_file_twice_adds_nothing(self):
        rows = [{"employee_id": "EMP-0300", "as_of": "2026-09-30", "SL": 30}]
        balance_import.apply_plan(self.plan(rows), self.hr.user, "b.xlsx")
        again = self.plan(rows)
        self.assertEqual(again[0].action, "no change")
        self.assertEqual(balance_import.apply_plan(again, self.hr.user, "b.xlsx"), 0)

    def test_leave_type_must_apply_to_employee(self):
        plans = self.plan([{"employee_id": "EMP-0301", "as_of": "2026-09-30", "VL": 5},
                           {"employee_id": "EMP-0300", "as_of": "2026-09-30", "COSP_LEAVE": 5}])
        self.assertIn("does not apply", " ".join(plans[0].errors))
        self.assertIn("does not apply", " ".join(plans[1].errors))

    def test_bad_inputs(self):
        plans = self.plan([
            {"employee_id": "NOPE", "as_of": "2026-09-30", "VL": 1},
            {"employee_id": "EMP-0300", "as_of": "2026-12-31", "VL": 1},
            {"employee_id": "EMP-0300", "as_of": "2026-09-30", "VL": -1},
            {"employee_id": "EMP-0301", "as_of": "2026-09-30", "WELLNESS": 9},
        ])
        self.assertIn("Import the employee list first", " ".join(plans[0].errors))
        self.assertIn("future", " ".join(plans[1].errors))
        self.assertIn("between 0", " ".join(plans[2].errors))
        self.assertIn("cannot be more than", " ".join(plans[3].errors))


class ImportScreenTests(TestCase):
    def setUp(self):
        self.hradmin = make_employee("scr_hradmin", "EMP-9920", RoleAssignment.HR_ADMINISTRATOR)
        self.plain = make_employee("scr_plain", "EMP-9921", RoleAssignment.EMPLOYEE)

    def test_only_hr_can_open(self):
        self.client.force_login(self.plain.user)
        self.assertEqual(self.client.get(reverse("dataimport:home")).status_code, 403)
        self.client.force_login(self.hradmin.user)
        self.assertEqual(self.client.get(reverse("dataimport:home")).status_code, 200)

    def test_upload_preview_then_confirm_then_slips_then_forced_password_change(self):
        self.client.force_login(self.hradmin.user)
        f = workbook_file(employee_import.SHEET, EMP_COLS, [NEW_ROW])
        r = self.client.post(reverse("dataimport:upload", args=["employees"]), {"file": f})
        self.assertContains(r, "Confirm and save")
        self.assertFalse(Employee.objects.filter(employee_id="EMP-0101").exists())  # preview only
        self.client.post(reverse("dataimport:confirm", args=["employees"]))
        emp = Employee.objects.get(employee_id="EMP-0101")
        slips = self.client.session["dataimport_slips"]
        self.assertEqual(slips[0]["employee_id"], "EMP-0101")
        r = self.client.get(reverse("dataimport:password_slips", args=["xlsx"]))
        self.assertEqual(r.status_code, 200)
        self.client.post(reverse("dataimport:clear_slips"))
        self.assertNotIn("dataimport_slips", self.client.session)

        # The new person logs in with the temporary password and is sent to change it.
        self.client.logout()
        self.client.post(reverse("login"), {"username": "EMP-0101", "password": slips[0]["password"]})
        r = self.client.get(reverse("notifications:notification_list"))
        self.assertRedirects(r, reverse("password_change"), fetch_redirect_response=False)
        self.client.post(reverse("password_change"), {
            "old_password": slips[0]["password"], "new_password1": "Bataraza#Nurse2026",
            "new_password2": "Bataraza#Nurse2026",
        })
        emp.refresh_from_db()
        self.assertFalse(emp.must_change_password)
        self.assertEqual(self.client.get(reverse("notifications:notification_list")).status_code, 200)

    def test_file_with_errors_cannot_be_confirmed(self):
        self.client.force_login(self.hradmin.user)
        f = workbook_file(employee_import.SHEET, EMP_COLS, [dict(NEW_ROW, surname="")])
        r = self.client.post(reverse("dataimport:upload", args=["employees"]), {"file": f})
        self.assertNotContains(r, "Confirm and save")
        self.client.post(reverse("dataimport:confirm", args=["employees"]))
        self.assertFalse(Employee.objects.filter(employee_id="EMP-0101").exists())

    def test_wrong_file_gives_plain_message(self):
        self.client.force_login(self.hradmin.user)
        bad = SimpleUploadedFile("x.xlsx", b"not a workbook")
        r = self.client.post(reverse("dataimport:upload", args=["employees"]), {"file": bad}, follow=True)
        self.assertContains(r, "could not be opened")
