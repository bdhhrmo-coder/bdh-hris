"""
Owner requests 2026-11-03:
  1. A return always carries a remark (leave, CTO, Exchange of Duty, Official
     Business/Time/Travel/OT); the filer's notification shows it.
  2. PDF conversion runs LibreOffice with its own profile folder, so it works
     under the Windows service account.
"""

from datetime import date
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from accounts.test_self_approval import R, person
from cto.models import CTOUsageApplication
from leave.models import LeaveApplication, LeaveType
from notifications.models import Notification
from official_requests.models import OfficialRequest

LAB = "Laboratory Section"


class ReturnRemarkTests(TestCase):
    def setUp(self):
        self.head = person("labhead4", [LAB], sup_sections=[LAB])
        self.staff = person("labstaff4", [LAB])
        self.c = Client()
        self.c.force_login(self.head.user)

    def test_leave_return_without_remark_changes_nothing(self):
        lt = LeaveType.objects.filter(requires_full_routing=True).first()
        app = LeaveApplication.objects.create(employee=self.staff, leave_type=lt, start_date=date(2026, 11, 20),
                                              end_date=date(2026, 11, 20), number_of_days=1)
        r = self.c.post(reverse("leave:leave_action", args=[app.pk]), {"action": "return", "notes": "  "}, follow=True)
        app.refresh_from_db()
        self.assertEqual(app.status, LeaveApplication.SUBMITTED)
        self.assertContains(r, "Please type a remark")
        self.c.post(reverse("leave:leave_action", args=[app.pk]), {"action": "return", "notes": "Attach the medical certificate"})
        app.refresh_from_db()
        self.assertEqual(app.status, LeaveApplication.RETURNED)
        msg = Notification.objects.filter(recipient=self.staff).latest("pk").message
        self.assertIn('Remark: "Attach the medical certificate"', msg)

    def test_cto_and_official_request_need_a_remark_too(self):
        cto = CTOUsageApplication.objects.create(employee=self.staff, start_date=date(2026, 11, 20),
                                                 end_date=date(2026, 11, 20), number_of_days=1)
        self.c.post(reverse("cto:cto_action", args=[cto.pk]), {"action": "return"})
        cto.refresh_from_db()
        self.assertEqual(cto.status, CTOUsageApplication.SUBMITTED)
        ob = OfficialRequest.objects.create(employee=self.staff, request_type=OfficialRequest.OFFICIAL_BUSINESS,
                                            start_date=date(2026, 11, 20), end_date=date(2026, 11, 20), purpose="T")
        self.c.post(reverse("official_requests:request_action", args=[ob.pk]), {"action": "return"})
        ob.refresh_from_db()
        self.assertEqual(ob.status, OfficialRequest.SUBMITTED)
        self.c.post(reverse("official_requests:request_action", args=[ob.pk]), {"action": "return", "notes": "Wrong date"})
        ob.refresh_from_db()
        self.assertEqual(ob.status, OfficialRequest.RETURNED)

    def test_reject_needs_a_remark_too(self):
        cto = CTOUsageApplication.objects.create(employee=self.staff, start_date=date(2026, 11, 20),
                                                 end_date=date(2026, 11, 20), number_of_days=1)
        self.c.post(reverse("cto:cto_action", args=[cto.pk]), {"action": "reject"})
        cto.refresh_from_db()
        self.assertEqual(cto.status, CTOUsageApplication.SUBMITTED)
        self.c.post(reverse("cto:cto_action", args=[cto.pk]), {"action": "reject", "notes": "Exceeds the monthly cap"})
        cto.refresh_from_db()
        self.assertEqual(cto.status, CTOUsageApplication.REJECTED)
        msg = Notification.objects.filter(recipient=self.staff).latest("pk").message
        self.assertIn('Remark: "Exceeds the monthly cap"', msg)

    def test_queue_shows_the_remark_box(self):
        lt = LeaveType.objects.filter(requires_full_routing=True).first()
        LeaveApplication.objects.create(employee=self.staff, leave_type=lt, start_date=date(2026, 11, 20),
                                        end_date=date(2026, 11, 20), number_of_days=1)
        r = self.c.get(reverse("leave:leave_queue"))
        self.assertContains(r, 'placeholder="Remark (required to return or reject)"')
        self.assertContains(r, ':disabled="!remark.trim()"')
        self.assertContains(r, 'x-data="{ remark: \'\' }"')
        self.assertNotContains(r, "\\'")


class PdfConvertProfileTests(TestCase):
    def test_libreoffice_gets_its_own_profile_folder(self):
        import openpyxl

        from leave import pdf_convert

        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            outdir = cmd[cmd.index("--outdir") + 1]
            open(f"{outdir}/form.pdf", "wb").write(b"%PDF-1.4 test")

            class Done:
                returncode, stdout, stderr = 0, b"", b""
            return Done()

        with patch.object(pdf_convert, "find_libreoffice", return_value="soffice"), \
                patch.object(pdf_convert.subprocess, "run", side_effect=fake_run):
            pdf = pdf_convert.workbook_to_pdf(openpyxl.Workbook(), "Test")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertTrue(any(a.startswith("-env:UserInstallation=file:") for a in seen["cmd"]))
