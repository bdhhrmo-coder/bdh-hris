"""Pilot Item 5: OIC for the COH and the AO (owner decisions 2026-11-24). Made-up data only."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auditlog.aggregation import audit_rows
from employees.models import Employee
from leave.models import LeaveApplication, LeaveType
from leave.permissions import OIC_REPEAT_TAG, can_endorse, endorsers_for
from notifications.models import Notification
from orgstructure.models import Section

from .models import RoleAssignment as R
from .oic import OICForm, may_be_oic, oic_candidates


def person(username, status="REGULAR", section=None, role=None, role_section=None):
    user = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(), first_name="T",
                                date_hired=date(2020, 1, 1), employment_status=status)
    if section:
        e.sections.add(section)
    if role:
        R.objects.create(employee=e, role=role, section=role_section)
    return e


class OICForCohAoTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.lab = Section.objects.get(name="Laboratory Section")
        self.pharm = Section.objects.get(name="Pharmacy Section")
        self.head = person("i5head", section=self.lab, role=R.SUPERVISOR, role_section=self.lab)  # section head
        self.other_head = person("i5ph", section=self.pharm, role=R.SUPERVISOR, role_section=self.pharm)
        self.nurse = person("i5cosp", status=Employee.EMPLOYMENT_STATUS_COSP, section=self.lab)
        self.hr = person("i5hr", role=R.HR_PROCESSOR)
        self.hradmin = person("i5hra", role=R.HR_ADMINISTRATOR)
        self.ao = person("i5ao", role=R.ADMINISTRATIVE_OFFICER)
        self.coh = person("i5coh", role=R.CHIEF_OF_HOSPITAL)

    def oic_for_ao(self, oic=None):
        return R.objects.create(employee=oic or self.head, role=R.ADMINISTRATIVE_OFFICER, is_oic=True,
                                delegated_by=self.ao, start_date=self.today, end_date=self.today + timedelta(days=5))

    # -- who may be OIC --------------------------------------------------------------
    def test_coh_list_has_the_ao_supervisor_list_does_not(self):
        self.assertIn(self.ao, oic_candidates(self.coh))
        self.assertNotIn(self.ao, oic_candidates(self.other_head))
        scope = R.objects.get(employee=self.other_head)
        form = OICForm({"scope": scope.pk, "oic": self.ao.pk, "start_date": self.today,
                        "end_date": self.today}, delegator=self.other_head)
        self.assertFalse(form.is_valid())

    def test_who_may_cover_the_coh(self):
        coh_scope = R.objects.get(employee=self.coh)
        self.assertTrue(may_be_oic(self.ao, coh_scope))
        self.assertTrue(may_be_oic(self.head, coh_scope))
        self.assertFalse(may_be_oic(self.nurse, coh_scope))  # COSP: never for the COH / AO
        R.objects.create(employee=self.nurse, role=R.SUPERVISOR, section=self.lab)
        self.assertFalse(may_be_oic(self.nurse, coh_scope))  # not even a COSP Supervisor
        self.assertFalse(may_be_oic(self.nurse, R.objects.get(employee=self.ao)))

    def test_hr_designation_needs_reference(self):
        scope = R.objects.get(employee=self.coh)
        self.client.force_login(self.hradmin.user)

        def post(**over):
            data = {"scope": scope.pk, "oic": self.ao.pk, "start_date": self.today,
                    "end_date": self.today + timedelta(days=2), "reason": "Out of town",
                    "instruction": SimpleUploadedFile("o.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
                    "reference": "OO-2026-TEST"}
            data.update(over)
            return self.client.post(reverse("oic_hr_home"), data)

        post(reference="")
        self.assertFalse(R.objects.filter(is_oic=True).exists())
        post()
        ra = R.objects.get(is_oic=True)
        self.assertEqual(ra.designation_reference, "OO-2026-TEST")
        row = [x for x in audit_rows() if x["module"] == "OIC Designation"][0]
        self.assertIn("ref. no. OO-2026-TEST", row["detail"])
        self.assertIn("signed written designation", row["detail"])

    # -- acting as OIC ----------------------------------------------------------------
    def leave(self, employee, **kw):
        return LeaveApplication.objects.create(
            employee=employee, leave_type=LeaveType.objects.get(code="COSP_LEAVE"), route=LeaveApplication.ROUTE_COSP,
            start_date=self.today + timedelta(days=20), end_date=self.today + timedelta(days=21),
            number_of_days=Decimal("2"), **kw)

    def act(self, who, app, action, notes=""):
        self.client.force_login(who.user)
        r = self.client.post(reverse("leave:leave_action", args=[app.pk]), {"action": action, "notes": notes},
                             follow=True)
        app.refresh_from_db()
        return r

    def test_oic_acting_again_needs_remark_and_is_tagged(self):
        self.oic_for_ao()
        app = self.leave(self.nurse)
        self.act(self.head, app, "endorse")
        self.assertEqual(app.status, LeaveApplication.ENDORSED_BY_SUPERVISOR)
        self.act(self.hr, app, "process")
        self.assertEqual(app.status, LeaveApplication.PROCESSED_BY_HR)
        note = Notification.objects.filter(recipient=self.head).order_by("-pk").first()
        self.assertTrue(note.message.startswith("As OIC for the AO: "), note.message)
        self.assertFalse(Notification.objects.filter(recipient=self.ao, message__contains="AO").exclude(
            message__contains="OIC").exists())  # the absent AO is not told to act
        r = self.act(self.head, app, "recommend")
        self.assertEqual(app.status, LeaveApplication.PROCESSED_BY_HR)
        self.assertContains(r, "Nothing was changed")
        self.act(self.head, app, "recommend", "Checked the staffing again")
        self.assertEqual(app.status, LeaveApplication.RECOMMENDED_BY_AO)
        last = app.actions.order_by("-pk").first()
        self.assertTrue(last.notes.startswith(OIC_REPEAT_TAG))
        row = [x for x in audit_rows(module="Leave") if "OIC, acted earlier" in x["action"]]
        self.assertEqual(len(row), 1)

    def test_oic_who_did_not_act_earlier_needs_no_remark(self):
        self.oic_for_ao(self.other_head)
        app = self.leave(self.nurse)
        self.act(self.head, app, "endorse")
        self.act(self.hr, app, "process")
        self.act(self.other_head, app, "recommend")
        self.assertEqual(app.status, LeaveApplication.RECOMMENDED_BY_AO)

    def test_banner_and_own_request_waits(self):
        self.oic_for_ao()
        self.client.force_login(self.head.user)
        page = self.client.get(reverse("leave:my_applications"))
        self.assertContains(page, "acting as Officer-in-Charge")
        self.assertContains(page, "OIC for the AO")
        own = self.leave(self.head)  # a section head's own request goes to the AO level
        # The OIC never acts on their own; the absent AO is set aside. Existing rule (unchanged):
        # with no AO left, the COH takes the step ("the COH when the AO is the filer").
        self.assertEqual(endorsers_for([self.head]), [self.coh])
        self.assertFalse(can_endorse(self.head, [self.head]))
        self.assertFalse(can_endorse(self.ao, [self.head]))
        self.act(self.head, own, "endorse")
        self.assertEqual(own.status, LeaveApplication.SUBMITTED)
        self.client.force_login(self.head.user)
        self.assertNotContains(self.client.get(reverse("leave:leave_queue")), f"/leave/{own.pk}/")

    def test_own_request_waits_when_one_oic_covers_ao_and_coh(self):
        self.oic_for_ao()
        R.objects.create(employee=self.head, role=R.CHIEF_OF_HOSPITAL, is_oic=True, delegated_by=self.coh,
                         start_date=self.today, end_date=self.today + timedelta(days=5))
        self.assertEqual(endorsers_for([self.head]), [])  # nobody can act: it waits until the OIC ends
        self.client.force_login(self.ao.user)
        self.assertNotContains(self.client.get(reverse("leave:my_applications")), "acting as Officer-in-Charge")
