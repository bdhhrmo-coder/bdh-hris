from django.db import transaction
from django.utils import timezone

from notifications.services import notify

from .models import Announcement, AnnouncementAction


def log(announcement, action, user, notes=""):
    AnnouncementAction.objects.create(announcement=announcement, action=action,
                                      resulting_status=announcement.status, notes=notes[:255], acted_by=user)


def archive_expired(today=None):
    """Published items whose expiry date has passed become Archived. Run
    whenever announcements are listed - no scheduled job needed."""
    today = today or timezone.localdate()
    for a in Announcement.objects.filter(status=Announcement.PUBLISHED, expiry_date__lt=today):
        a.status = Announcement.ARCHIVED
        a.save(update_fields=["status", "updated_at"])
        log(a, "archive", None, f"Archived automatically after the expiry date ({a.expiry_date:%b %d, %Y}).")


@transaction.atomic
def publish(announcement, user):
    announcement.status = Announcement.PUBLISHED
    announcement.published_at = timezone.now()
    announcement.save(update_fields=["status", "published_at", "updated_at"])
    log(announcement, "publish", user)
    notify(list(announcement.audience()),
           f"New {announcement.get_announcement_type_display()}: {announcement.title}",
           f"/announcements/{announcement.pk}/")


@transaction.atomic
def archive(announcement, user):
    announcement.status = Announcement.ARCHIVED
    announcement.save(update_fields=["status", "updated_at"])
    log(announcement, "archive", user)
