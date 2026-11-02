"""
Daily: on the dates in CTO_CARRYOVER_REMINDER_DATES (default Dec 1, Dec 10,
Dec 16), remind Supervisors/Unit Heads and HR of employees with at-risk CTO
and no carry-over reason, and the COH of reasons awaiting a decision. Does
nothing on other days.

    python manage.py cto_carryover_reminders
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from cto.carryover import send_reminders


class Command(BaseCommand):
    help = "Send CTO carry-over reminders (only on the configured dates)."

    def handle(self, *args, **options):
        summary = send_reminders(timezone.localdate())
        if summary is None:
            self.stdout.write("Not a reminder date; nothing sent.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Reminders sent: {summary}"))
