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
