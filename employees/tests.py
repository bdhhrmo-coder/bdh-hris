from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment

from .models import Employee, EmployeeEditHistory


def make_employee(username, employee_id, role=None, **extra):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user,
        employee_id=employee_id,
        surname=username.title(),
        first_name="Test",
        **extra,
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role)
    return employee


class EmployeeScreenPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.hr_admin_a = make_employee("hradmin_a", "EMP-001", role=RoleAssignment.HR_ADMINISTRATOR)
        self.hr_admin_b = make_employee("hradmin_b", "EMP-002", role=RoleAssignment.HR_ADMINISTRATOR)
        self.sysadmin = make_employee("sysadmin1", "EMP-003", role=RoleAssignment.SYSTEM_ADMINISTRATOR)
        self.rank_and_file = make_employee("nurse1", "EMP-004", role=RoleAssignment.EMPLOYEE)

    # --- Decision 1: employee_id / is_active are System-Administrator only ---
    def test_hr_administrator_cannot_change_employee_id_or_active(self):
        self.client.login(username="hradmin_a", password="testpass123")
        target = self.rank_and_file
        old_id = target.employee_id
        response = self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": "HACKED-999",
                "is_active": "",  # attempt to deactivate
                "surname": "Changed",
                "first_name": target.first_name,
                "employment_status": target.employment_status,
                "reason": "Testing lockdown of restricted fields",
            },
        )
        target.refresh_from_db()
        self.assertEqual(target.employee_id, old_id, "HR Administrator must not be able to change employee_id")
        self.assertTrue(target.is_active, "HR Administrator must not be able to change is_active")
        self.assertEqual(target.surname, "Changed", "HR Administrator should still be able to edit ordinary fields")

    def test_system_administrator_can_change_employee_id_and_active(self):
        self.client.login(username="sysadmin1", password="testpass123")
        target = self.rank_and_file
        response = self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": "EMP-004-NEW",
                # is_active omitted -> False for a checkbox
                "surname": target.surname,  # should NOT change since sysadmin isn't HR admin
                "first_name": target.first_name,
                "employment_status": target.employment_status,
                "reason": "Correcting employee ID per IT ticket #123",
            },
        )
        target.refresh_from_db()
        self.assertEqual(target.employee_id, "EMP-004-NEW")
        self.assertFalse(target.is_active)

    def test_system_administrator_cannot_change_hr_fields(self):
        self.client.login(username="sysadmin1", password="testpass123")
        target = self.rank_and_file
        old_surname = target.surname
        self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": target.employee_id,
                "is_active": "on",
                "surname": "ShouldNotChange",
                "first_name": target.first_name,
                "employment_status": target.employment_status,
                "reason": "Only touching account status",
            },
        )
        target.refresh_from_db()
        self.assertEqual(target.surname, old_surname, "System Administrator must not be able to edit HR fields")

    # --- Decision 2: HR Administrator's own record is read-only ---
    def test_hr_administrator_cannot_edit_own_record(self):
        self.client.login(username="hradmin_a", password="testpass123")
        old_surname = self.hr_admin_a.surname
        response = self.client.post(
            reverse("employees:employee_edit", args=[self.hr_admin_a.pk]),
            {
                "employee_id": self.hr_admin_a.employee_id,
                "surname": "SelfEdited",
                "first_name": self.hr_admin_a.first_name,
                "employment_status": self.hr_admin_a.employment_status,
                "reason": "Trying to edit myself",
            },
        )
        self.hr_admin_a.refresh_from_db()
        self.assertEqual(self.hr_admin_a.surname, old_surname)
        # Should be redirected away from the edit screen entirely.
        self.assertRedirects(response, reverse("employees:employee_detail", args=[self.hr_admin_a.pk]))

    def test_another_hr_administrator_can_edit_it(self):
        self.client.login(username="hradmin_b", password="testpass123")
        response = self.client.post(
            reverse("employees:employee_edit", args=[self.hr_admin_a.pk]),
            {
                "employee_id": self.hr_admin_a.employee_id,
                "surname": "EditedByColleague",
                "first_name": self.hr_admin_a.first_name,
                "employment_status": self.hr_admin_a.employment_status,
                "reason": "Correcting surname per updated PDS",
            },
        )
        self.hr_admin_a.refresh_from_db()
        self.assertEqual(self.hr_admin_a.surname, "EditedByColleague")

    def test_non_hr_non_sysadmin_cannot_open_edit_screen(self):
        self.client.login(username="nurse1", password="testpass123")
        response = self.client.get(reverse("employees:employee_edit", args=[self.rank_and_file.pk]))
        self.assertEqual(response.status_code, 403)

    # --- Decision 3: audit trail with before/after values + required reason ---
    def test_edit_without_reason_is_rejected(self):
        self.client.login(username="hradmin_a", password="testpass123")
        target = self.rank_and_file
        response = self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": target.employee_id,
                "surname": "NoReasonGiven",
                "first_name": target.first_name,
                "employment_status": target.employment_status,
                "reason": "",
            },
        )
        target.refresh_from_db()
        self.assertEqual(target.surname, "Nurse1")  # unchanged (title-cased from setUp)
        self.assertContains(response, "This field is required")

    def test_edit_logs_before_after_and_reason(self):
        self.client.login(username="hradmin_a", password="testpass123")
        target = self.rank_and_file
        old_position = target.position
        self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": target.employee_id,
                "surname": target.surname,
                "first_name": target.first_name,
                "position": "Staff Nurse II",
                "employment_status": target.employment_status,
                "reason": "Promotion effective this quarter",
            },
        )
        entry = EmployeeEditHistory.objects.get(employee=target, field_name="position")
        self.assertEqual(entry.old_value, old_position or "")
        self.assertEqual(entry.new_value, "Staff Nurse II")
        self.assertEqual(entry.reason, "Promotion effective this quarter")
        self.assertEqual(entry.changed_by, self.hr_admin_a.user)

    def test_no_history_row_for_unchanged_fields(self):
        self.client.login(username="hradmin_a", password="testpass123")
        target = self.rank_and_file
        self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": target.employee_id,
                "surname": target.surname,
                "first_name": target.first_name,
                "position": "Same Position",
                "employment_status": target.employment_status,
                "reason": "first save to set a baseline",
            },
        )
        self.assertEqual(EmployeeEditHistory.objects.filter(employee=target).count(), 1)
        # Re-saving identical data should not create a spurious history row.
        self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": target.employee_id,
                "surname": target.surname,
                "first_name": target.first_name,
                "position": "Same Position",
                "employment_status": target.employment_status,
                "reason": "",
            },
        )
        self.assertEqual(EmployeeEditHistory.objects.filter(employee=target).count(), 1)
