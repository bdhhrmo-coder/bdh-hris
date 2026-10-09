from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from employees.models import Employee
from leave.models import LeaveType
from leave.permissions import employees_with_role, hr_employees, supervisors_of
from official_requests.models import OfficialRequest

from .models import Notification
from .services import notify


def make_employee(username, employee_id, role=None, **role_kwargs):
    user = User.objects.create_user(username=username, password="testpass123")
    employee = Employee.objects.create(
        user=user, employee_id=employee_id, surname=username.title(), first_name="Test",
        date_hired=date(2024, 1, 1), employment_status="REGULAR",
    )
    if role:
        RoleAssignment.objects.create(employee=employee, role=role, **role_kwargs)
    return employee


class NotifyServiceTests(TestCase):
    """Direct tests of services.notify() — the single write path every
    app's notifications.py module calls."""

    def test_creates_one_notification_per_employee(self):
        a = make_employee("svc_a", "EMP-SVC-1")
        b = make_employee("svc_b", "EMP-SVC-2")
        notify([a, b], "Hello", "/somewhere/")
        self.assertEqual(Notification.objects.filter(recipient=a).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=b).count(), 1)

    def test_accepts_single_employee_not_just_iterable(self):
        a = make_employee("svc_single", "EMP-SVC-3")
        notify(a, "Solo message")
        self.assertEqual(Notification.objects.filter(recipient=a).count(), 1)

    def test_deduplicates_repeated_employee(self):
        a = make_employee("svc_dup", "EMP-SVC-4")
        notify([a, a], "Only once")
        self.assertEqual(Notification.objects.filter(recipient=a).count(), 1)

    def test_skips_none_entries(self):
        a = make_employee("svc_none", "EMP-SVC-5")
        notify([a, None], "Still works")
        self.assertEqual(Notification.objects.count(), 1)

    def test_none_employees_argument_is_a_no_op(self):
        notify(None, "Nobody")
        self.assertEqual(Notification.objects.count(), 0)


class RoleLookupHelperTests(TestCase):
    """leave.permissions helpers added for notification fan-out."""

    def test_employees_with_role_finds_active_assignment(self):
        hr = make_employee("rolehelp_hr", "EMP-RH-1", role=RoleAssignment.HR_PROCESSOR)
        make_employee("rolehelp_plain", "EMP-RH-2")
        self.assertEqual(list(employees_with_role(RoleAssignment.HR_PROCESSOR)), [hr])

    def test_hr_employees_combines_both_hr_roles(self):
        processor = make_employee("rolehelp_proc", "EMP-RH-3", role=RoleAssignment.HR_PROCESSOR)
        admin = make_employee("rolehelp_admin", "EMP-RH-4", role=RoleAssignment.HR_ADMINISTRATOR)
        result = set(hr_employees())
        self.assertIn(processor, result)
        self.assertIn(admin, result)

    def test_supervisors_of_hospital_wide_supervisor(self):
        supervisor = make_employee("rolehelp_sup", "EMP-RH-5", role=RoleAssignment.SUPERVISOR)
        target = make_employee("rolehelp_target", "EMP-RH-6")
        self.assertEqual(supervisors_of(target), [supervisor])


class NotificationListViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.employee = make_employee("listview_emp", "EMP-LV-1")

    def test_shows_own_notifications_only(self):
        other = make_employee("listview_other", "EMP-LV-2")
        Notification.objects.create(recipient=self.employee, message="Mine")
        Notification.objects.create(recipient=other, message="Not mine")
        self.client.login(username="listview_emp", password="testpass123")
        response = self.client.get(reverse("notifications:notification_list"))
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Not mine")

    def test_viewing_the_list_no_longer_marks_everything_read(self):
        n = Notification.objects.create(recipient=self.employee, message="Unread")
        self.client.login(username="listview_emp", password="testpass123")
        page = self.client.get(reverse("notifications:notification_list"))
        self.assertContains(page, "Mark all as read")
        n.refresh_from_db()
        self.assertFalse(n.is_read)

    def test_opening_a_notification_marks_it_read_and_follows_its_link(self):
        n = Notification.objects.create(recipient=self.employee, message="Go", url="/leave/mine/")
        other = Notification.objects.create(recipient=self.employee, message="Stay unread")
        self.client.login(username="listview_emp", password="testpass123")
        r = self.client.get(reverse("notifications:open", args=[n.pk]))
        self.assertRedirects(r, "/leave/mine/", fetch_redirect_response=False)
        n.refresh_from_db(); other.refresh_from_db()
        self.assertTrue(n.is_read)
        self.assertFalse(other.is_read)

    def test_cannot_open_someone_elses_notification(self):
        stranger = make_employee("listview_stranger", "EMP-LV-3")
        n = Notification.objects.create(recipient=stranger, message="Private")
        self.client.login(username="listview_emp", password="testpass123")
        self.assertEqual(self.client.get(reverse("notifications:open", args=[n.pk])).status_code, 404)
        n.refresh_from_db()
        self.assertFalse(n.is_read)

    def test_outside_link_is_not_followed(self):
        n = Notification.objects.create(recipient=self.employee, message="Bad", url="http://evil.example/")
        self.client.login(username="listview_emp", password="testpass123")
        r = self.client.get(reverse("notifications:open", args=[n.pk]))
        self.assertRedirects(r, reverse("notifications:notification_list"), fetch_redirect_response=False)

    def test_mark_all_read_only_touches_my_notifications(self):
        other = make_employee("listview_other2", "EMP-LV-4")
        for i in range(3):
            Notification.objects.create(recipient=self.employee, message=f"m{i}")
        theirs = Notification.objects.create(recipient=other, message="theirs")
        self.client.login(username="listview_emp", password="testpass123")
        self.assertEqual(self.client.get(reverse("notifications:mark_all_read")).status_code, 405)  # POST only
        self.client.post(reverse("notifications:mark_all_read"))
        self.assertFalse(Notification.objects.filter(recipient=self.employee, is_read=False).exists())
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_unread_count_endpoint_and_bubble_label(self):
        other = make_employee("listview_other3", "EMP-LV-5")
        Notification.objects.create(recipient=other, message="not counted")
        self.client.login(username="listview_emp", password="testpass123")
        self.assertEqual(self.client.get(reverse("notifications:unread_count")).json(), {"count": 0, "label": ""})
        for i in range(3):
            Notification.objects.create(recipient=self.employee, message=f"n{i}")
        self.assertEqual(self.client.get(reverse("notifications:unread_count")).json(), {"count": 3, "label": "3"})
        for i in range(8):
            Notification.objects.create(recipient=self.employee, message=f"x{i}")
        self.assertEqual(self.client.get(reverse("notifications:unread_count")).json()["label"], "9+")
        page = self.client.get(reverse("leave:my_applications")).content.decode()
        self.assertIn('class="topbar-bell"', page)
        self.assertIn('11 unread notifications', page)

    def test_pages_load_the_animation_script(self):
        self.client.login(username="listview_emp", password="testpass123")
        page = self.client.get(reverse("notifications:notification_list")).content.decode()
        self.assertIn("js/ui.js", page)
        self.assertIn("pulseBell", page)

    def test_unread_count_requires_login(self):
        self.assertEqual(self.client.get(reverse("notifications:unread_count")).status_code, 302)

    def test_unread_count_context_processor(self):
        Notification.objects.create(recipient=self.employee, message="One")
        Notification.objects.create(recipient=self.employee, message="Two")
        self.client.login(username="listview_emp", password="testpass123")
        response = self.client.get(reverse("leave:my_applications"))
        self.assertEqual(response.context["unread_notification_count"], 2)


