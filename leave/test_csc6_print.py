"""Part B (owner decisions 2026-11-27): who may print CSC Form No. 6 of a
regular employee's leave. HR at any stage; the filer only their own, once it
is For Capitol approval (or the older Recorded); nobody else. Made-up data."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment as R
from orgstructure.models import Section, Unit

from .csc_form6 import may_see_salary
from .models import LeaveApplication as A, LeaveType
from .permissions import CSC6_HR_ONLY_MESSAGE, may_print_csc6
from .tests import make_employee

PENDING = (A.SUBMITTED, A.ENDORSED_BY_UNIT_HEAD, A.ENDORSED_BY_SUPERVISOR, A.PROCESSED_BY_HR, A.RECOMMENDED_BY_AO)
DONE = (A.FOR_CAPITOL_APPROVAL, A.RECORDED)
CLOSED = (A.RETURNED, A.REJECTED, A.CANCELLED)
EVERY = PENDING + DONE + CLOSED


class CscForm6PrintRightsTests(TestCase):
    def setUp(self):
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.er = Unit.objects.get(section=self.nursing, name="ER")
        self.vl = LeaveType.objects.get(code="VL")
        self.cosp_leave = LeaveType.objects.get(code="COSP_LEAVE")

        self.nurse = make_employee("b_nurse", "EMP-B1", date(2020, 1, 1))
        self.nurse.sections.add(self.nursing)
        self.nurse.units.add(self.er)
        self.chief_nurse = make_employee("b_chiefnurse", "EMP-B2", date(2015, 1, 1))
        self.chief_nurse.sections.add(self.nursing)
        R.objects.create(employee=self.chief_nurse, role=R.SUPERVISOR, section=self.nursing)
        self.unit_head = make_employee("b_unithead", "EMP-B3", date(2016, 1, 1))
        self.unit_head.sections.add(self.nursing)
        R.objects.create(employee=self.unit_head, role=R.SUPERVISOR, unit=self.er)
        self.hrp = make_employee("b_hrp", "EMP-B4", date(2018, 1, 1), role=R.HR_PROCESSOR)
        self.hra = make_employee("b_hra", "EMP-B5", date(2018, 1, 1), role=R.HR_ADMINISTRATOR)
        self.ao = make_employee("b_ao", "EMP-B6", date(2012, 1, 1), role=R.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("b_coh", "EMP-B7", date(2010, 1, 1), role=R.CHIEF_OF_HOSPITAL)
        self.sysadmin = make_employee("b_sysadmin", "EMP-B8", date(2019, 1, 1), role=R.SYSTEM_ADMINISTRATOR)

    def app(self, emp, status, leave_type=None):
        return A.objects.create(employee=emp, leave_type=leave_type or self.vl, status=status,
                                start_date=date(2026, 12, 7), end_date=date(2026, 12, 8),
                                number_of_days=Decimal("2"))

    def url(self, a):
        return reverse("leave:print_csc_form6", args=[a.pk])

    def get(self, user, a):
        self.client.login(username=user, password="testpass123")
        return self.client.get(self.url(a))

    def oic(self, who, role, delegator, **scope):
        today = date.today()
        return R.objects.create(employee=who, role=role, is_oic=True, delegated_by=delegator,
                                start_date=today - timedelta(days=1), end_date=today + timedelta(days=5), **scope)

    # --- the rule itself -------------------------------------------------
    def test_hr_processor_and_administrator_can_print_at_every_stage(self):
        for st in EVERY:
            a = self.app(self.nurse, st)
            for who in (self.hrp, self.hra):
                self.assertTrue(may_print_csc6(who, a), (who, st))

    def test_approvers_and_sysadmin_never_print_anothers_form(self):
        for st in EVERY:
            a = self.app(self.nurse, st)
            for who in (self.chief_nurse, self.unit_head, self.ao, self.coh, self.sysadmin):
                self.assertFalse(may_print_csc6(who, a), (who, st))

    def test_filer_only_after_for_capitol_approval(self):
        for st in PENDING + CLOSED:
            self.assertFalse(may_print_csc6(self.nurse, self.app(self.nurse, st)), st)
        for st in DONE:
            self.assertTrue(may_print_csc6(self.nurse, self.app(self.nurse, st)), st)

    def test_approver_prints_own_form_after_approval_only(self):
        for me in (self.chief_nurse, self.ao, self.coh, self.unit_head):
            self.assertFalse(may_print_csc6(me, self.app(me, A.PROCESSED_BY_HR)), me)
            self.assertTrue(may_print_csc6(me, self.app(me, A.FOR_CAPITOL_APPROVAL)), me)
            self.assertFalse(may_print_csc6(me, self.app(self.nurse, A.FOR_CAPITOL_APPROVAL)), me)

    def test_hr_person_own_form_follows_the_filer_rule(self):
        # Decision 3, Option A: rights follow the relationship to the form.
        self.assertFalse(may_print_csc6(self.hrp, self.app(self.hrp, A.SUBMITTED)))
        self.assertTrue(may_print_csc6(self.hrp, self.app(self.hrp, A.FOR_CAPITOL_APPROVAL)))

    def test_person_with_hr_and_approver_roles_can_print(self):
        R.objects.create(employee=self.hra, role=R.SUPERVISOR, section=self.nursing)
        self.assertTrue(may_print_csc6(self.hra, self.app(self.nurse, A.SUBMITTED)))

    def test_oic_for_ao_coh_or_unit_head_gains_no_print_rights(self):
        self.oic(self.chief_nurse, R.ADMINISTRATIVE_OFFICER, self.ao)
        self.oic(self.chief_nurse, R.CHIEF_OF_HOSPITAL, self.coh)
        other = make_employee("b_oicuh", "EMP-B9", date(2017, 1, 1))
        other.sections.add(self.nursing)
        R.objects.create(employee=other, role=R.SUPERVISOR, section=self.nursing)
        self.oic(other, R.SUPERVISOR, self.unit_head, unit=self.er)
        for st in (A.RECOMMENDED_BY_AO, A.FOR_CAPITOL_APPROVAL):
            a = self.app(self.nurse, st)
            self.assertFalse(may_print_csc6(self.chief_nurse, a), st)
            self.assertFalse(may_print_csc6(other, a), st)
            # the absent AO keeps view-only access but no print right
            self.assertFalse(may_print_csc6(self.ao, a), st)

    # --- server enforcement ----------------------------------------------
    def test_pasted_link_is_refused_with_plain_message(self):
        a = self.app(self.nurse, A.FOR_CAPITOL_APPROVAL)
        for user in ("b_chiefnurse", "b_unithead", "b_ao", "b_coh"):
            r = self.get(user, a)
            self.assertEqual(r.status_code, 403, user)
            self.assertNotEqual(r.get("Content-Type"), "application/pdf")
            self.assertIn("Only HR can print", r.content.decode(), user)
        self.assertEqual(self.get("b_sysadmin", a).status_code, 403)

    def test_hr_link_works_before_approval(self):
        a = self.app(self.nurse, A.SUBMITTED)
        self.assertEqual(self.get("b_hrp", a).status_code, 200)
        self.assertEqual(self.get("b_hra", a).status_code, 200)

    def test_filer_link_waits_then_works(self):
        self.assertEqual(self.get("b_nurse", self.app(self.nurse, A.RECOMMENDED_BY_AO)).status_code, 302)
        for st in CLOSED:
            self.assertEqual(self.get("b_nurse", self.app(self.nurse, st)).status_code, 302, st)
        self.assertEqual(self.get("b_nurse", self.app(self.nurse, A.FOR_CAPITOL_APPROVAL)).status_code, 200)

    # --- buttons ---------------------------------------------------------
    def test_button_gone_for_approvers_on_detail_page(self):
        a = self.app(self.nurse, A.FOR_CAPITOL_APPROVAL)
        for user in ("b_chiefnurse", "b_unithead", "b_ao", "b_coh"):
            self.client.login(username=user, password="testpass123")
            page = self.client.get(reverse("leave:leave_detail", args=[a.pk]))
            self.assertEqual(page.status_code, 200, user)
            self.assertNotContains(page, self.url(a))
            self.assertNotContains(page, "Print CSC Form 6")

    def test_button_gone_in_unit_head_queue(self):
        a = self.app(self.nurse, A.SUBMITTED)
        self.client.login(username="b_unithead", password="testpass123")
        page = self.client.get(reverse("leave:leave_queue"))
        self.assertContains(page, reverse("leave:leave_detail", args=[a.pk]))
        self.assertNotContains(page, self.url(a))

    def test_button_shown_for_hr(self):
        a = self.app(self.nurse, A.FOR_CAPITOL_APPROVAL)
        self.client.login(username="b_hrp", password="testpass123")
        page = self.client.get(reverse("leave:leave_detail", args=[a.pk]))
        self.assertContains(page, self.url(a))
        page = self.client.get(reverse("leave:leave_queue"))  # "for Capitol approval" list
        self.assertContains(page, self.url(a))

    # --- unchanged things ------------------------------------------------
    def test_cosp_form_unchanged_for_approvers(self):
        cosp = make_employee("b_cosp", "EMP-B10", date(2025, 1, 1), employment_status="COSP")
        cosp.sections.add(self.nursing)
        cosp.units.add(self.er)
        a = self.app(cosp, A.RECOMMENDED_BY_AO, self.cosp_leave)
        A.objects.filter(pk=a.pk).update(route=A.ROUTE_COSP)
        url = reverse("leave:print_cosp_leave_form", args=[a.pk])
        for user in ("b_ao", "b_coh", "b_chiefnurse", "b_hrp"):
            self.client.login(username=user, password="testpass123")
            self.assertEqual(self.client.get(url).status_code, 200, user)

    def test_salary_only_for_filer_and_hr(self):
        a = self.app(self.nurse, A.PROCESSED_BY_HR)
        for who in (self.nurse, self.hrp, self.hra):
            self.assertTrue(may_see_salary(who, a), who)
        for who in (self.ao, self.coh, self.chief_nurse, self.unit_head, self.sysadmin):
            self.assertFalse(may_see_salary(who, a), who)

    def test_message_text(self):
        self.assertIn("Only HR can print another employee's CSC Form No. 6.", CSC6_HR_ONLY_MESSAGE)
