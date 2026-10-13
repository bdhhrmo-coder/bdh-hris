"""Batch 7: OIC screen, OIC dates, and the absent approver's hand-over."""

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment as R
from auditlog.aggregation import audit_rows
from employees.models import Employee
from leave.permissions import employees_with_role, is_administrative_officer, is_supervisor_of
from notifications.models import Notification
from orgstructure.models import Section


def person(username, role=None, section=None):
    user = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(), first_name="T",
                                date_hired=date(2024, 1, 1))
    if role:
        R.objects.create(employee=e, role=role, section=section)
    return e


class OICRulesTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.lab = Section.objects.get(name="Laboratory Section")
        self.pharm = Section.objects.get(name="Pharmacy Section")
        self.head = person("labhead", R.SUPERVISOR, self.lab)
        R.objects.create(employee=self.head, role=R.SUPERVISOR, section=self.pharm)
        self.other = person("otherhead", R.SUPERVISOR, self.pharm)
        self.staff = person("labstaff")
        self.staff.sections.add(self.lab)
        self.pstaff = person("pharmstaff")
        self.pstaff.sections.add(self.pharm)

    def oic(self, start, end, by=None, to=None, role=R.SUPERVISOR, section="lab"):
        return R.objects.create(employee=to or self.other, role=role, section=self.lab if section == "lab" else None,
                                is_oic=True, delegated_by=by or self.head, start_date=start, end_date=end)

    def test_oic_acts_only_between_its_dates(self):
        self.oic(self.today + timedelta(days=1), self.today + timedelta(days=3))
        self.assertFalse(is_supervisor_of(self.other, self.staff))  # not started
        self.assertTrue(is_supervisor_of(self.head, self.staff))
        R.objects.filter(is_oic=True).delete()
        self.oic(self.today - timedelta(days=5), self.today - timedelta(days=1))
        self.assertFalse(is_supervisor_of(self.other, self.staff))  # already ended
        self.assertTrue(is_supervisor_of(self.head, self.staff))

    def test_delegator_cannot_act_in_handed_over_area_only(self):
        self.oic(self.today, self.today + timedelta(days=2))
        self.assertTrue(is_supervisor_of(self.other, self.staff))
        self.assertFalse(is_supervisor_of(self.head, self.staff))  # lab handed over
        self.assertTrue(is_supervisor_of(self.head, self.pstaff))  # pharmacy kept

    def test_ao_hand_over(self):
        ao = person("theao", R.ADMINISTRATIVE_OFFICER)
        self.oic(self.today, self.today, by=ao, role=R.ADMINISTRATIVE_OFFICER, section=None)
        self.assertFalse(is_administrative_officer(ao))
        self.assertTrue(is_administrative_officer(self.other))

    def test_notification_fan_out_follows_the_same_rule(self):
        self.oic(self.today + timedelta(days=1), self.today + timedelta(days=2), to=self.other)
        ids = set(employees_with_role(R.SUPERVISOR).values_list("pk", flat=True))
        self.assertIn(self.head.pk, ids)
        ao = person("ao2", R.ADMINISTRATIVE_OFFICER)
        self.oic(self.today, self.today, by=ao, role=R.ADMINISTRATIVE_OFFICER, section=None)
        ao_ids = set(employees_with_role(R.ADMINISTRATIVE_OFFICER).values_list("pk", flat=True))
        self.assertEqual(ao_ids, {self.other.pk})


class OICScreenTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.lab = Section.objects.get(name="Laboratory Section")
        self.head = person("scrhead", R.SUPERVISOR, self.lab)
        self.cand = person("scrcand", R.SUPERVISOR, Section.objects.get(name="Pharmacy Section"))
        self.plain = person("scrplain")
        self.client.force_login(self.head.user)
        self.scope = R.objects.get(employee=self.head)

    def post(self, **over):
        data = {"scope": self.scope.pk, "oic": self.cand.pk, "start_date": self.today,
                "end_date": self.today + timedelta(days=2)}
        data.update(over)
        return self.client.post(reverse("oic_home"), data)

    def test_supervisor_designates_and_oic_is_notified(self):
        r = self.post()
        self.assertRedirects(r, reverse("oic_home"))
        ra = R.objects.get(is_oic=True)
        self.assertEqual((ra.employee, ra.delegated_by, ra.section, ra.created_by),
                         (self.cand, self.head, self.lab, self.head.user))
        self.assertTrue(Notification.objects.filter(recipient=self.cand).exists())
        self.assertTrue(any(row["module"] == "OIC Designation" for row in audit_rows()))

    def test_only_supervisors_can_be_oic_and_dates_are_checked(self):
        self.post(oic=self.plain.pk)
        self.post(start_date=self.today - timedelta(days=1))
        self.post(end_date=self.today - timedelta(days=1))
        self.assertFalse(R.objects.filter(is_oic=True).exists())

    def test_no_overlapping_oic_for_same_area(self):
        self.post()
        self.post(start_date=self.today + timedelta(days=1), end_date=self.today + timedelta(days=4))
        self.assertEqual(R.objects.filter(is_oic=True).count(), 1)

    def test_employee_without_approver_role_is_refused(self):
        self.client.force_login(self.plain.user)
        self.assertEqual(self.client.get(reverse("oic_home")).status_code, 403)

    def test_delegator_can_still_open_screen_and_end_oic_early(self):
        ra = R.objects.create(employee=self.cand, role=R.SUPERVISOR, section=self.lab, is_oic=True,
                              delegated_by=self.head, start_date=self.today - timedelta(days=3),
                              end_date=self.today + timedelta(days=3))
        self.assertEqual(self.client.get(reverse("oic_home")).status_code, 200)
        self.client.post(reverse("oic_end", args=[ra.pk]))
        ra.refresh_from_db()
        self.assertEqual(ra.end_date, self.today - timedelta(days=1))
        self.assertEqual(ra.ended_by, self.head.user)
        self.assertTrue(self.head.has_role(R.SUPERVISOR))
        staff = person("scrstaff")
        staff.sections.add(self.lab)
        self.assertTrue(is_supervisor_of(self.head, staff))
        self.assertFalse(is_supervisor_of(self.cand, staff))

    def test_ending_an_upcoming_oic_cancels_it_and_only_delegator_may_end(self):
        ra = R.objects.create(employee=self.cand, role=R.SUPERVISOR, section=self.lab, is_oic=True,
                              delegated_by=self.head, start_date=self.today + timedelta(days=2),
                              end_date=self.today + timedelta(days=4))
        self.client.force_login(self.cand.user)
        self.assertEqual(self.client.post(reverse("oic_end", args=[ra.pk])).status_code, 403)
        self.client.force_login(self.head.user)
        self.client.post(reverse("oic_end", args=[ra.pk]))
        ra.refresh_from_db()
        self.assertFalse(ra.is_active)


