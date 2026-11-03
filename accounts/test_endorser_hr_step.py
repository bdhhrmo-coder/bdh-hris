"""
Owner decision 2026-11-03 (Option B): whoever endorsed a request (unit head
or Supervisor step) cannot also act on it at the HR step; another HR
Processor or HR Administrator must. A return and resubmission starts a new
cycle. Also checks the owner's own set-up: Employee + Supervisor of HRMS +
Supervisor (unit head) of ER + HR Administrator, attached to HRMS only.
"""

from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from accounts.test_self_approval import NURSING, R, ids, person, unit
from cto.models import CTOUsageApplication, CTOUsageApplicationAction
from cto.permissions import visible_cto_applications_for
from leave.permissions import can_act_hr_step, endorser_ids, endorsers_for, hr_step_actors
from official_requests.models import OfficialRequest
from official_requests.permissions import actor_may_advance

HRMS = "Human Resources Management Section"


class EndorserNotAtHRStepTests(TestCase):
    def setUp(self):
        self.er = unit(NURSING, "ER")
        self.chief_nurse = person("cn3", [NURSING], sup_sections=[NURSING])
        # The multi-role owner: attached to HRMS only (confirmed 2026-11-03).
        self.boss = person("boss3", [HRMS], roles=[R.HR_ADMINISTRATOR],
                           sup_sections=[HRMS], sup_units=[self.er])
        self.hr_staff = person("hrstaff3", [HRMS])
        self.hr2 = person("hr3b", [HRMS], roles=[R.HR_PROCESSOR])
        self.er_nurse = person("ernurse3", [NURSING], [self.er])
        self.ao = person("ao3", roles=[R.ADMINISTRATIVE_OFFICER])
        self.coh = person("coh3", roles=[R.CHIEF_OF_HOSPITAL])

    def cto(self, emp):
        return CTOUsageApplication.objects.create(employee=emp, start_date=date(2026, 11, 20),
                                                  end_date=date(2026, 11, 20), number_of_days=1)

    def act(self, who, app, action):
        c = Client()
        c.force_login(who.user)
        r = c.post(reverse("cto:cto_action", args=[app.pk]), {"action": action})
        app.refresh_from_db()
        return r.status_code

    def test_owner_own_request_goes_to_the_ao(self):
        self.assertEqual(ids(endorsers_for([self.boss])), {self.ao.pk})

    def test_hrms_staff_endorsed_by_owner_then_another_hr(self):
        app = self.cto(self.hr_staff)
        self.act(self.boss, app, "endorse")
        self.assertEqual(app.status, CTOUsageApplication.ENDORSED_BY_SUPERVISOR)
        self.assertEqual(endorser_ids(app), {self.boss.pk})
        self.assertNotIn(app, visible_cto_applications_for(self.boss))
        self.assertFalse(can_act_hr_step(self.boss, [self.hr_staff], app))
        self.assertEqual(self.act(self.boss, app, "process"), 403)
        self.assertEqual(app.status, CTOUsageApplication.ENDORSED_BY_SUPERVISOR)
        self.assertNotIn(self.boss.pk, ids(hr_step_actors([self.hr_staff], app)))   # not notified
        self.assertIn(self.hr2.pk, ids(hr_step_actors([self.hr_staff], app)))
        self.assertIn(app, visible_cto_applications_for(self.hr2))
        self.act(self.hr2, app, "process")
        self.assertEqual(app.status, CTOUsageApplication.PROCESSED_BY_HR)

    def test_er_nurse_unit_head_owner_cannot_process_after_chief_nurse(self):
        app = self.cto(self.er_nurse)
        self.act(self.boss, app, "endorse")
        self.assertEqual(app.status, CTOUsageApplication.ENDORSED_BY_UNIT_HEAD)
        self.act(self.chief_nurse, app, "endorse")
        self.assertEqual(app.status, CTOUsageApplication.ENDORSED_BY_SUPERVISOR)
        self.assertEqual(endorser_ids(app), {self.boss.pk, self.chief_nurse.pk})
        self.assertNotIn(app, visible_cto_applications_for(self.boss))
        self.assertEqual(self.act(self.boss, app, "process"), 403)
        self.assertIn(app, visible_cto_applications_for(self.hr2))

    def test_an_hr_person_who_did_not_endorse_is_unaffected(self):
        app = self.cto(self.hr_staff)
        self.act(self.boss, app, "endorse")
        self.assertTrue(can_act_hr_step(self.hr2, [self.hr_staff], app))

    def test_resubmission_starts_a_new_cycle(self):
        app = self.cto(self.hr_staff)
        log = lambda who, status, action: CTOUsageApplicationAction.objects.create(
            application=app, action=action, resulting_status=status, acted_by=who.user)
        log(self.boss, CTOUsageApplication.ENDORSED_BY_SUPERVISOR, "endorse")
        log(self.hr2, CTOUsageApplication.RETURNED, "return")
        log(self.hr_staff, CTOUsageApplication.SUBMITTED, "resubmit")
        self.assertEqual(endorser_ids(app), set())
        log(self.chief_nurse, CTOUsageApplication.ENDORSED_BY_SUPERVISOR, "endorse")
        self.assertEqual(endorser_ids(app), {self.chief_nurse.pk})

    def test_official_business_same_rule(self):
        req = OfficialRequest.objects.create(employee=self.hr_staff, request_type=OfficialRequest.OFFICIAL_BUSINESS,
                                             start_date=date(2026, 11, 20), end_date=date(2026, 11, 20),
                                             purpose="TEST")
        c = Client()
        c.force_login(self.boss.user)
        c.post(reverse("official_requests:request_action", args=[req.pk]), {"action": "endorse"})
        req.refresh_from_db()
        self.assertEqual(req.status, OfficialRequest.ENDORSED_BY_SUPERVISOR)
        self.assertFalse(actor_may_advance(self.boss, req))
        self.assertTrue(actor_may_advance(self.hr2, req))
