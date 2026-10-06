from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from orgstructure.models import Section

from .models import EducationHistory, Employee, EmployeeEditHistory, EmployeeProfileEditRequest

# The Employee edit screen also carries an inline Education History formset.
# Django's formsets require their management-form fields even when there's
# nothing to submit for them, so every employee_edit POST in these tests
# merges this in.
EDU_FORMSET_EMPTY = {
    "education_history-TOTAL_FORMS": "0",
    "education_history-INITIAL_FORMS": "0",
    "education_history-MIN_NUM_FORMS": "0",
    "education_history-MAX_NUM_FORMS": "1000",
}


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
        self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": "HACKED-999",
                "is_active": "",  # attempt to deactivate
                "surname": "Changed",
                "first_name": target.first_name,
                "employment_status": target.employment_status,
                "reason": "Testing lockdown of restricted fields",
                **EDU_FORMSET_EMPTY,
            },
        )
        target.refresh_from_db()
        self.assertEqual(target.employee_id, old_id, "HR Administrator must not be able to change employee_id")
        self.assertTrue(target.is_active, "HR Administrator must not be able to change is_active")
        self.assertEqual(target.surname, "Changed", "HR Administrator should still be able to edit ordinary fields")

    def test_system_administrator_can_change_employee_id_and_active(self):
        self.client.login(username="sysadmin1", password="testpass123")
        target = self.rank_and_file
        self.client.post(
            reverse("employees:employee_edit", args=[target.pk]),
            {
                "employee_id": "EMP-004-NEW",
                # is_active omitted -> False for a checkbox
                "surname": target.surname,  # should NOT change since sysadmin isn't HR admin
                "first_name": target.first_name,
                "employment_status": target.employment_status,
                "reason": "Correcting employee ID per IT ticket #123",
                **EDU_FORMSET_EMPTY,
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
                **EDU_FORMSET_EMPTY,
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
                **EDU_FORMSET_EMPTY,
            },
        )
        self.hr_admin_a.refresh_from_db()
        self.assertEqual(self.hr_admin_a.surname, old_surname)
        # Should be redirected away from the edit screen entirely.
        self.assertRedirects(response, reverse("employees:employee_detail", args=[self.hr_admin_a.pk]))

    def test_another_hr_administrator_can_edit_it(self):
        self.client.login(username="hradmin_b", password="testpass123")
        self.client.post(
            reverse("employees:employee_edit", args=[self.hr_admin_a.pk]),
            {
                "employee_id": self.hr_admin_a.employee_id,
                "surname": "EditedByColleague",
                "first_name": self.hr_admin_a.first_name,
                "employment_status": self.hr_admin_a.employment_status,
                "reason": "Correcting surname per updated PDS",
                **EDU_FORMSET_EMPTY,
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
                **EDU_FORMSET_EMPTY,
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
                **EDU_FORMSET_EMPTY,
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
                **EDU_FORMSET_EMPTY,
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
                **EDU_FORMSET_EMPTY,
            },
        )
        self.assertEqual(EmployeeEditHistory.objects.filter(employee=target).count(), 1)


