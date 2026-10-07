"""Archive / Undo / Restore of employee records (Batch 3 Item 5)."""

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from auditlog.aggregation import audit_rows

from .models import Employee, EmployeeEditHistory

PW = "testpass123"


def emp(username, eid, role=None, **extra):
    user = User.objects.create_user(username, password=PW)
    e = Employee.objects.create(user=user, employee_id=eid, surname=username.title(), first_name="T", **extra)
    if role:
        RoleAssignment.objects.create(employee=e, role=role)
    return e


class ArchiveTests(TestCase):
    def setUp(self):
        self.hradmin = emp("hra", "A-1", RoleAssignment.HR_ADMINISTRATOR)
        self.proc = emp("proc", "A-2", RoleAssignment.HR_PROCESSOR)
        self.staff = emp("staff", "A-3", position="Nurse I")
        self.client.force_login(self.hradmin.user)

    def archive(self, who=None, target=None, reason="Duplicate record"):
        if who:
            self.client.force_login(who.user)
        return self.client.post(reverse("employees:employee_archive", args=[(target or self.staff).pk]),
                                {"reason": reason})

    def test_archive_hides_record_blocks_login_and_offers_undo(self):
        r = self.archive()
        self.assertRedirects(r, reverse("employees:employee_list"), fetch_redirect_response=False)
        self.staff.refresh_from_db()
        self.assertIsNotNone(self.staff.archived_at)
        self.assertFalse(self.staff.is_active)
        self.assertEqual(self.staff.archive_reason, "Duplicate record")
        page = self.client.get(reverse("employees:employee_list")).content.decode()
        self.assertIn("data-snackbar", page)            # Undo offered once
        self.assertNotIn(f'href="/employees/{self.staff.pk}/"', page)  # hidden from the list
        self.assertNotIn("data-toast", page)             # no double message
        again = self.client.get(reverse("employees:employee_list")).content.decode()
        self.assertNotIn("data-snackbar", again)
        self.assertIn("Staff", self.client.get(reverse("employees:employee_list") + "?archived=1").content.decode())
        self.assertFalse(Client().login(username="staff", password=PW))  # login blocked
        self.assertFalse(Client().login(username="A-3", password=PW))    # also by Employee ID

    def test_open_session_of_archived_person_ends(self):
        c = Client()
        c.login(username="staff", password=PW)
        self.archive()
        r = c.get(reverse("notifications:notification_list"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/hris/login/", r.url)

    def test_undo_restores_exactly_and_both_steps_are_audited(self):
        self.staff.is_active = False
        self.staff.save()   # was already Inactive before archiving
        self.archive()
        self.client.post(reverse("employees:employee_restore", args=[self.staff.pk]), {"undo": "1"})
        self.staff.refresh_from_db()
        self.assertIsNone(self.staff.archived_at)
        self.assertFalse(self.staff.is_active)          # back to exactly what it was
        self.assertEqual(self.staff.position, "Nurse I")
        rows = EmployeeEditHistory.objects.filter(employee=self.staff, field_name="archived").order_by("pk")
        self.assertEqual([(r.new_value, r.changed_by_id) for r in rows],
                         [("Archived", self.hradmin.user.pk), ("Not archived", self.hradmin.user.pk)])
        self.assertIn("Undo", rows[1].reason)
        actions = [r["action"] for r in audit_rows(employee_id=self.staff.pk)]
        self.assertEqual(actions.count("Edited Archive status"), 2)

    def test_restore_later_from_the_record_page(self):
        self.archive()
        self.client.post(reverse("employees:employee_restore", args=[self.staff.pk]))
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.assertTrue(Client().login(username="staff", password=PW))

    def test_rules_hr_admin_only_not_own_record_reason_required(self):
        self.assertEqual(self.archive(who=self.proc).status_code, 403)
        self.assertEqual(self.archive(who=self.hradmin, target=self.hradmin).status_code, 403)
        self.archive(who=self.hradmin, reason="  ")
        self.staff.refresh_from_db()
        self.assertIsNone(self.staff.archived_at)

    def test_record_page_shows_archive_button_with_confirmation(self):
        page = self.client.get(reverse("employees:employee_detail", args=[self.staff.pk])).content.decode()
        self.assertIn("data-confirm-reason", page)
        self.assertIn("Archive record", page)
        own = self.client.get(reverse("employees:employee_detail", args=[self.hradmin.pk])).content.decode()
        self.assertNotIn("Archive record", own)