class LeaveNotificationIntegrationTests(TestCase):
    """Confirms leave/notifications.py is actually wired into leave/views.py
    — not just that the standalone function works in isolation."""

    def setUp(self):
        self.client = Client()
        self.supervisor = make_employee("leavenotif_sup", "EMP-LN-1", role=RoleAssignment.SUPERVISOR)
        self.applicant = make_employee("leavenotif_emp", "EMP-LN-2")
        self.leave_type = LeaveType.objects.create(
            name="Vacation Leave", code="VL-NOTIF", applicable_to=LeaveType.APPLICABLE_BOTH,
            balance_tracking=LeaveType.TRACKING_UNTRACKED, requires_full_routing=True,
        )

    def test_filing_cosp_style_application_notifies_supervisor(self):
        self.client.login(username="leavenotif_emp", password="testpass123")
        self.client.post(reverse("leave:leave_apply"), {
            "leave_type": self.leave_type.pk,
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "number_of_days": "1",
            "justification": "Personal",
        })
        self.assertTrue(
            Notification.objects.filter(recipient=self.supervisor, message__icontains="awaiting your endorsement").exists()
        )


class ExchangeNotificationIntegrationTests(TestCase):
    def test_filing_notifies_employee_b_for_consent(self):
        from datetime import date, timedelta

        a = make_employee("exnotif_a", "EMP-EN-1")
        b = make_employee("exnotif_b", "EMP-EN-2")
        # Batch 5: exchange partners must hold the same position.
        Employee.objects.filter(pk__in=[a.pk, b.pk]).update(position="Nurse II")
        far_future_a = date.today() + timedelta(days=30)
        far_future_b = date.today() + timedelta(days=31)
        client = Client()
        client.login(username="exnotif_a", password="testpass123")
        client.post(reverse("exchange:exchange_apply"), {
            "employee_b": b.pk,
            "date_a": far_future_a,
            "date_b": far_future_b,
            "reason": "Swap",
            "is_emergency": False,
        })
        self.assertTrue(
            Notification.objects.filter(recipient=b, message__icontains="consent is needed").exists()
        )


class CTONotificationIntegrationTests(TestCase):
    def test_credited_claim_notifies_employee(self):
        from unittest.mock import patch

        from cto.tests import _FakeDate, make_approved_ot, make_draft_claim, upload_all_required

        hr = make_employee("ctonotif_hr", "EMP-CN-1", role=RoleAssignment.HR_PROCESSOR)
        employee = make_employee("ctonotif_emp", "EMP-CN-2")
        ot = make_approved_ot(employee, date(2026, 3, 5), date(2026, 3, 5), "8")
        entry = make_draft_claim(ot, date(2026, 3, 5), "8", hr.user)
        upload_all_required(entry, hr.user)
        client = Client()
        client.login(username="ctonotif_hr", password="testpass123")
        with patch("cto.views.date", _FakeDate):
            client.post(reverse("cto:claim_submit", args=[entry.pk]))
        self.assertTrue(Notification.objects.filter(recipient=employee, message__icontains="credited").exists())
        self.assertFalse(Notification.objects.filter(recipient=hr).exists())


class OfficialRequestNotificationIntegrationTests(TestCase):
    """The one case worth an explicit test: Official Time skips the
    Supervisor step (§6.2), so filing one must notify HR directly, not a
    supervisor who has no authority to act on it."""

    def test_official_time_notifies_hr_not_supervisor(self):
        make_employee("ortnotif_sup", "EMP-ON-1", role=RoleAssignment.SUPERVISOR)
        hr = make_employee("ortnotif_hr", "EMP-ON-2", role=RoleAssignment.HR_PROCESSOR)
        applicant = make_employee("ortnotif_emp", "EMP-ON-3")
        client = Client()
        client.login(username="ortnotif_emp", password="testpass123")
        client.post(reverse("official_requests:request_apply"), {
            "request_type": OfficialRequest.OFFICIAL_TIME,
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "purpose": "Training",
        })
        self.assertTrue(Notification.objects.filter(recipient=hr).exists())
        self.assertFalse(
            Notification.objects.filter(message__icontains="awaiting your endorsement").exists()
        )
