"""
Owner decisions 2026-10-26 (pilot employee import):
  1A  A Supervisor with a unit on their import row supervises that unit only;
      with no unit, the whole section (the Chief Nurse).
  2A  The COH and the AO may be imported with no section.
  3A  Nobody acts on their own request at any step. A unit head's request is
      endorsed by the section-wide Supervisor (not a fellow unit head); a
      section head's by the AO; the AO's own request goes to the COH.
"""

from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from cto.models import CTOUsageApplication
from cto.permissions import visible_cto_applications_for
from dataimport import employee_import
from dataimport.tests import EMP_COLS, workbook_file
from employees.models import Employee
from exchange.models import DutyExchangeRequest
from leave.permissions import (
    ao_step_actors,
    can_act_ao_step,
    can_act_coh_step,
    can_act_hr_step,
    can_endorse,
    endorsers_for,
)
from official_requests.models import OfficialRequest
from official_requests.permissions import actor_may_advance
from orgstructure.models import Section, Unit

R = RoleAssignment
NURSING = "Nursing Service Section"
AFS = "Accounting and Finance Section"


def section(name):
    return Section.objects.get_or_create(name=name)[0]


def unit(section_name, name):
    return Unit.objects.get_or_create(section=section(section_name), name=name)[0]


def person(username, sections=(), units=(), roles=(), sup_units=(), sup_sections=()):
    user = User.objects.create_user(username=username, password="pw-12345678")
    emp = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(),
                                  first_name="Test", employment_status="REGULAR", date_hired=date(2020, 1, 1))
    emp.sections.set([section(s) for s in sections])
    emp.units.set(list(units))
    R.objects.create(employee=emp, role=R.EMPLOYEE)
    for role in roles:
        R.objects.create(employee=emp, role=role)
    for u in sup_units:
        R.objects.create(employee=emp, role=R.SUPERVISOR, unit=u)
    for s in sup_sections:
        R.objects.create(employee=emp, role=R.SUPERVISOR, section=section(s))
    return emp


def ids(people):
    return {p.pk for p in people}


class NextLevelUpTests(TestCase):
    def setUp(self):
        self.ordr = unit(NURSING, "OR/DR")
        self.er = unit(NURSING, "ER")
        self.chief_nurse = person("chiefnurse", [NURSING], sup_sections=[NURSING])
        self.head1 = person("head1", [NURSING], [self.ordr], sup_units=[self.ordr])
        self.head2 = person("head2", [NURSING], [self.ordr], sup_units=[self.ordr])
        self.nurse = person("nurse", [NURSING], [self.ordr])
        self.er_nurse = person("ernurse", [NURSING], [self.er])
        self.afs_head1 = person("afshead1", [AFS], sup_sections=[AFS])
        self.afs_head2 = person("afshead2", [AFS], sup_sections=[AFS])
        self.hr1 = person("hr1", ["Human Resources Management Section"], roles=[R.HR_PROCESSOR])
        self.hr2 = person("hr2", ["Human Resources Management Section"], roles=[R.HR_PROCESSOR])
        self.ao = person("ao", roles=[R.ADMINISTRATIVE_OFFICER])
        self.coh = person("coh", roles=[R.CHIEF_OF_HOSPITAL])

    def test_ordinary_employee_unit_heads_and_section_head_may_endorse(self):
        self.assertEqual(ids(endorsers_for([self.nurse])), ids([self.chief_nurse, self.head1, self.head2]))

    def test_unit_head_goes_to_the_chief_nurse_not_the_fellow_head_and_never_self(self):
        self.assertEqual(ids(endorsers_for([self.head1])), {self.chief_nurse.pk})
        self.assertFalse(can_endorse(self.head1, [self.head1]))
        self.assertFalse(can_endorse(self.head2, [self.head1]))
        self.assertTrue(can_endorse(self.chief_nurse, [self.head1]))

    def test_unit_with_no_head_goes_to_the_section_supervisor(self):
        self.assertEqual(ids(endorsers_for([self.er_nurse])), {self.chief_nurse.pk})

    def test_section_heads_go_to_the_ao_even_with_a_peer_head(self):
        self.assertEqual(ids(endorsers_for([self.chief_nurse])), {self.ao.pk})
        self.assertEqual(ids(endorsers_for([self.afs_head1])), {self.ao.pk})
        self.assertFalse(can_endorse(self.afs_head2, [self.afs_head1]))

    def test_ao_own_request_goes_to_the_coh(self):
        self.assertEqual(ids(endorsers_for([self.ao])), {self.coh.pk})
        self.assertEqual(ids(ao_step_actors([self.ao])), {self.coh.pk})
        self.assertFalse(can_act_ao_step(self.ao, [self.ao]))
        self.assertTrue(can_act_ao_step(self.coh, [self.ao]))

    def test_coh_never_approves_own_and_has_no_endorser(self):
        self.assertFalse(can_act_coh_step(self.coh, [self.coh]))
        self.assertEqual(endorsers_for([self.coh]), [])

    def test_hr_never_processes_own(self):
        self.assertFalse(can_act_hr_step(self.hr1, [self.hr1]))
        self.assertTrue(can_act_hr_step(self.hr2, [self.hr1]))

    def test_exchange_partners_never_endorse_each_other(self):
        # head1 and head2 swap: neither acts; the Chief Nurse is above both.
        self.assertEqual(ids(endorsers_for([self.head1, self.head2])), {self.chief_nurse.pk})

    def test_cto_action_and_queue_block_self_and_allow_next_level(self):
        app = CTOUsageApplication.objects.create(employee=self.head1, start_date=date(2026, 11, 20),
                                                 end_date=date(2026, 11, 20), number_of_days=1)
        self.assertNotIn(app, visible_cto_applications_for(self.head1))
        self.assertNotIn(app, visible_cto_applications_for(self.head2))
        self.assertIn(app, visible_cto_applications_for(self.chief_nurse))
        c = Client()
        c.force_login(self.head2.user)
        r = c.post(reverse("cto:cto_action", args=[app.pk]), {"action": "endorse"})
        self.assertEqual(r.status_code, 403)
        c.force_login(self.chief_nurse.user)
        c.post(reverse("cto:cto_action", args=[app.pk]), {"action": "endorse"})
        app.refresh_from_db()
        self.assertEqual(app.status, CTOUsageApplication.ENDORSED_BY_SUPERVISOR)

    def test_official_request_section_head_endorsed_by_ao(self):
        req = OfficialRequest.objects.create(
            employee=self.chief_nurse, request_type=OfficialRequest.OFFICIAL_BUSINESS,
            start_date=date(2026, 11, 20), end_date=date(2026, 11, 20), purpose="Meeting")
        self.assertFalse(actor_may_advance(self.chief_nurse, req))
        self.assertTrue(actor_may_advance(self.ao, req))

    def test_exchange_queue_excludes_parties(self):
        from exchange.permissions import visible_exchange_requests_for
        ex = DutyExchangeRequest.objects.create(
            employee_a=self.head1, employee_b=self.head2, date_a=date(2026, 11, 20), date_b=date(2026, 11, 21),
            status=DutyExchangeRequest.SUBMITTED)
        self.assertNotIn(ex, visible_exchange_requests_for(self.head1))
        self.assertNotIn(ex, visible_exchange_requests_for(self.head2))
        self.assertIn(ex, visible_exchange_requests_for(self.chief_nurse))


