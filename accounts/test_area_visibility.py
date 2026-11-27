"""Part C (owner decisions 2026-11-27): Unit Heads, Supervisors and the Chief
Nurse see only requests of their own unit or section. Most of this was already
true; these tests pin it down, plus the one change (group requests across
units, decision 4 Option A). Made-up names and data only."""

from datetime import date, time, timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment as R
from cto.models import CTOUsageApplication
from exchange.models import DutyExchangeRequest
from exchange.permissions import can_view_exchange_request
from leave.models import LeaveApplication as A, LeaveType
from leave.notifications import notify_status_change
from leave.permissions import can_view_application, visible_applications_for
from leave.tests import make_employee
from notifications.models import Notification
from official_requests import batch as batch_rules
from official_requests.batch_views import batch_full_view, can_view_batch
from official_requests.models import OfficialRequestBatch as B
from orgstructure.models import Section, Unit

PW = "testpass123"


class AreaVisibilityTests(TestCase):
    def setUp(self):
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.lab = Section.objects.get(name="Laboratory Section")
        self.er = Unit.objects.get(section=self.nursing, name="ER")
        self.ped = Unit.objects.get(section=self.nursing, name="Pediatric")
        self.vl = LeaveType.objects.get(code="VL")

        def staff(user, eid, section, unit=None):
            e = make_employee(user, eid, date(2020, 1, 1))
            e.sections.add(section)
            if unit:
                e.units.add(unit)
            return e

        self.nurse_er = staff("c_nurse_er", "EMP-C1", self.nursing, self.er)
        self.nurse_ped = staff("c_nurse_ped", "EMP-C2", self.nursing, self.ped)
        self.medtech = staff("c_medtech", "EMP-C3", self.lab)
        self.uh_er = staff("c_uh_er", "EMP-C4", self.nursing)
        R.objects.create(employee=self.uh_er, role=R.SUPERVISOR, unit=self.er)
        self.uh_ped = staff("c_uh_ped", "EMP-C5", self.nursing)
        R.objects.create(employee=self.uh_ped, role=R.SUPERVISOR, unit=self.ped)
        self.chief_nurse = staff("c_chiefnurse", "EMP-C6", self.nursing)
        R.objects.create(employee=self.chief_nurse, role=R.SUPERVISOR, section=self.nursing)
        self.lab_head = staff("c_labhead", "EMP-C7", self.lab)
        R.objects.create(employee=self.lab_head, role=R.SUPERVISOR, section=self.lab)
        self.hrp = make_employee("c_hrp", "EMP-C8", date(2018, 1, 1), role=R.HR_PROCESSOR)
        self.ao = make_employee("c_ao", "EMP-C9", date(2012, 1, 1), role=R.ADMINISTRATIVE_OFFICER)
        self.coh = make_employee("c_coh", "EMP-C10", date(2010, 1, 1), role=R.CHIEF_OF_HOSPITAL)

    def leave(self, emp, status=A.SUBMITTED):
        return A.objects.create(employee=emp, leave_type=self.vl, status=status, start_date=date(2026, 12, 7),
                                end_date=date(2026, 12, 8), number_of_days=Decimal("2"))

    def page(self, user, url):
        self.client.login(username=user, password=PW)
        return self.client.get(url)

    # --- leave: queue, detail, direct link, home list ---------------------
    def test_unit_head_sees_only_own_unit(self):
        mine, other = self.leave(self.nurse_er), self.leave(self.nurse_ped)
        self.assertTrue(can_view_application(self.uh_er, mine))
        self.assertFalse(can_view_application(self.uh_er, other))
        self.assertIn(mine, visible_applications_for(self.uh_er))
        self.assertNotIn(other, visible_applications_for(self.uh_er))
        self.assertEqual(self.page("c_uh_er", reverse("leave:leave_detail", args=[other.pk])).status_code, 403)
        queue = self.page("c_uh_er", reverse("leave:leave_queue"))
        self.assertNotContains(queue, reverse("leave:leave_detail", args=[other.pk]))
        home = self.page("c_uh_er", reverse("homepage:home"))
        self.assertNotContains(home, self.nurse_ped.surname)

    def test_chief_nurse_sees_all_nursing_units_and_nothing_else(self):
        for emp in (self.nurse_er, self.nurse_ped):
            self.assertTrue(can_view_application(self.chief_nurse, self.leave(emp)))
        lab = self.leave(self.medtech)
        self.assertFalse(can_view_application(self.chief_nurse, lab))
        self.assertEqual(self.page("c_chiefnurse", reverse("leave:leave_detail", args=[lab.pk])).status_code, 403)

    def test_section_supervisor_sees_only_own_section(self):
        self.assertTrue(can_view_application(self.lab_head, self.leave(self.medtech)))
        self.assertFalse(can_view_application(self.lab_head, self.leave(self.nurse_er)))

    def test_hr_ao_coh_unchanged(self):
        for emp in (self.nurse_er, self.nurse_ped, self.medtech):
            a = self.leave(emp)
            for who in (self.hrp, self.ao, self.coh):
                self.assertTrue(can_view_application(who, a), (who, emp))

    def test_employee_always_sees_own(self):
        self.assertTrue(can_view_application(self.nurse_ped, self.leave(self.nurse_ped)))

    # --- notifications ------------------------------------------------------
    def test_notification_only_to_own_unit_head(self):
        a = self.leave(self.nurse_ped)
        notify_status_change(a)
        to = set(Notification.objects.values_list("recipient_id", flat=True))
        self.assertIn(self.uh_ped.pk, to)
        self.assertNotIn(self.uh_er.pk, to)
        self.assertNotIn(self.lab_head.pk, to)

    # --- OIC -----------------------------------------------------------------
    def test_oic_sees_covered_unit_only_while_active(self):
        today = date.today()
        oic = R.objects.create(employee=self.uh_er, role=R.SUPERVISOR, unit=self.ped, is_oic=True,
                               delegated_by=self.uh_ped, start_date=today - timedelta(days=1),
                               end_date=today + timedelta(days=2))
        a = self.leave(self.nurse_ped)
        self.assertTrue(can_view_application(self.uh_er, a))
        self.assertIn(a, visible_applications_for(self.uh_er))
        self.assertFalse(can_view_application(self.uh_er, self.leave(self.medtech)))
        oic.end_date = today - timedelta(days=1)
        oic.start_date = today - timedelta(days=3)
        oic.save()
        self.assertFalse(can_view_application(self.uh_er, a))
        self.assertNotIn(a, visible_applications_for(self.uh_er))

    # --- cross-unit Exchange of Duty --------------------------------------
    def test_cross_unit_exchange_visible_to_both_heads_and_chief_nurse_only(self):
        x = DutyExchangeRequest.objects.create(employee_a=self.nurse_er, date_a=date(2026, 12, 10),
                                               employee_b=self.nurse_ped, date_b=date(2026, 12, 12))
        for who in (self.uh_er, self.uh_ped, self.chief_nurse):
            self.assertTrue(can_view_exchange_request(who, x), who)
        self.assertFalse(can_view_exchange_request(self.lab_head, x))
        # the partner's other requests stay hidden from the other head
        self.assertFalse(can_view_application(self.uh_er, self.leave(self.nurse_ped)))

    # --- other request types: direct links -----------------------------------
    def test_cto_detail_link_refused_outside_unit(self):
        c = CTOUsageApplication.objects.create(employee=self.nurse_ped, start_date=date(2026, 12, 14),
                                               end_date=date(2026, 12, 14), number_of_days=Decimal("1"))
        self.assertEqual(self.page("c_uh_er", reverse("cto:cto_detail", args=[c.pk])).status_code, 403)
        self.assertEqual(self.page("c_uh_ped", reverse("cto:cto_detail", args=[c.pk])).status_code, 200)

    # --- group requests across units (decision 4, Option A) --------------------
    def ot_batch(self):
        entries = [{"employee": e, "date": date(2026, 12, 5), "time_from": time(17), "time_to": time(19)}
                   for e in (self.nurse_er, self.nurse_ped)]
        hr_user = self.hrp.user
        return batch_rules.create_batch(hr_user, B.FILER_HR_PROCESSOR, "OT", "Coverage", "", entries)

    def test_group_request_unit_head_sees_only_own_unit_lines(self):
        b = self.ot_batch()
        self.assertTrue(can_view_batch(self.uh_er, self.uh_er.user, b))  # unchanged: may open it
        self.assertFalse(batch_full_view(self.uh_er, self.uh_er.user, b))
        page = self.page("c_uh_er", reverse("official_requests:batch_detail", args=[b.pk]))
        self.assertContains(page, self.nurse_er.full_name)
        self.assertNotContains(page, self.nurse_ped.full_name)
        self.assertContains(page, "outside your unit or section")
        self.assertFalse(can_view_batch(self.lab_head, self.lab_head.user, b))

    def test_group_request_full_view_for_chief_nurse_hr_ao_coh(self):
        b = self.ot_batch()
        for who in (self.chief_nurse, self.hrp, self.ao, self.coh):
            self.assertTrue(batch_full_view(who, who.user, b), who)
        page = self.page("c_chiefnurse", reverse("official_requests:batch_detail", args=[b.pk]))
        self.assertContains(page, self.nurse_ped.full_name)
        self.assertContains(page, self.nurse_er.full_name)

    def test_group_request_print_refused_for_partial_view(self):
        entries = [{"employee": e, "date": date(2026, 12, 9)} for e in (self.nurse_er, self.nurse_ped)]
        b = batch_rules.create_batch(self.hrp.user, B.FILER_HR_PROCESSOR, "OFFICIAL_BUSINESS", "Seminar",
                                     "Puerto Princesa", entries, expenses="PER_DIEM")
        url = reverse("official_requests:print_batch_travel_order", args=[b.pk])
        self.assertEqual(self.page("c_uh_er", url).status_code, 403)
        # the Chief Nurse covers every line: not refused, only told it isn't printable yet
        self.assertEqual(self.page("c_chiefnurse", url).status_code, 302)
        detail = self.page("c_chiefnurse", reverse("official_requests:batch_detail", args=[b.pk]))
        self.assertEqual(detail.status_code, 200)

    # --- data check -------------------------------------------------------
    def test_area_check_flags_hospital_wide_supervisor_line(self):
        R.objects.create(employee=self.medtech, role=R.SUPERVISOR)  # no scope = hospital-wide
        out = StringIO()
        call_command("check_area_scope", stdout=out)
        text = out.getvalue()
        self.assertIn(f"employee {self.medtech.employee_id}", text)
        self.assertIn("Nothing was changed.", text)
        self.assertNotIn(self.medtech.surname, text)  # IDs only, no names