class Phase2Tests(TestCase):
    """Employee list/create access control, self-view-only scoping, and
    education history editing (added when Phase 2 was completed)."""

    def setUp(self):
        self.client = Client()
        self.hr_admin = make_employee("hradmin", "EMP-101", role=RoleAssignment.HR_ADMINISTRATOR)
        self.sysadmin = make_employee("sysadmin", "EMP-102", role=RoleAssignment.SYSTEM_ADMINISTRATOR)
        self.nurse = make_employee("nurse2", "EMP-103", role=RoleAssignment.EMPLOYEE)
        self.other_nurse = make_employee("nurse3", "EMP-104", role=RoleAssignment.EMPLOYEE)

    def test_org_structure_seeded(self):
        self.assertTrue(Section.objects.filter(name="Nursing Service Section").exists())
        nursing = Section.objects.get(name="Nursing Service Section")
        self.assertEqual(nursing.units.count(), 6)
        self.assertTrue(Section.objects.filter(name="Dietary Section").exists())

    def test_employee_list_requires_hr_or_sysadmin(self):
        self.client.login(username="nurse2", password="testpass123")
        response = self.client.get(reverse("employees:employee_list"))
        self.assertEqual(response.status_code, 403)

        self.client.login(username="hradmin", password="testpass123")
        response = self.client.get(reverse("employees:employee_list"))
        self.assertEqual(response.status_code, 200)

    def test_ordinary_employee_can_view_own_record_only(self):
        self.client.login(username="nurse2", password="testpass123")
        own = self.client.get(reverse("employees:employee_detail", args=[self.nurse.pk]))
        self.assertEqual(own.status_code, 200)

        someone_elses = self.client.get(reverse("employees:employee_detail", args=[self.other_nurse.pk]))
        self.assertEqual(someone_elses.status_code, 403)

    def test_hr_admin_and_sysadmin_can_view_anyone(self):
        self.client.login(username="hradmin", password="testpass123")
        response = self.client.get(reverse("employees:employee_detail", args=[self.nurse.pk]))
        self.assertEqual(response.status_code, 200)

        self.client.login(username="sysadmin", password="testpass123")
        response = self.client.get(reverse("employees:employee_detail", args=[self.nurse.pk]))
        self.assertEqual(response.status_code, 200)

    def test_only_sysadmin_can_create_employee(self):
        self.client.login(username="hradmin", password="testpass123")
        response = self.client.post(
            reverse("employees:employee_create"),
            {"employee_id": "EMP-999", "surname": "New", "first_name": "Hire", "is_active": "on"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Employee.objects.filter(employee_id="EMP-999").exists())

        self.client.login(username="sysadmin", password="testpass123")
        response = self.client.post(
            reverse("employees:employee_create"),
            {"employee_id": "EMP-999", "surname": "New", "first_name": "Hire", "is_active": "on"},
        )
        self.assertTrue(Employee.objects.filter(employee_id="EMP-999").exists())

    def test_hr_admin_can_add_education_history(self):
        self.client.login(username="hradmin", password="testpass123")
        self.client.post(
            reverse("employees:employee_edit", args=[self.nurse.pk]),
            {
                "employee_id": self.nurse.employee_id,
                "surname": self.nurse.surname,
                "first_name": self.nurse.first_name,
                "employment_status": self.nurse.employment_status,
                "reason": "Adding education record from submitted PDS",
                "education_history-TOTAL_FORMS": "1",
                "education_history-INITIAL_FORMS": "0",
                "education_history-MIN_NUM_FORMS": "0",
                "education_history-MAX_NUM_FORMS": "1000",
                "education_history-0-education_level": "COLLEGE",  # fixed levels since 2026-10-06
                "education_history-0-school": "Palawan State University",
                "education_history-0-degree_course": "BS Nursing",
                "education_history-0-units_earned": "",
            },
        )
        self.assertTrue(
            EducationHistory.objects.filter(employee=self.nurse, school="Palawan State University").exists()
        )

    def test_sysadmin_cannot_add_education_history(self):
        self.client.login(username="sysadmin", password="testpass123")
        self.client.post(
            reverse("employees:employee_edit", args=[self.nurse.pk]),
            {
                "employee_id": self.nurse.employee_id,
                "surname": self.nurse.surname,
                "first_name": self.nurse.first_name,
                "employment_status": self.nurse.employment_status,
                "reason": "Trying to sneak in an education record",
                "education_history-TOTAL_FORMS": "1",
                "education_history-INITIAL_FORMS": "0",
                "education_history-MIN_NUM_FORMS": "0",
                "education_history-MAX_NUM_FORMS": "1000",
                "education_history-0-education_level": "COLLEGE",  # fixed levels since 2026-10-06
                "education_history-0-school": "Should Not Save University",
                "education_history-0-degree_course": "",
                "education_history-0-units_earned": "",
            },
        )
        self.assertFalse(
            EducationHistory.objects.filter(employee=self.nurse, school="Should Not Save University").exists()
        )


class Phase3SelfServiceTests(TestCase):
    """Self-service profile edit requests + HR Administrator approval queue."""

    def setUp(self):
        self.client = Client()
        self.hr_admin_a = make_employee("hradmin3a", "EMP-201", role=RoleAssignment.HR_ADMINISTRATOR)
        self.hr_admin_b = make_employee("hradmin3b", "EMP-202", role=RoleAssignment.HR_ADMINISTRATOR)
        self.nurse = make_employee(
            "nurse4", "EMP-203", role=RoleAssignment.EMPLOYEE, email="old@example.com"
        )

    def test_submitting_a_change_does_not_apply_it_immediately(self):
        self.client.login(username="nurse4", password="testpass123")
        self.client.post(
            reverse("employees:my_profile_edit_request"),
            {
                "telephone_mobile": "09171234567",
                "email": "old@example.com",  # unchanged
                "residential_address": "",
                "permanent_address": "",
                "civil_status": "",
            },
        )
        self.nurse.refresh_from_db()
        self.assertEqual(self.nurse.telephone_mobile, "", "Self-service submission must not apply directly")
        self.assertTrue(
            EmployeeProfileEditRequest.objects.filter(
                employee=self.nurse, field_name="telephone_mobile", status=EmployeeProfileEditRequest.PENDING
            ).exists()
        )

    def test_can_only_request_changes_for_self(self):
        # There is no pk in the self-service URL at all — confirm a second
        # employee's submission creates a request against THEIR OWN record.
        other = make_employee("nurse5", "EMP-204", role=RoleAssignment.EMPLOYEE)
        self.client.login(username="nurse5", password="testpass123")
        self.client.post(
            reverse("employees:my_profile_edit_request"),
            {
                "telephone_mobile": "09179999999",
                "email": "",
                "residential_address": "",
                "permanent_address": "",
                "civil_status": "",
            },
        )
        self.assertTrue(EmployeeProfileEditRequest.objects.filter(employee=other).exists())
        self.assertFalse(EmployeeProfileEditRequest.objects.filter(employee=self.nurse).exists())

    def test_only_hr_administrator_sees_the_queue(self):
        self.client.login(username="nurse4", password="testpass123")
        response = self.client.get(reverse("employees:profile_edit_request_queue"))
        self.assertEqual(response.status_code, 403)

        self.client.login(username="hradmin3a", password="testpass123")
        response = self.client.get(reverse("employees:profile_edit_request_queue"))
        self.assertEqual(response.status_code, 200)

    def test_approving_applies_change_and_logs_history(self):
        req = EmployeeProfileEditRequest.objects.create(
            employee=self.nurse,
            field_name="telephone_mobile",
            old_value="",
            requested_value="09171234567",
        )
        self.client.login(username="hradmin3a", password="testpass123")
        self.client.post(
            reverse("employees:profile_edit_request_review", args=[req.pk]),
            {"decision": "approve"},
        )
        self.nurse.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(self.nurse.telephone_mobile, "09171234567")
        self.assertEqual(req.status, EmployeeProfileEditRequest.APPROVED)
        self.assertTrue(
            EmployeeEditHistory.objects.filter(
                employee=self.nurse, field_name="telephone_mobile", new_value="09171234567"
            ).exists()
        )

    def test_rejecting_does_not_apply_change(self):
        req = EmployeeProfileEditRequest.objects.create(
            employee=self.nurse,
            field_name="civil_status",
            old_value="",
            requested_value="MARRIED",
        )
        self.client.login(username="hradmin3a", password="testpass123")
        self.client.post(
            reverse("employees:profile_edit_request_review", args=[req.pk]),
            {"decision": "reject", "review_notes": "Please attach marriage certificate"},
        )
        self.nurse.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(self.nurse.civil_status, "")
        self.assertEqual(req.status, EmployeeProfileEditRequest.REJECTED)

    def test_hr_administrator_cannot_approve_own_request(self):
        req = EmployeeProfileEditRequest.objects.create(
            employee=self.hr_admin_a,
            field_name="email",
            old_value="",
            requested_value="me@example.com",
        )
        self.client.login(username="hradmin3a", password="testpass123")
        self.client.post(
            reverse("employees:profile_edit_request_review", args=[req.pk]),
            {"decision": "approve"},
        )
        req.refresh_from_db()
        self.hr_admin_a.refresh_from_db()
        self.assertEqual(req.status, EmployeeProfileEditRequest.PENDING, "Must not self-approve")
        self.assertEqual(self.hr_admin_a.email, "")

    def test_another_hr_administrator_can_approve_it(self):
        req = EmployeeProfileEditRequest.objects.create(
            employee=self.hr_admin_a,
            field_name="email",
            old_value="",
            requested_value="me@example.com",
        )
        self.client.login(username="hradmin3b", password="testpass123")
        self.client.post(
            reverse("employees:profile_edit_request_review", args=[req.pk]),
            {"decision": "approve"},
        )
        req.refresh_from_db()
        self.hr_admin_a.refresh_from_db()
        self.assertEqual(req.status, EmployeeProfileEditRequest.APPROVED)
        self.assertEqual(self.hr_admin_a.email, "me@example.com")


class AlphabeticalOrderTests(TestCase):
    """Owner request (2026-10-06): employee lists, dropdowns and reports are
    alphabetical by Surname, First Name - and stay that way as records are
    added or edited, because the database sorts on every query."""

    def setUp(self):
        self.hr = make_employee("zz_order_hr", "EMP-ORD-0", role=RoleAssignment.HR_ADMINISTRATOR)
        Employee.objects.filter(pk=self.hr.pk).update(surname="Zamora", first_name="Hr")
        for eid, surname, first, middle in [
            ("EMP-ORD-3", "Santos", "Maria", "B"), ("EMP-ORD-1", "Abad", "Jose", ""),
            ("EMP-ORD-2", "Santos", "Maria", "A"), ("EMP-ORD-4", "Santos", "Ana", ""),
            ("EMP-ORD-5", "Dela Cruz", "Juan", ""),
        ]:
            Employee.objects.create(employee_id=eid, surname=surname, first_name=first, middle_name=middle)
        self.expected = ["EMP-ORD-1", "EMP-ORD-5", "EMP-ORD-4", "EMP-ORD-2", "EMP-ORD-3", "EMP-ORD-0"]

    def ids(self, qs):
        return [e.employee_id for e in qs if e.employee_id.startswith("EMP-ORD")]

    def test_default_order_is_surname_first_middle(self):
        self.assertEqual(self.ids(Employee.objects.all()), self.expected)

    def test_new_and_renamed_records_fall_into_place(self):
        Employee.objects.create(employee_id="EMP-ORD-6", surname="Bautista", first_name="Lea")
        Employee.objects.filter(employee_id="EMP-ORD-1").update(surname="Yap")  # Abad renamed to Yap
        self.assertEqual(
            self.ids(Employee.objects.all()),
            ["EMP-ORD-6", "EMP-ORD-5", "EMP-ORD-4", "EMP-ORD-2", "EMP-ORD-3", "EMP-ORD-1", "EMP-ORD-0"],
        )

    def test_employee_list_screen_is_alphabetical(self):
        client = Client()
        client.force_login(self.hr.user)
        employees = client.get(reverse("employees:employee_list")).context["employees"]
        self.assertEqual(self.ids(employees), self.expected)

    def test_employee_dropdowns_are_alphabetical(self):
        from exchange.forms import DutyExchangeRequestForm

        form = DutyExchangeRequestForm(employee_a=self.hr)
        self.assertEqual(self.ids(form.fields["employee_b"].queryset), self.expected[:-1])

    def test_password_slips_are_alphabetical(self):
        from dataimport.template import build_password_slips

        creds = [{"name": n, "employee_id": n, "username": n, "password": "x", "position": "", "sections": ""}
                 for n in ("Santos, Ana", "Abad, Jose", "dela Cruz, Juan")]
        ws = build_password_slips(creds, "http://x/login/").active
        names = [ws.cell(row=r, column=2).value for r in range(1, ws.max_row + 1)
                 if ws.cell(row=r, column=1).value == "Name:"]
        self.assertEqual(names, ["Abad, Jose", "dela Cruz, Juan", "Santos, Ana"])
