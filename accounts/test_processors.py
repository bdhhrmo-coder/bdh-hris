"""Part A (owner decisions 2026-11-27): the name of whoever processed each
step, in short form; the AO and COH not named when they act personally; an
OIC shown with its label. Display only. Made-up names and data only."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment as R
from accounts.processors import HIDDEN, OIC_AO, OIC_COH, OIC_SUPERVISOR, SELF, describe, short_name
from exchange.models import DutyExchangeRequest, DutyExchangeRequestAction
from leave.models import LeaveApplication as A, LeaveApplicationAction, LeaveType
from leave.tests import make_employee
from orgstructure.models import Section, Unit

PW = "testpass123"


def person(user, eid, first, last, middle="", ext="", role=None):
    e = make_employee(user, eid, date(2018, 1, 1), role=role)
    e.first_name, e.surname, e.middle_name, e.name_extension = first, last, middle, ext
    e.save()
    return e


class ShortNameTests(TestCase):
    def test_initial_and_surname_only(self):
        e = person("p_sn1", "EMP-A1", "Juana", "Dela Paz", middle="Reyes", ext="Jr.")
        self.assertEqual(short_name(e), "J. Dela Paz")

    def test_surname_only_when_no_first_name(self):
        e = person("p_sn2", "EMP-A2", "", "Ramos")
        self.assertEqual(short_name(e), "Ramos")


class ProcessorNameTests(TestCase):
    def setUp(self):
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.er = Unit.objects.get(section=self.nursing, name="ER")
        self.vl = LeaveType.objects.get(code="VL")
        self.nurse = person("p_nurse", "EMP-A3", "Alma", "Dela Paz", middle="Cruz")
        self.nurse.sections.add(self.nursing)
        self.nurse.units.add(self.er)
        self.uh = person("p_uh", "EMP-A4", "Lorna", "Ramos", middle="Santos")
        self.uh.sections.add(self.nursing)
        R.objects.create(employee=self.uh, role=R.SUPERVISOR, unit=self.er)
        self.cn = person("p_cn", "EMP-A5", "Carmen", "Villa", ext="III")
        self.cn.sections.add(self.nursing)
        R.objects.create(employee=self.cn, role=R.SUPERVISOR, section=self.nursing)
        self.hr = person("p_hr", "EMP-A6", "Paolo", "Lim", middle="Tan", role=R.HR_ADMINISTRATOR)
        self.ao = person("p_ao", "EMP-A7", "Maria", "Bautista", middle="Lopez", role=R.ADMINISTRATIVE_OFFICER)
        self.coh = person("p_coh", "EMP-A8", "Ramon", "Aquino", middle="Diaz", role=R.CHIEF_OF_HOSPITAL)
        self.oic = person("p_oic", "EMP-A9", "Dina", "Santos", middle="Reyes")
        self.oic.sections.add(self.nursing)
        R.objects.create(employee=self.oic, role=R.SUPERVISOR, section=self.nursing)

    def oic_line(self, role, delegator, **scope):
        today = date.today()
        return R.objects.create(employee=self.oic, role=role, is_oic=True, delegated_by=delegator,
                                start_date=today - timedelta(days=1), end_date=today + timedelta(days=3), **scope)

    def app(self):
        return A.objects.create(employee=self.nurse, leave_type=self.vl, start_date=date(2026, 12, 7),
                                end_date=date(2026, 12, 8), number_of_days=Decimal("2"))

    def act(self, user, a, action, notes=""):
        self.client.login(username=user, password=PW)
        r = self.client.post(reverse("leave:leave_action", args=[a.pk]), {"action": action, "notes": notes})
        a.refresh_from_db()
        return r

    def run_chain(self, ao_user="p_ao", coh_user="p_coh"):
        a = self.app()
        self.act("p_uh", a, "endorse")
        self.act("p_cn", a, "endorse")
        self.act("p_hr", a, "record")
        self.act(ao_user, a, "review")
        self.act(coh_user, a, "recommend_capitol")
        self.assertEqual(a.status, A.FOR_CAPITOL_APPROVAL)
        return a

    def by_status(self, a):
        return {h.resulting_status: describe(h) for h in a.actions.all()}

    def test_chain_all_acting_personally(self):
        a = self.run_chain()
        names = self.by_status(a)
        self.assertEqual(names[A.ENDORSED_BY_UNIT_HEAD], "L. Ramos")
        self.assertEqual(names[A.ENDORSED_BY_SUPERVISOR], "C. Villa")
        self.assertEqual(names[A.PROCESSED_BY_HR], "P. Lim")
        self.assertEqual(names[A.RECOMMENDED_BY_AO], "")
        self.assertEqual(names[A.FOR_CAPITOL_APPROVAL], "")
        page = self.client.get(reverse("leave:leave_detail", args=[a.pk]))
        for full in ("Ramos, Lorna", "Villa, Carmen", "Lim, Paolo", "Bautista", "Aquino"):
            self.assertNotContains(page, full)
        self.assertContains(page, "L. Ramos")
        self.assertContains(page, "P. Lim")

    def test_oic_for_ao(self):
        self.oic_line(R.ADMINISTRATIVE_OFFICER, self.ao)
        a = self.run_chain(ao_user="p_oic")
        row = a.actions.get(resulting_status=A.RECOMMENDED_BY_AO)
        self.assertEqual((row.acting_capacity, row.acting_for_id), (OIC_AO, self.ao.pk))  # stored at saving
        self.assertEqual(describe(row), "D. Santos - acting as OIC for AO")

    def test_oic_for_coh(self):
        self.oic_line(R.CHIEF_OF_HOSPITAL, self.coh)
        a = self.run_chain(coh_user="p_oic")
        row = a.actions.get(resulting_status=A.FOR_CAPITOL_APPROVAL)
        self.assertEqual(row.acting_capacity, OIC_COH)
        self.assertEqual(describe(row), "D. Santos - acting as OIC for COH")

    def test_ao_as_oic_for_coh_on_cosp_leave(self):
        today = date.today()
        R.objects.create(employee=self.ao, role=R.CHIEF_OF_HOSPITAL, is_oic=True, delegated_by=self.coh,
                         start_date=today - timedelta(days=1), end_date=today + timedelta(days=3))
        cosp = person("p_cosp", "EMP-A13", "Celia", "Ramos")
        a = A.objects.create(employee=cosp, leave_type=LeaveType.objects.get(code="COSP_LEAVE"),
                             start_date=date(2026, 12, 7), end_date=date(2026, 12, 7), number_of_days=Decimal("1"))
        rec = LeaveApplicationAction.objects.create(application=a, action="recommend", acted_by=self.ao.user,
                                                    resulting_status=A.RECOMMENDED_BY_AO)
        appr = LeaveApplicationAction.objects.create(application=a, action="approve", acted_by=self.ao.user,
                                                     resulting_status=A.APPROVED)
        self.assertEqual(describe(rec), "")                                   # the AO personally
        self.assertEqual(describe(appr), "M. Bautista - acting as OIC for COH")  # the COH step, as OIC

    def test_oic_at_unit_head_step(self):
        self.oic_line(R.SUPERVISOR, self.uh, unit=self.er)
        a = self.app()
        self.act("p_oic", a, "endorse")
        row = a.actions.get(resulting_status=A.ENDORSED_BY_UNIT_HEAD)
        self.assertEqual(row.acting_capacity, OIC_SUPERVISOR)
        self.assertEqual(describe(row), "D. Santos - acting as OIC for L. Ramos")

    def test_old_rows_are_worked_out_from_role_lines(self):
        a = self.run_chain()
        LeaveApplicationAction.objects.filter(application=a).update(acting_capacity="", acting_for=None)
        names = self.by_status(a)
        self.assertEqual(names[A.ENDORSED_BY_UNIT_HEAD], "L. Ramos")
        self.assertEqual(names[A.RECOMMENDED_BY_AO], "")

    def test_old_row_without_actor_shows_nothing(self):
        a = self.app()
        row = LeaveApplicationAction(application=a, action="lapse", resulting_status=A.CANCELLED)
        self.assertEqual(describe(row), "")

    def test_capitol_return_shows_recorded_by(self):
        a = self.run_chain()
        hr2 = person("p_hr2", "EMP-A10", "Rosa", "Mendoza", role=R.HR_PROCESSOR)
        self.assertTrue(hr2)
        self.act("p_hr", a, "capitol_return", "Attach the clearance")
        row = a.actions.filter(action="capitol_return").last()
        self.assertEqual(describe(row), "Recorded by P. Lim")

    def test_stored_values_never_change_routing(self):
        a = self.run_chain()
        self.assertEqual(set(a.actions.values_list("acting_capacity", flat=True)) - {SELF, HIDDEN, ""}, set())

    def test_audit_log_unchanged(self):
        self.run_chain()
        self.client.login(username="p_hr", password=PW)
        page = self.client.get(reverse("auditlog:audit_log"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "p_uh")          # unchanged: the Audit Log shows the account as before
        self.assertNotContains(page, "L. Ramos")   # no short names there

    def test_exchange_detail_page_shows_short_names_to_parties_only(self):
        other = person("p_other", "EMP-A11", "Nora", "Garcia")
        other.sections.add(self.nursing)
        other.units.add(self.er)
        x = DutyExchangeRequest.objects.create(employee_a=self.nurse, date_a=date(2026, 12, 10),
                                               employee_b=other, date_b=date(2026, 12, 12),
                                               status=DutyExchangeRequest.ENDORSED_BY_UNIT_HEAD)
        DutyExchangeRequestAction.objects.create(request=x, action="endorse_unit", acted_by=self.uh.user,
                                                 resulting_status=DutyExchangeRequest.ENDORSED_BY_UNIT_HEAD)
        self.client.login(username="p_nurse", password=PW)
        page = self.client.get(reverse("exchange:exchange_detail", args=[x.pk]))
        self.assertContains(page, "L. Ramos")
        self.assertNotContains(page, "Ramos, Lorna")
        outsider = person("p_out", "EMP-A12", "Eli", "Torres")
        self.assertTrue(outsider)
        self.client.login(username="p_out", password=PW)
        self.assertEqual(self.client.get(reverse("exchange:exchange_detail", args=[x.pk])).status_code, 403)

    def test_my_requests_last_action_line(self):
        a = self.app()
        self.act("p_uh", a, "endorse")
        self.client.login(username="p_nurse", password=PW)
        page = self.client.get(reverse("leave:my_applications"))
        self.assertContains(page, "Last action: L. Ramos")


class PerformanceExcludedTests(TestCase):
    def test_no_performance_template_uses_processor_names(self):
        root = Path(settings.BASE_DIR) / "performance"
        if not root.exists():
            return  # the Performance module is not on this branch
        for f in root.rglob("*.html"):
            text = f.read_text()
            self.assertNotIn("processed_by", text, f)
            self.assertNotIn("last_action", text, f)
