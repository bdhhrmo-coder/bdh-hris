from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from cto.models import CTOCreditEntry
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

    def test_viewing_marks_unread_as_read(self):
        n = Notification.objects.create(recipient=self.employee, message="Unread")
        self.assertFalse(n.is_read)
        self.client.login(username="listview_emp", password="testpass123")
        self.client.get(reverse("notifications:notification_list"))
        n.refresh_from_db()
        self.assertTrue(n.is_read)

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
    def test_credit_entry_notifies_employee(self):
        hr = make_employee("ctonotif_hr", "EMP-CN-1", role=RoleAssignment.HR_PROCESSOR)
        employee = make_employee("ctonotif_emp", "EMP-CN-2")
        client = Client()
        client.login(username="ctonotif_hr", password="testpass123")
        client.post(reverse("cto:credit_entry_create"), {
            "employee": employee.pk,
            "work_date": "2026-03-05",
            "duty_type": CTOCreditEntry.DUTY_REGULAR,
            "hours_worked": "8.00",
            "is_restday_or_holiday": False,
            "notes": "",
        })
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
