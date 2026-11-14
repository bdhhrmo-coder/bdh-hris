"""Out-on-Pass stage 2: filing, routing and actions (CLAUDE.md 13B)."""

import shutil
import tempfile
from datetime import time, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment as RA
from documents.models import UploadedDocument
from employees.models import Employee
from notifications.models import Notification
from orgstructure.models import Section, Unit

from .models import OutOnPass as P

MEDIA = tempfile.mkdtemp()


def emp(username, section=None, unit=None, roles=(), scope_unit=None, status="REGULAR"):
    user = User.objects.create_user(username=username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(),
                                first_name="T", employment_status=status)
    if section:
        e.sections.add(section)
    if unit:
        e.units.add(unit)
    for role in roles:
        if role == RA.SUPERVISOR:
            RA.objects.create(employee=e, role=role, section=None if scope_unit else section, unit=scope_unit)
        else:
            RA.objects.create(employee=e, role=role)
    return e


def pdf():
    return SimpleUploadedFile("proof.pdf", b"%PDF-1.4\n%test\n", content_type="application/pdf")


@override_settings(MEDIA_ROOT=MEDIA)
class FlowTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.lab = Section.objects.get(name="Laboratory Section")
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.er, _ = Unit.objects.get_or_create(section=self.nursing, name="ER")
        self.head = emp("labhead", self.lab, roles=[RA.SUPERVISOR])
        self.staff = emp("staff", self.lab)
        self.ao = emp("ao", roles=[RA.ADMINISTRATIVE_OFFICER])
        self.coh = emp("coh", roles=[RA.CHIEF_OF_HOSPITAL])
        self.hr = emp("hr", roles=[RA.HR_PROCESSOR])
        self.day = timezone.localdate() + timedelta(days=3)

    # -- helpers --
    def file(self, who, kind=P.ORDINARY, out=time(10), back=time(11), proof=None, day=None, **extra):
        self.client.force_login(who.user)
        data = {"kind": kind, "pass_date": (day or self.day).isoformat(), "time_out": out.strftime("%H:%M"),
                "time_in": back.strftime("%H:%M"), "reason_type": P.REASON_GOVT_BANK, "reason": "Bank"}
        if proof:
            data["proof"] = proof
        data.update(extra)
        return self.client.post(reverse("outonpass:new"), data)

    def act(self, who, p, action, notes="", confirm=True, **extra):
        self.client.force_login(who.user)
        data = {"action": action, "notes": notes}
        if confirm:
            data.update(confirmed_coverage="on", confirmed_no_surge="on", confirmed_shift_hours="on")
        data.update(extra)
        resp = self.client.post(reverse("outonpass:action", args=[p.pk]), data)
        p.refresh_from_db()
        return resp

    def last(self, who):
        return P.objects.filter(employee=who).latest("pk")

    # -- ordinary pass --
    def test_ordinary_pass_supervisor_then_ao(self):
        r = self.file(self.staff)
        p = self.last(self.staff)
        self.assertRedirects(r, reverse("outonpass:detail", args=[p.pk]))
        self.assertEqual(p.status, P.SUBMITTED)
        self.assertTrue(Notification.objects.filter(recipient=self.head).exists())
        # Nobody but the supervisor acts at the first step.
        self.assertEqual(self.act(self.ao, p, "approve").status_code, 403)
        self.act(self.head, p, "endorse")
        self.assertEqual(p.status, P.ENDORSED)
        self.assertTrue(p.confirmed_coverage and p.confirmed_no_surge)
        self.act(self.ao, p, "approve")
        self.assertEqual(p.status, P.APPROVED)
        self.assertTrue(Notification.objects.filter(recipient=self.staff, message__contains="Tap Out").exists())

    def test_coh_may_also_approve_an_ordinary_pass(self):
        self.file(self.staff)
        p = self.last(self.staff)
        self.act(self.head, p, "endorse")
        self.act(self.coh, p, "approve")
        self.assertEqual(p.status, P.APPROVED)

    def test_endorsement_needs_the_confirmations(self):
        self.file(self.staff)
        p = self.last(self.staff)
        self.act(self.head, p, "endorse", confirm=False)
        self.assertEqual(p.status, P.SUBMITTED)

    def test_violation_is_not_filed_and_reason_shown(self):
        r = self.file(self.staff, out=time(9), back=time(11, 30))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "This pass was not filed")
        self.assertContains(r, "may not be longer than 2 h 00 min")
        self.assertFalse(P.objects.filter(employee=self.staff).exists())

    def test_return_needs_remark_then_resubmit_restarts(self):
        self.file(self.staff)
        p = self.last(self.staff)
        self.act(self.head, p, "return")
        self.assertEqual(p.status, P.SUBMITTED)  # no remark: nothing changed
        self.act(self.head, p, "return", notes="Pick another time")
        self.assertEqual(p.status, P.RETURNED)
        self.client.force_login(self.staff.user)
        self.client.post(reverse("outonpass:edit", args=[p.pk]), {
            "kind": P.ORDINARY, "pass_date": self.day.isoformat(), "time_out": "13:00", "time_in": "14:00",
            "reason_type": P.REASON_GOVT_BANK, "reason": "Bank"})
        p.refresh_from_db()
        self.assertEqual((p.status, p.time_out), (P.SUBMITTED, time(13)))
        self.assertEqual(p.actions.last().action, "resubmit")

    def test_reject_needs_remark(self):
        self.file(self.staff)
        p = self.last(self.staff)
        self.act(self.head, p, "reject", notes="Short-staffed")
        self.assertEqual(p.status, P.REJECTED)

    def test_filer_cancels_and_slot_is_freed(self):
        self.file(self.staff)
        p = self.last(self.staff)
        self.act(self.staff, p, "cancel", confirm=False)
        self.assertEqual(p.status, P.CANCELLED)
        self.file(self.staff, out=time(14), back=time(15))  # one a day: the cancelled one no longer counts
        self.assertEqual(P.objects.filter(employee=self.staff, status=P.SUBMITTED).count(), 1)

    def test_rules_checked_again_at_approval(self):
        self.file(self.staff)
        p = self.last(self.staff)
        self.act(self.head, p, "endorse")
        P.objects.filter(pk=p.pk).update(pass_date=timezone.localdate() - timedelta(days=1))  # time out has passed
        self.act(self.ao, p, "approve")
        self.assertEqual(p.status, P.ENDORSED)

    # -- next level up --
    def test_section_head_pass_approved_by_ao_in_one_step(self):
        self.file(self.head)
        p = self.last(self.head)
        self.act(self.ao, p, "approve")
        self.assertEqual(p.status, P.APPROVED)

    def test_ao_pass_approved_by_coh(self):
        self.file(self.ao)
        p = self.last(self.ao)
        self.assertEqual(self.act(self.ao, p, "approve").status_code, 403)  # never on one's own pass
        self.act(self.coh, p, "approve")
        self.assertEqual(p.status, P.APPROVED)

    def test_unit_head_endorses_nursing_staff_single_level(self):
        cn = emp("chiefnurse", self.nursing, roles=[RA.SUPERVISOR])
        uh = emp("erhead", self.nursing, roles=[RA.SUPERVISOR], scope_unit=self.er)
        nurse = emp("nurse", self.nursing, self.er)
        self.file(nurse)
        p = self.last(nurse)
        self.assertEqual(self.act(cn, p, "endorse").status_code, 403)
        self.act(uh, p, "endorse")
        self.assertEqual(p.status, P.ENDORSED)  # single level: straight to the AO or COH
        self.act(self.ao, p, "approve")
        self.assertEqual(p.status, P.APPROVED)

    # -- special types --
    def test_third_pass_needs_proof_and_coh_alone_decides(self):
        nm = (timezone.localdate().replace(day=1) + timedelta(days=40)).replace(day=1)
        for d in (nm + timedelta(days=1), nm + timedelta(days=2)):
            P.objects.create(employee=self.staff, pass_date=d, time_out=time(8), time_in=time(8, 30),
                             reason_type=P.REASON_ERRAND, reason="x", status=P.APPROVED, filed_by=self.staff.user)
        d3 = nm + timedelta(days=5)
        r = self.file(self.staff, kind=P.THIRD, day=d3)
        self.assertContains(r, "without proof is not processed")
        self.file(self.staff, kind=P.THIRD, day=d3, proof=pdf())
        p = P.objects.get(employee=self.staff, kind=P.THIRD)
        self.act(self.head, p, "endorse")
        self.assertEqual(p.status, P.SUBMITTED)  # third pass: staff covering is required
        self.act(self.head, p, "endorse", covering_staff="J. Cruz")
        self.assertEqual(p.status, P.ENDORSED)
        self.assertEqual(self.act(self.ao, p, "approve").status_code, 403)
        self.act(self.coh, p, "approve")
        self.assertEqual(p.status, P.APPROVED)

    def test_proof_seen_by_filer_supervisor_and_coh_not_hr(self):
        self.file(self.staff, kind=P.EXCEPTION, out=time(9), back=time(11, 30), proof=pdf())
        p = self.last(self.staff)
        doc = UploadedDocument.objects.get(object_id=p.pk)
        self.assertTrue(doc.is_confidential)
        url = reverse("documents:document_download", args=[doc.pk])
        for who, code in ((self.staff, 200), (self.head, 200), (self.coh, 200), (self.hr, 403), (self.ao, 403)):
            self.client.force_login(who.user)
            self.assertEqual(self.client.get(url).status_code, code, who)

    def test_section_head_special_pass_goes_ao_then_coh(self):
        self.file(self.head, kind=P.EXCEPTION, out=time(9), back=time(11, 30))
        p = self.last(self.head)
        self.act(self.ao, p, "endorse")
        self.assertEqual(p.status, P.ENDORSED)
        self.assertEqual(self.act(self.ao, p, "approve").status_code, 403)
        self.act(self.coh, p, "approve")
        self.assertEqual(p.status, P.APPROVED)

    # -- the COH's own pass --
    def test_coh_pass_recorded_by_hr(self):
        self.file(self.coh)
        p = self.last(self.coh)
        self.assertTrue(p.recorded_only)
        self.assertEqual(self.act(self.ao, p, "approve").status_code, 403)
        self.act(self.hr, p, "record", confirm=False)
        self.assertEqual(p.status, P.RECORDED)

    # -- screens --
    def test_queue_and_menu_count(self):
        self.file(self.staff)
        self.client.force_login(self.head.user)
        r = self.client.get(reverse("outonpass:queue"))
        self.assertContains(r, self.last(self.staff).pass_number)
        self.assertContains(self.client.get(reverse("outonpass:mine")), "Balance")

    def test_outsider_cannot_open_a_pass(self):
        other = emp("pharm", Section.objects.get(name="Pharmacy Section"))
        self.file(self.staff)
        self.client.force_login(other.user)
        self.assertEqual(self.client.get(reverse("outonpass:detail", args=[self.last(self.staff).pk])).status_code, 403)