class ImportScopeTests(TestCase):
    def setUp(self):
        self.hradmin = person("imphradmin", ["Human Resources Management Section"], roles=[R.HR_ADMINISTRATOR])
        self.ordr = unit(NURSING, "OR/DR")

    def plan(self, rows):
        return employee_import.build_plan(workbook_file(employee_import.SHEET, EMP_COLS, rows), self.hradmin)

    def row(self, eid, **extra):
        base = {"employee_id": eid, "surname": "S" + eid, "first_name": "F", "employment_status": "Regular",
                "date_hired": "2020-01-01"}
        base.update(extra)
        return base

    def test_unit_head_scoped_to_unit_chief_nurse_to_section(self):
        plans = self.plan([
            self.row("U1", sections=NURSING, units="OR/DR", roles="Supervisor"),
            self.row("CN", sections=NURSING, roles="Supervisor"),
        ])
        self.assertEqual([p.errors for p in plans], [[], []])
        employee_import.apply_plan(plans, self.hradmin.user, "f.xlsx")
        head = Employee.objects.get(employee_id="U1")
        chief = Employee.objects.get(employee_id="CN")
        self.assertEqual(list(head.role_assignments.filter(role=R.SUPERVISOR).values_list("section", "unit")),
                         [(None, self.ordr.pk)])
        self.assertEqual(list(chief.role_assignments.filter(role=R.SUPERVISOR).values_list("section", "unit")),
                         [(section(NURSING).pk, None)])

    def test_coh_and_ao_need_no_section_others_do(self):
        plans = self.plan([
            self.row("COH1", roles="Chief of Hospital"),
            self.row("AO1", roles="Administrative Officer"),
            self.row("X1"),
        ])
        self.assertEqual(plans[0].errors, [])
        self.assertEqual(plans[1].errors, [])
        self.assertIn("Section(s) is required", " ".join(plans[2].errors))

    def test_unit_outside_the_rows_section_is_an_error(self):
        plans = self.plan([self.row("W1", sections=AFS, units="OR/DR")])
        self.assertIn("not in Section(s)", " ".join(plans[0].errors))


class COHOwnRequestTests(TestCase):
    """Owner decision 2026-10-26 (Option A): the COH's own Travel / Official
    Business / OT / Official Time is RECORDED by HR, like regular leave;
    the approval itself happens at the PHO or Capitol."""

    def setUp(self):
        self.coh = person("cohx", roles=[R.CHIEF_OF_HOSPITAL])
        self.hr = person("hrx", ["Human Resources Management Section"], roles=[R.HR_PROCESSOR])
        self.ao = person("aox", roles=[R.ADMINISTRATIVE_OFFICER])

    def test_coh_files_travel_and_hr_records_it(self):
        c = Client()
        c.force_login(self.coh.user)
        c.post(reverse("official_requests:request_apply"), {
            "request_type": OfficialRequest.TRAVEL, "start_date": "2026-11-20", "end_date": "2026-11-21",
            "destination": "Puerto Princesa", "purpose": "PHO meeting"})
        req = OfficialRequest.objects.get(employee=self.coh)
        self.assertTrue(req.recorded_only)
        self.assertFalse(actor_may_advance(self.ao, req))
        self.assertFalse(actor_may_advance(self.coh, req))
        self.assertTrue(actor_may_advance(self.hr, req))
        c.force_login(self.hr.user)
        c.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "record"})
        req.refresh_from_db()
        self.assertEqual(req.status, OfficialRequest.RECORDED)
        self.assertTrue(req.is_terminal())

    def test_ordinary_request_still_routes_normally(self):
        emp = person("empx", ["Human Resources Management Section"])
        c = Client()
        c.force_login(emp.user)
        c.post(reverse("official_requests:request_apply"), {
            "request_type": OfficialRequest.OFFICIAL_TIME, "start_date": "2026-11-20", "end_date": "2026-11-20",
            "purpose": "Seminar"})
        req = OfficialRequest.objects.get(employee=emp)
        self.assertFalse(req.recorded_only)
        c.force_login(self.hr.user)
        r = c.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "record"})
        self.assertEqual(r.status_code, 403)
