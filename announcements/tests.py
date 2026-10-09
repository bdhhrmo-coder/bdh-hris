"""Batch 4 item 4.3: announcements."""

import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment
from auditlog.aggregation import audit_rows
from employees.models import Employee
from notifications.models import Notification
from orgstructure.models import Section

from . import services
from .models import Announcement

TODAY = timezone.localdate()
MEDIA = tempfile.mkdtemp()


def emp(username, section=None, role=None):
    user = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(), first_name="T")
    if section:
        e.sections.add(section)
    if role:
        RoleAssignment.objects.create(employee=e, role=role)
    return e


@override_settings(MEDIA_ROOT=MEDIA)
class AnnouncementTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.lab = Section.objects.get(name="Laboratory Section")
        self.pharm = Section.objects.get(name="Pharmacy Section")
        self.hradmin = emp("annhr", role=RoleAssignment.HR_ADMINISTRATOR)
        self.proc = emp("annproc", role=RoleAssignment.HR_PROCESSOR)
        self.labber = emp("annlab", self.lab)
        self.pharmer = emp("annpharm", self.pharm)

    def post_new(self, who=None, **extra):
        self.client.force_login((who or self.hradmin).user)
        data = {"announcement_type": "OFFICE_ORDER", "title": "Office hours", "reference_no": "Office Order No. 30 s. 2026",
                "date_issued": TODAY.isoformat(), "effective_date": TODAY.isoformat(), "summary": "New office hours.",
                "visibility": "ALL"}
        data.update(extra)
        return self.client.post(reverse("announcements:create"), data)

    def test_only_hr_administrator_manages(self):
        self.assertEqual(self.post_new(self.proc).status_code, 403)
        self.client.force_login(self.labber.user)
        self.assertEqual(self.client.get(reverse("announcements:manage")).status_code, 403)
        self.post_new()
        self.assertEqual(Announcement.objects.get().status, Announcement.DRAFT)

    def test_draft_invisible_then_published_visible_and_notified(self):
        self.post_new()
        a = Announcement.objects.get()
        self.client.force_login(self.labber.user)
        self.assertNotContains(self.client.get(reverse("homepage:home")), "Office hours")
        self.assertEqual(self.client.get(reverse("announcements:detail", args=[a.pk])).status_code, 404)
        self.client.force_login(self.hradmin.user)
        self.client.post(reverse("announcements:publish", args=[a.pk]))
        self.client.force_login(self.labber.user)
        self.assertContains(self.client.get(reverse("homepage:home")), "Office hours")
        self.assertTrue(Notification.objects.filter(recipient=self.labber, message__contains="Office hours").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.pharmer).exists())

    def test_section_visibility(self):
        self.post_new(visibility="SELECTED", sections=[self.lab.pk], title="Lab only")
        a = Announcement.objects.get()
        services.publish(a, self.hradmin.user)
        self.assertEqual(list(Announcement.visible_to(self.labber)), [a])
        self.assertEqual(list(Announcement.visible_to(self.pharmer)), [])
        self.assertFalse(Notification.objects.filter(recipient=self.pharmer).exists())
        self.client.force_login(self.pharmer.user)
        self.assertEqual(self.client.get(reverse("announcements:detail", args=[a.pk])).status_code, 404)

    def test_selected_needs_a_section(self):
        r = self.post_new(visibility="SELECTED")
        self.assertContains(r, "Choose at least one section")
        self.assertFalse(Announcement.objects.exists())

    def test_filter_by_type(self):
        for t, title in (("POLICY", "Dress code"), ("ACTIVITY", "Fun run")):
            self.post_new(announcement_type=t, title=title)
        for a in Announcement.objects.all():
            services.publish(a, self.hradmin.user)
        self.client.force_login(self.labber.user)
        page = self.client.get(reverse("homepage:home") + "?type=POLICY").content.decode()
        self.assertIn("Dress code", page)
        self.assertNotIn("Fun run", page)

    def test_expired_items_archive_themselves(self):
        self.post_new(expiry_date=(TODAY - timedelta(days=1)).isoformat(),
                      date_issued=(TODAY - timedelta(days=5)).isoformat(),
                      effective_date=(TODAY - timedelta(days=5)).isoformat())
        a = Announcement.objects.get()
        services.publish(a, self.hradmin.user)
        self.client.force_login(self.labber.user)
        self.assertNotContains(self.client.get(reverse("homepage:home")), "Office hours")
        a.refresh_from_db()
        self.assertEqual(a.status, Announcement.ARCHIVED)
        self.assertEqual(a.actions.last().notes[:20], "Archived automatical")

    def test_pdf_only_and_download_respects_visibility(self):
        bad = SimpleUploadedFile("photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50, content_type="image/png")
        r = self.post_new(pdf=bad)
        self.assertFalse(Announcement.objects.exists())
        good = SimpleUploadedFile("Signed OO 30.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        self.post_new(pdf=good, visibility="SELECTED", sections=[self.lab.pk])
        a = Announcement.objects.get()
        self.assertEqual(a.pdf_original_name, "Signed OO 30.pdf")
        self.assertNotIn("Signed OO 30", a.pdf.name)  # stored under a random name
        services.publish(a, self.hradmin.user)
        self.client.force_login(self.labber.user)
        self.assertEqual(self.client.get(reverse("announcements:pdf", args=[a.pk])).status_code, 200)
        self.client.force_login(self.pharmer.user)
        self.assertEqual(self.client.get(reverse("announcements:pdf", args=[a.pk])).status_code, 404)

    def test_every_change_is_in_the_audit_log(self):
        self.post_new()
        a = Announcement.objects.get()
        self.client.force_login(self.hradmin.user)
        self.client.post(reverse("announcements:publish", args=[a.pk]))
        self.client.post(reverse("announcements:archive", args=[a.pk]))
        actions = [r["action"] for r in audit_rows(module="Announcements")]
        self.assertEqual(sorted(actions), ["archive", "create", "publish"])

    def test_published_cannot_be_edited(self):
        self.post_new()
        a = Announcement.objects.get()
        services.publish(a, self.hradmin.user)
        self.client.force_login(self.hradmin.user)
        r = self.client.post(reverse("announcements:edit", args=[a.pk]), {"title": "Changed"})
        self.assertRedirects(r, reverse("announcements:detail", args=[a.pk]), fetch_redirect_response=False)
        a.refresh_from_db()
        self.assertEqual(a.title, "Office hours")
