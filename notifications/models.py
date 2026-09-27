from django.db import models


class Notification(models.Model):
    """
    One in-app notification for one employee.

    CLAUDE.md §11: in-app notifications are required now; email is
    deferred until BDH's GovMail SMTP setup is confirmed (§2). This model,
    services.notify(), and the list view below are the whole of Phase 10
    for now — a single, generic notification inbox that every routed
    request type (leave, CTO, exchange, attendance correction, official
    requests) writes to at its status-change points, rather than each app
    building its own notification mechanism.

    When GovMail SMTP is confirmed and email is added, the natural place
    to add it is inside notifications.services.notify() (send an email in
    addition to creating the row) — no calling app needs to change.
    """

    recipient = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="notifications"
    )
    message = models.CharField(max_length=255)
    url = models.CharField(
        max_length=255, blank=True, help_text="Where clicking this notification should take the recipient."
    )
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"])]

    def __str__(self):
        return f"{self.recipient} — {self.message}"
