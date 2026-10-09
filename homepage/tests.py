"""Batch 4: homepage, KPI cards and their visibility rules (owner decisions 2026-10-09)."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment
from cto.models import CTOUsageApplication
from employees.models import Employee
from leave.models import LeaveApplication, LeaveType
from official_requests.models import OfficialRequest
from orgstructure.models import Section

from . import kpis

TODAY = timezone.localdate()


def emp(username, section=None, role=None, status="REGULAR", **ra):
    user = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(), first_name="T",
                                employment_status=status)
    if section:
        e.sections.add(section)
    if role:
        RoleAssignment.objects.create(employee=e, role=role,
                                      section=section if role == RoleAssignment.SUPERVISOR else None, **ra)
    return e


def leave(e, code, status, start=TODAY, end=TODAY):
    return LeaveApplication.objects.create(employee=e, leave_type=LeaveType.objects.get(code=code), start_date=start,
                                           end_date=end, number_of_days=1, status=status)


def cto(e, status, start=TODAY, end=TODAY):
    return CTOUsageApplication.objects.create(employee=e, start_date=start, end_date=end, number_of_days=1,
                                              status=status)


class HomepageTests(TestCase):
    def setUp(self):
        self.lab = Section.objects.get(name="Laboratory Section")
        self.pharm = Section.objects.get(name="Pharmacy Section")
        self.hr = emp("hrp", role=RoleAssignment.HR_PROCESSOR)
        self.ao = emp("aoo", role=RoleAssignment.ADMINISTRATIVE_OFFICER)
        self.sup = emp("labsup", self.lab, RoleAssignment.SUPERVISOR)
        self.staff = emp("labstaff", self.lab)
        self.cosp = emp("labcosp", self.lab, status="COSP")
        self.other = emp("pharmstaff", self.pharm)

    def page(self, who, name="homepage:home"):
        self.client.force_login(who.user)
        return self.client.get(reverse(name))

    # -- absent today ----------------------------------------------------------

    def test_absent_counts_recorded_regular_approved_cosp_and_cto_once_per_person(self):
        leave(self.staff, "VL", "RECORDED")
        cto(self.staff, "APPROVED")  # same person, same day: counted once
        leave(self.cosp, "COSP_LEAVE", "APPROVED")
        cto(self.other, "APPROVED", TODAY - timedelta(days=1), TODAY + timedelta(days=1))
        OfficialRequest.objects.create(employee=self.hr, request_type=OfficialRequest.TRAVEL, purpose="x",
                                       destination="PPC", start_date=TODAY, end_date=TODAY, status="APPROVED")
        leave(self.ao, "VL", "SUBMITTED")   # pending: not absent
        leave(self.sup, "VL", "RECORDED", TODAY + timedelta(days=1), TODAY + timedelta(days=2))  # not today
        rows = kpis.absent_today()
        self.assertEqual(sorted(r["employee"].pk for r in rows), sorted([self.staff.pk, self.cosp.pk, self.other.pk]))
        self.assertEqual(next(r for r in rows if r["employee"] == self.staff)["what"], ["Leave", "CTO"])
        self.assertEqual(len(kpis.pending_leave_cto()), 1)

    def test_pending_becomes_absent_on_next_load(self):
        app = leave(self.staff, "VL", "SUBMITTED")
        self.assertEqual(self.page(self.hr).context["absent_count"], 0)
        app.status = "RECORDED"
        app.save()
        self.assertEqual(self.page(self.hr).context["absent_count"], 1)
        self.assertEqual(self.page(self.hr).context["pending_lc_count"], 0)

    def test_employee_sees_number_only(self):
        leave(self.other, "SL", "RECORDED")
        page = self.page(self.staff)
        self.assertEqual(page.context["absent_count"], 1)
        self.assertNotContains(page, reverse("homepage:absent_list"))
        self.assertEqual(self.page(self.staff, "homepage:absent_list").status_code, 403)
        self.assertEqual(self.page(self.staff, "homepage:pending_leave_list").status_code, 403)

    def test_supervisor_sees_own_section_only_and_no_leave_type(self):
        leave(self.staff, "SL", "RECORDED")
        leave(self.other, "SL", "RECORDED")
        page = self.page(self.sup, "homepage:absent_list").content.decode()
        self.assertIn("Labstaff", page)
        self.assertNotIn("Pharmstaff", page)
        self.assertNotIn("Sick Leave", page)
        self.assertEqual(self.page(self.sup).context["absent_count"], 1)

    def test_only_hr_sees_the_specific_leave_type(self):
        leave(self.staff, "SL", "RECORDED")
        self.assertIn("Sick Leave", self.page(self.hr, "homepage:absent_list").content.decode())
        ao_page = self.page(self.ao, "homepage:absent_list").content.decode()
        self.assertIn("Labstaff", ao_page)
        self.assertNotIn("Sick Leave", ao_page)

    # -- other cards ---------------------------------------------------------------

    def test_headcount_excludes_archived_and_inactive(self):
        base = kpis.headcount()
        Employee.objects.filter(pk=self.other.pk).update(archived_at=timezone.now(), is_active=False)
        Employee.objects.filter(pk=self.cosp.pk).update(is_active=False)
        self.assertEqual(kpis.headcount(), base - 2)

    def test_pending_applications_scope(self):
        leave(self.staff, "VL", "SUBMITTED")
        leave(self.other, "VL", "SUBMITTED")
        OfficialRequest.objects.create(employee=self.other, request_type=OfficialRequest.OFFICIAL_TIME, purpose="x",
                                       start_date=TODAY, end_date=TODAY)
        self.assertEqual(self.page(self.hr).context["pending_apps_total"], 3)
        self.assertEqual(self.page(self.sup).context["pending_apps_total"], 1)   # own section
        self.assertEqual(self.page(self.staff).context["pending_apps_total"], 1)  # own request only
        self.assertEqual(self.page(self.cosp).context["pending_apps_total"], 0)
        mine = self.page(self.staff, "homepage:pending_applications_list").content.decode()
        self.assertNotIn("Pharmstaff", mine)

    def test_waiting_for_my_action_uses_the_queues(self):
        leave(self.staff, "VL", "SUBMITTED")  # Regular leave: HR records it
        waiting = dict((label, n) for label, n, _ in self.page(self.hr).context["waiting"])
        self.assertEqual(waiting.get("Leave"), 1)
        self.assertEqual(self.page(self.staff).context["waiting_total"], 0)

    # -- landing, header, watermark -----------------------------------------------------

    def test_everyone_lands_on_home_with_greeting_and_date(self):
        self.client.force_login(self.staff.user)
        page = self.client.get("/", follow=True)
        self.assertEqual(page.request["PATH_INFO"], "/hris/home/")
        self.assertContains(page, "Good ")
        self.assertContains(page, TODAY.strftime("%Y"))

    def test_watermark_on_homepage_only(self):
        home = self.page(self.staff).content.decode()
        self.assertIn("home-watermark", home)
        self.assertIn("home-watermark.jpg", home)
        for name in ("leave:leave_apply", "notifications:notification_list"):
            self.assertNotIn("watermark", self.page(self.staff, name).content.decode())
        self.client.logout()
        self.assertNotIn("watermark", self.client.get("/hris/login/").content.decode())

    @override_settings(HOME_WATERMARK_IMAGE="")
    def test_no_image_set_means_no_watermark(self):
        page = self.page(self.staff)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("home-watermark", page.content.decode())

    @override_settings(HOME_WATERMARK_IMAGE="images/does-not-exist.png")
    def test_missing_image_file_means_no_watermark(self):
        self.assertNotIn("home-watermark", self.page(self.staff).content.decode())
