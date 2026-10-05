from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from employees.models import Employee

from .models import RoleAssignment

PASSWORD = "testpass123"


def make_employee(username, employee_id, *roles):
    user = User.objects.create_user(username=username, password=PASSWORD)
    employee = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test"
    )
    for role in roles:
        RoleAssignment.objects.create(employee=employee, role=role)
    return employee


class RoleAssignmentTests(TestCase):
    def test_employee_can_hold_several_roles_at_once(self):
        emp = make_employee("multi", "E-1", RoleAssignment.SUPERVISOR, RoleAssignment.HR_PROCESSOR)
        self.assertTrue(emp.has_role(RoleAssignment.SUPERVISOR))
        self.assertTrue(emp.has_role(RoleAssignment.HR_PROCESSOR))
        self.assertFalse(emp.has_role(RoleAssignment.CHIEF_OF_HOSPITAL))

    def test_inactive_assignment_does_not_count(self):
        emp = make_employee("ended", "E-2", RoleAssignment.SUPERVISOR)
        emp.role_assignments.update(is_active=False)
        self.assertFalse(emp.has_role(RoleAssignment.SUPERVISOR))

    def test_oic_is_only_for_supervisor_ao_coh(self):
        delegator = make_employee("boss", "E-3", RoleAssignment.SUPERVISOR)
        target = make_employee("deputy", "E-4")
        for role in (
            RoleAssignment.SUPERVISOR,
            RoleAssignment.ADMINISTRATIVE_OFFICER,
            RoleAssignment.CHIEF_OF_HOSPITAL,
        ):
            RoleAssignment(employee=target, role=role, is_oic=True, delegated_by=delegator).clean()
        for role in (
            RoleAssignment.EMPLOYEE,
            RoleAssignment.HR_PROCESSOR,
            RoleAssignment.HR_ADMINISTRATOR,
            RoleAssignment.SYSTEM_ADMINISTRATOR,
        ):
            with self.assertRaises(ValidationError):
                RoleAssignment(employee=target, role=role, is_oic=True, delegated_by=delegator).clean()

    def test_oic_must_record_who_delegated(self):
        target = make_employee("deputy2", "E-5")
        with self.assertRaises(ValidationError):
            RoleAssignment(employee=target, role=RoleAssignment.SUPERVISOR, is_oic=True).clean()

    def test_permanent_assignment_needs_no_delegator(self):
        target = make_employee("perm", "E-6")
        RoleAssignment(employee=target, role=RoleAssignment.SUPERVISOR).clean()


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.client = Client()
        make_employee("nurse", "E-10", RoleAssignment.EMPLOYEE)

    def login(self, username="nurse", password=PASSWORD, **extra):
        return self.client.post(reverse("login"), {"username": username, "password": password, **extra})

    def test_site_root_goes_to_login_page(self):
        response = self.client.get("/")
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_login_success_lands_on_notifications(self):
        response = self.login()
        self.assertRedirects(response, reverse("notifications:notification_list"))

    def test_wrong_password_and_unknown_user_get_the_same_message(self):
        wrong = self.login(password="nope")
        unknown = self.login(username="ghost")
        for response in (wrong, unknown):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Invalid username or password.")

    def test_deactivated_account_cannot_log_in(self):
        User.objects.filter(username="nurse").update(is_active=False)
        self.assertEqual(self.login().status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_without_remember_me_session_ends_with_browser(self):
        self.login()
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_remember_me_keeps_session_for_two_weeks(self):
        self.login(remember_me="on")
        self.assertEqual(self.client.session.get_expiry_age(), 60 * 60 * 24 * 14)

    def test_logout_rejects_get_and_works_with_post(self):
        self.login()
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)  # GET must not log anyone out
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_topbar_logout_is_a_post_form(self):
        self.login()
        page = self.client.get(reverse("notifications:notification_list"))
        self.assertContains(page, 'method="post" action="/logout/"')


class SidebarByRoleTests(TestCase):
    """What each role sees in the sidebar (CLAUDE.md Section 3)."""

    def sidebar_for(self, role, n):
        make_employee(f"user{n}", f"E-{n}", role)
        client = Client()
        client.login(username=f"user{n}", password=PASSWORD)
        return client.get(reverse("notifications:notification_list")).content.decode()

    def test_plain_employee_sees_requests_but_no_approvals_or_management_links(self):
        html = self.sidebar_for(RoleAssignment.EMPLOYEE, 20)
        self.assertIn("My Requests", html)
        for hidden in ("Approvals", "Dashboard", "Audit Log", ">Employees<"):
            self.assertNotIn(hidden, html)

    def test_supervisor_sees_approvals_but_not_management_links(self):
        html = self.sidebar_for(RoleAssignment.SUPERVISOR, 21)
        self.assertIn("Approvals", html)
        self.assertNotIn("Dashboard", html)
        self.assertNotIn("Audit Log", html)

    def test_approval_chain_roles_see_approvals(self):
        for n, role in enumerate(
            [
                RoleAssignment.HR_PROCESSOR,
                RoleAssignment.HR_ADMINISTRATOR,
                RoleAssignment.ADMINISTRATIVE_OFFICER,
                RoleAssignment.CHIEF_OF_HOSPITAL,
            ],
            start=30,
        ):
            self.assertIn("Approvals", self.sidebar_for(role, n), role)

    def test_system_administrator_sees_no_approval_queues(self):
        html = self.sidebar_for(RoleAssignment.SYSTEM_ADMINISTRATOR, 40)
        self.assertNotIn("Approvals", html)
        self.assertIn("Dashboard", html)
        self.assertIn("Audit Log", html)

    def test_hr_ao_coh_see_dashboard_and_audit_log(self):
        for n, role in enumerate(
            [
                RoleAssignment.HR_ADMINISTRATOR,
                RoleAssignment.ADMINISTRATIVE_OFFICER,
                RoleAssignment.CHIEF_OF_HOSPITAL,
            ],
            start=50,
        ):
            html = self.sidebar_for(role, n)
            self.assertIn("Dashboard", html, role)
            self.assertIn("Audit Log", html, role)
