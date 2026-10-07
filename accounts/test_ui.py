"""Batch 3 screen polish: the shared files are loaded and wired in."""

from django.contrib.auth.models import User
from django.contrib.messages import get_messages  # noqa: F401
from django.test import TestCase
from django.urls import reverse

from employees.models import Employee

PASSWORD = "testpass123"


class SharedUiTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("uiuser", password=PASSWORD)
        Employee.objects.create(user=user, employee_id="UI-1", surname="Ui", first_name="Test")
        self.client.login(username="uiuser", password=PASSWORD)

    def test_pages_load_one_shared_stylesheet_and_script(self):
        html = self.client.get(reverse("notifications:notification_list")).content.decode()
        self.assertIn("css/animations.css", html)
        self.assertEqual(html.count("js/ui.js"), 1)

    def test_django_messages_become_toasts(self):
        r = self.client.post(reverse("password_change"), {
            "old_password": PASSWORD, "new_password1": "N3w-Passw0rd!x", "new_password2": "N3w-Passw0rd!x"}, follow=True)
        html = r.content.decode()
        self.assertIn('data-toast="success"', html)
        self.assertIn("Your password has been changed.", html)


class LoginEntranceTests(TestCase):
    def test_entrance_plays_on_page_load_but_not_after_a_failed_login(self):
        self.assertContains(self.client.get("/hris/login/"), 'class="login-card entrance"')
        failed = self.client.post("/hris/login/", {"username": "nobody", "password": "x"})
        self.assertContains(failed, 'class="login-card"')
        self.assertNotContains(failed, "login-card entrance")


class AccordionTests(TestCase):
    def setUp(self):
        from accounts.models import RoleAssignment

        user = User.objects.create_user("acc", password=PASSWORD)
        self.emp = Employee.objects.create(user=user, employee_id="ACC-1", surname="Acc", first_name="T")
        RoleAssignment.objects.create(employee=self.emp, role=RoleAssignment.HR_ADMINISTRATOR)
        other = Employee.objects.create(employee_id="ACC-2", surname="Other", first_name="T")
        self.other = other
        self.client.force_login(user)

    def test_profile_first_section_open_others_closed_with_aria(self):
        html = self.client.get(reverse("employees:employee_detail", args=[self.other.pk])).content.decode()
        self.assertEqual(html.count('class="card accordion"'), 3)
        self.assertIn('aria-controls="acc-details"', html)
        self.assertIn('id="acc-details" role="region"', html)
        self.assertEqual(html.count('aria-expanded="true"'), 1)
        self.assertEqual(html.count('aria-expanded="false"'), 2)

    def test_attendance_corrections_open_when_one_was_returned(self):
        from attendance.models import AttendanceCorrectionRequest

        url = reverse("attendance:my_attendance")
        self.assertIn('aria-expanded="false"', self.client.get(url).content.decode())
        AttendanceCorrectionRequest.objects.create(correction_type="FORMAL", employee=self.emp, validator="HR",
                                                  status="RETURNED", filed_by=self.emp.user)
        html = self.client.get(url).content.decode()
        self.assertNotIn('aria-expanded="false"', html)