class NewMenuLinksTests(TestCase):
    def labels(self, employee):
        self.client.force_login(employee.user)
        groups = self.client.get("/hris/home/").context["nav_groups"]
        return [i["label"] for g in groups for i in g["items"]]

    def test_each_role_sees_its_new_links(self):
        lab = Section.objects.get(name="Laboratory Section")
        emp = self.labels(person("mlemp"))
        self.assertIn("My Profile", emp)
        self.assertNotIn("Officer-in-Charge (OIC)", emp)
        sup = self.labels(person("mlsup", R.SUPERVISOR, lab))
        self.assertIn("Officer-in-Charge (OIC)", sup)
        self.assertNotIn("Import Biometric Attendance", sup)
        hrp = self.labels(person("mlhrp", R.HR_PROCESSOR))
        self.assertIn("Import Biometric Attendance", hrp)
        self.assertIn("Minor Attendance Correction", hrp)
        self.assertNotIn("Profile Changes", hrp)
        self.assertIn("Profile Changes", self.labels(person("mlhra", R.HR_ADMINISTRATOR)))

    def test_new_links_open_for_the_right_role_and_stay_closed_for_others(self):
        hrp = person("mlhrp2", R.HR_PROCESSOR)
        emp = person("mlemp2")
        for name in ("attendance:biometric_import", "attendance:minor_correction_create"):
            self.client.force_login(hrp.user)
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
            self.client.force_login(emp.user)
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.client.force_login(emp.user)
        self.assertEqual(self.client.get(reverse("employees:profile_edit_request_queue")).status_code, 403)
        self.assertEqual(self.client.get(reverse("employees:my_profile_edit_request")).status_code, 200)


class ViewOnlyDuringOICTests(TestCase):
    """Owner, 2026-10-13: the absent approver keeps VIEW-ONLY access to the
    area handed to the OIC, but cannot act on it."""

    def setUp(self):
        from leave.models import LeaveApplication, LeaveType

        self.today = timezone.localdate()
        self.lab = Section.objects.get(name="Laboratory Section")
        self.head = person("vohead", R.SUPERVISOR, self.lab)
        self.oic = person("vooic", R.SUPERVISOR, Section.objects.get(name="Pharmacy Section"))
        self.ao = person("voao", R.ADMINISTRATIVE_OFFICER)
        self.staff = person("vostaff")
        self.staff.sections.add(self.lab)
        R.objects.create(employee=self.oic, role=R.SUPERVISOR, section=self.lab, is_oic=True,
                         delegated_by=self.head, start_date=self.today, end_date=self.today)
        R.objects.create(employee=self.oic, role=R.ADMINISTRATIVE_OFFICER, is_oic=True,
                         delegated_by=self.ao, start_date=self.today, end_date=self.today)
        ltype = LeaveType.objects.filter(requires_full_routing=True).first()
        self.app = LeaveApplication.objects.create(
            employee=self.staff, leave_type=ltype, start_date=self.today, end_date=self.today, number_of_days=1)

    def test_supervisor_can_view_but_not_act(self):
        from leave.permissions import can_view_application, visible_applications_for

        self.assertTrue(can_view_application(self.head, self.app))
        self.assertNotIn(self.app, visible_applications_for(self.head))
        self.assertIn(self.app, visible_applications_for(self.oic))

    def test_ao_keeps_dashboard_and_audit_log_but_no_approvals(self):
        from auditlog.permissions import can_view_audit_log
        from dashboard.permissions import can_view_dashboard

        self.assertTrue(can_view_dashboard(self.ao))
        self.assertTrue(can_view_audit_log(self.ao))
        self.assertFalse(is_administrative_officer(self.ao))
        self.client.force_login(self.ao.user)
        self.assertEqual(self.client.get(reverse("dashboard:dashboard_home")).status_code, 200)
        groups = self.client.get("/hris/home/").context["nav_groups"]
        labels = {g["label"]: [i["label"] for i in g["items"]] for g in groups}
        self.assertIn("Dashboard", labels["Reports"])
        self.assertEqual(labels["Approvals"], ["Officer-in-Charge (OIC)"])  # only the screen to end it early
