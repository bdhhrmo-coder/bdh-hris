"""Batch 3 Item 9: the long-running actions are marked for the honest
'Working...' indicator, and report downloads send a file name the page can
use when it saves the download."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from employees.models import Employee


class ProgressMarkupTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("hrprog", password="x")
        emp = Employee.objects.create(user=user, employee_id="P-1", surname="Prog", first_name="T")
        RoleAssignment.objects.create(employee=emp, role=RoleAssignment.HR_ADMINISTRATOR)
        self.client.force_login(user)

    def test_imports_show_working_indicator_not_fake_percent(self):
        home = self.client.get(reverse("dataimport:home")).content.decode()
        self.assertEqual(home.count("data-working="), 2)
        self.assertNotIn("%", home.split("data-working=")[1][:60])
        self.assertIn('data-upload-text="Uploading employees"', home)  # real upload %, then Working...
        bio = self.client.get(reverse("attendance:biometric_import")).content.decode()
        self.assertIn('data-working="Importing biometric records', bio)

    def test_report_links_download_with_a_file_name(self):
        page = self.client.get(reverse("dashboard:dashboard_home")).content.decode()
        self.assertGreaterEqual(page.count("data-download="), 4)
        r = self.client.get(reverse("dashboard:leave_balance_report") + "?format=xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn("filename", r["Content-Disposition"])
