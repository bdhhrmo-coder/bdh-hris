"""Batch 8: CSC Form No. 6 section 6.B details, 6.D commutation, Wellness Leave reason,
leave details page, and filer edit/resubmit."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment as R
from orgstructure.models import Section

from .csc_form6 import fill_csc_form6
from .models import LeaveApplication as L
from .models import LeaveApplicationAction, LeaveCreditTransaction, LeaveType
from .tests import make_employee


class Base(TestCase):
    def setUp(self):
        self.emp = make_employee("cscemp", "EMP-C1", date_hired=date(2020, 1, 1))
        self.emp.position, self.emp.salary_grade = "Nurse II", "15"
        self.emp.save()
        self.hr = make_employee("cschr", "EMP-C2", date_hired=date(2020, 1, 1), role=R.HR_PROCESSOR)
        self.client.force_login(self.emp.user)
        self.t = {c: LeaveType.objects.get(code=c) for c in ("VL", "SPL", "SL", "SPECIAL_WOMEN", "STUDY", "WELLNESS")}

    def post(self, code, url=None, **fields):
        data = {"leave_type": self.t[code].pk, "start_date": "2026-11-02", "end_date": "2026-11-03",
                "number_of_days": "2", "commutation": "NOT_REQUESTED", "justification": ""}
        data.update(fields)
        return self.client.post(url or reverse("leave:leave_apply"), data)

    def errors(self, response):
        return response.context["form"].errors if response.status_code == 200 else {}

    def last(self):
        return L.objects.filter(employee=self.emp).latest("pk")


class ValidationTests(Base):
    def test_vacation_and_special_privilege_need_a_location_and_abroad_needs_a_place(self):
        for code in ("VL", "SPL"):
            self.assertIn("location", self.errors(self.post(code)))
            self.assertIn("location_detail", self.errors(self.post(code, location="ABROAD")))
        self.assertRedirects(self.post("VL", location="ABROAD", location_detail="Singapore"),
                             reverse("leave:my_applications"))
        a = self.last()
        self.assertEqual((a.location, a.location_detail), ("ABROAD", "Singapore"))

    def test_sick_leave_needs_type_and_illness(self):
        e = self.errors(self.post("SL"))
        self.assertIn("sick_leave_type", e)
        self.assertIn("illness", e)
        self.post("SL", sick_leave_type="OUTPATIENT", illness="Influenza")
        self.assertEqual((self.last().sick_leave_type, self.last().illness), ("OUTPATIENT", "Influenza"))

    def test_special_leave_for_women_and_study_leave(self):
        self.assertIn("illness", self.errors(self.post("SPECIAL_WOMEN")))
        self.assertIn("study_purpose", self.errors(self.post("STUDY")))
        self.post("STUDY", study_purpose="BAR_BOARD")
        self.assertEqual(self.last().study_purpose, "BAR_BOARD")

    def test_wellness_leave_cannot_be_submitted_without_a_reason(self):
        self.assertIn("wellness_reason", self.errors(self.post("WELLNESS")))
        self.assertFalse(L.objects.filter(employee=self.emp).exists())
        self.post("WELLNESS", wellness_reason="GENERAL_BREAK")
        self.assertEqual(self.last().wellness_reason, "GENERAL_BREAK")

    def test_details_of_other_leave_types_are_not_kept(self):
        self.post("VL", location="PH", illness="should not stay", wellness_reason="MENTAL_HEALTH",
                  study_purpose="MASTERS", sick_leave_type="HOSPITAL")
        a = self.last()
        self.assertEqual((a.illness, a.wellness_reason, a.study_purpose, a.sick_leave_type), ("", "", "", ""))

    def test_monetization_needs_no_dates_and_requests_commutation(self):
        self.post("VL", location="PH", other_purpose="MONETIZATION", start_date="", end_date="",
                  number_of_days="10")
        a = self.last()
        self.assertEqual((a.start_date, a.end_date, a.commutation), (None, None, "REQUESTED"))
        self.assertEqual(a.dates_label, "Not applicable")
        # An ordinary leave still needs dates.
        self.assertIn("start_date", self.errors(self.post("VL", location="PH", start_date="", end_date="")))
        # HR can record it; the ledger uses the filing date.
        self.client.force_login(self.hr.user)
        self.client.post(reverse("leave:leave_action", args=[a.pk]), {"action": "record"})
        a.refresh_from_db()
        self.assertEqual(a.status, L.RECORDED)
        entry = LeaveCreditTransaction.objects.get(leave_application=a)
        self.assertEqual(entry.transaction_date, a.submitted_at.date())


class EditAndViewTests(Base):
    def test_filer_edits_a_submitted_application_and_values_stay(self):
        self.post("SL", sick_leave_type="HOSPITAL", illness="Dengue")
        a = self.last()
        r = self.post("SL", url=reverse("leave:leave_edit", args=[a.pk]), sick_leave_type="OUTPATIENT",
                      illness="Dengue fever, recovering", number_of_days="3", end_date="2026-11-04")
        self.assertRedirects(r, reverse("leave:leave_detail", args=[a.pk]))
        a.refresh_from_db()
        self.assertEqual((a.sick_leave_type, a.illness, a.number_of_days), ("OUTPATIENT", "Dengue fever, recovering", Decimal("3")))
        self.assertTrue(LeaveApplicationAction.objects.filter(application=a, action="edit").exists())
        page = self.client.get(reverse("leave:leave_detail", args=[a.pk])).content.decode()
        self.assertIn("Dengue fever, recovering", page)
        form_page = self.client.get(reverse("leave:leave_edit", args=[a.pk])).content.decode()
        self.assertIn('value="OUTPATIENT" checked', form_page.replace('checked=""', "checked").replace(' id="id_sick_leave_type_1"', ""))

    def test_only_the_filer_may_edit_and_not_after_it_is_recorded(self):
        self.post("WELLNESS", wellness_reason="PHYSICAL")
        a = self.last()
        self.client.force_login(self.hr.user)
        self.assertEqual(self.client.get(reverse("leave:leave_edit", args=[a.pk])).status_code, 403)
        self.client.post(reverse("leave:leave_action", args=[a.pk]), {"action": "record"})
        self.client.force_login(self.emp.user)
        r = self.client.get(reverse("leave:leave_edit", args=[a.pk]))
        self.assertRedirects(r, reverse("leave:leave_detail", args=[a.pk]))

    def test_returned_cosp_leave_is_resubmitted_from_the_first_step(self):
        cosp = make_employee("cscosp", "EMP-C3", date_hired=date(2026, 1, 1), employment_status="COSP")
        lab = Section.objects.get(name="Laboratory Section")
        cosp.sections.add(lab)
        sup = make_employee("cscsup", "EMP-C4", date_hired=date(2020, 1, 1))
        R.objects.create(employee=sup, role=R.SUPERVISOR, section=lab)
        a = L.objects.create(employee=cosp, leave_type=LeaveType.objects.get(code="COSP_LEAVE"),
                             start_date=date(2026, 11, 2), end_date=date(2026, 11, 2), number_of_days=1)
        self.client.force_login(sup.user)
        self.client.post(reverse("leave:leave_action", args=[a.pk]), {"action": "return", "notes": "Wrong date"})
        a.refresh_from_db()
        self.assertEqual(a.status, L.RETURNED)
        self.client.force_login(cosp.user)
        self.client.post(reverse("leave:leave_edit", args=[a.pk]), {
            "leave_type": a.leave_type_id, "start_date": "2026-11-03", "end_date": "2026-11-03", "number_of_days": "1"})
        a.refresh_from_db()
        self.assertEqual((a.status, a.start_date), (L.SUBMITTED, date(2026, 11, 3)))
        self.assertTrue(a.actions.filter(action="resubmit").exists())

    def test_old_applications_without_details_still_open_and_fill_the_form(self):
        a = L.objects.create(employee=self.emp, leave_type=self.t["VL"], start_date=date(2026, 9, 1),
                             end_date=date(2026, 9, 2), number_of_days=2)
        self.assertEqual(self.client.get(reverse("leave:leave_detail", args=[a.pk])).status_code, 200)
        ws = fill_csc_form6(a)["blank"]
        self.assertEqual(ws["A16"].value, "X")
        self.assertEqual(ws["R35"].value, "X")  # 6.D Not Requested
        self.assertIsNone(ws["R17"].value)
        self.assertIn("September 01, 2026", ws["B36"].value)


class PrintedFormTests(Base):
    def make(self, code, **kw):
        base = dict(start_date=date(2026, 11, 2), end_date=date(2026, 11, 3), number_of_days=Decimal("2.00"))
        base.update(kw)
        return L.objects.create(employee=self.emp, leave_type=self.t[code], **base)

    def test_section_6b_values_print_in_their_boxes(self):
        ws = fill_csc_form6(self.make("VL", location="ABROAD", location_detail="Tokyo, Japan"))["blank"]
        self.assertEqual((ws["R18"].value, ws["X18"].value.strip()), ("X", "Tokyo, Japan"))
        ws = fill_csc_form6(self.make("SL", sick_leave_type="HOSPITAL", illness="Pneumonia"))["blank"]
        self.assertEqual((ws["A18"].value, ws["R20"].value, ws["Z20"].value.strip()), ("X", "X", "Pneumonia"))
        ws = fill_csc_form6(self.make("SPECIAL_WOMEN", illness="Myomectomy"))["blank"]
        self.assertEqual(ws["V24"].value.strip(), "Myomectomy")
        ws = fill_csc_form6(self.make("STUDY", study_purpose="MASTERS"))["blank"]
        self.assertEqual(ws["R27"].value, "X")
        ws = fill_csc_form6(self.make("VL", location="PH", other_purpose="TERMINAL", commutation="REQUESTED",
                                      start_date=None, end_date=None))["blank"]
        self.assertEqual((ws["R31"].value, ws["R36"].value, ws["B36"].value), ("X", "X", "Not applicable"))

    def test_long_illness_continues_on_the_next_ruled_line(self):
        text = "Acute gastroenteritis with moderate dehydration, admitted for intravenous hydration"
        ws = fill_csc_form6(self.make("SL", sick_leave_type="OUTPATIENT", illness=text))["blank"]
        joined = (ws["Z21"].value.strip() + " " + ws["R22"].value.strip())
        self.assertEqual(joined, text)

    def test_wellness_reason_prints_on_the_others_line(self):
        ws = fill_csc_form6(self.make("WELLNESS", wellness_reason="MENTAL_HEALTH"))["blank"]
        self.assertEqual(ws["A30"].value, "X")
        self.assertEqual(ws["B31"].value, "Wellness Leave - Mental Health Care")
