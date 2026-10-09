"""
Announcements (Batch 4, item 4.3, 2026-10-09): Office Orders, Policies and
Activity notices shown on the homepage.

- Only the HR Administrator creates, publishes and archives them, encoding
  each from the signed original. The system displays the notice; the
  signed document remains the authority.
- Draft -> Published -> Archived. A published item is archived by hand or
  automatically once its expiry date has passed.
- Visibility: all staff, or only selected sections/units.
- Every change is logged in AnnouncementAction, which the Audit Log shows.
- Activities are notices only; scheduling stays in the HR Activity
  Scheduler and the L&D Tracker.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


def announcement_pdf_path(instance, filename):
    """Stored under a random name, never the uploaded one (CLAUDE.md §10)."""
    return f"announcements/{uuid.uuid4().hex}.pdf"


class Announcement(models.Model):
    OFFICE_ORDER = "OFFICE_ORDER"
    POLICY = "POLICY"
    ACTIVITY = "ACTIVITY"
    TYPE_CHOICES = [(OFFICE_ORDER, "Office Order"), (POLICY, "Policy"), (ACTIVITY, "Activity")]

    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PUBLISHED, "Published"), (ARCHIVED, "Archived")]

    ALL_STAFF = "ALL"
    SELECTED = "SELECTED"
    VISIBILITY_CHOICES = [(ALL_STAFF, "All staff"), (SELECTED, "Selected sections/units")]

    announcement_type = models.CharField("Type", max_length=15, choices=TYPE_CHOICES)
    title = models.CharField(max_length=200)
    reference_no = models.CharField("Reference no.", max_length=100, blank=True,
                                    help_text='e.g. "Office Order No. 028 s. 2026"')
    date_issued = models.DateField()
    effective_date = models.DateField()
    summary = models.TextField(help_text="Short summary. The signed original remains the authority.")
    expiry_date = models.DateField(null=True, blank=True,
                                   help_text="Optional. After this date the announcement is archived automatically.")
    pdf = models.FileField("Signed copy (PDF)", upload_to=announcement_pdf_path, blank=True, max_length=300)
    pdf_original_name = models.CharField(max_length=255, blank=True)
    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default=ALL_STAFF)
    sections = models.ManyToManyField("orgstructure.Section", blank=True, related_name="+")
    units = models.ManyToManyField("orgstructure.Unit", blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_issued", "-published_at", "-pk"]

    def __str__(self):
        return f"{self.get_announcement_type_display()}: {self.title}"

    def is_visible_to(self, employee):
        """Published items, and archived ones (staff can still open them,
        marked "No longer in effect" - owner decision 2026-10-09). Drafts
        are never shown to staff."""
        if self.status == self.DRAFT or employee is None:
            return False
        if self.visibility == self.ALL_STAFF:
            return True
        return (self.sections.filter(pk__in=employee.sections.values("pk")).exists()
                or self.units.filter(pk__in=employee.units.values("pk")).exists())

    @property
    def in_effect(self):
        return self.status == self.PUBLISHED

    @classmethod
    def visible_to(cls, employee, status=None):
        """Current (published) items by default; status=ARCHIVED for the
        Archive tab."""
        qs = cls.objects.filter(status=status or cls.PUBLISHED)
        if employee is None:
            return qs.none()
        return qs.filter(
            Q(visibility=cls.ALL_STAFF)
            | Q(sections__in=employee.sections.all())
            | Q(units__in=employee.units.all())
        ).distinct()

    def audience(self):
        """Active employees who can see it (for the publish notification)."""
        from employees.models import Employee

        qs = Employee.objects.filter(is_active=True, archived_at__isnull=True)
        if self.visibility == self.ALL_STAFF:
            return qs
        return qs.filter(Q(sections__in=self.sections.all()) | Q(units__in=self.units.all())).distinct()


class AnnouncementAction(models.Model):
    """Accountability log (create, edit, publish, archive). acted_by is empty
    only for the automatic archive on the expiry date."""

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=30)
    resulting_status = models.CharField(max_length=10, choices=Announcement.STATUS_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    acted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+")
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["acted_at", "pk"]

    def __str__(self):
        return f"{self.announcement} — {self.action}"
