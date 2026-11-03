"""
CTO year-end (CLAUDE.md §7.1), scheduled DAILY. Does nothing new until
Dec 17 each year; then, for credit whose Dec 15 use deadline has passed:
carries it over if the COH approved a reason, holds it while a reason
awaits the COH, and forfeits the rest. Also closes out stale credit from
earlier years (e.g. after a separation status is cleared). Separating
employees are skipped. Safe to run every day; self-heals if a day is missed.

    python manage.py cto_year_end
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from cto.carryover import run_year_end


class Command(BaseCommand):
    help = "CTO year-end: carry over, hold pending, or forfeit credit past its use deadline (run daily)."

    def handle(self, *args, **options):
        counts = run_year_end(timezone.localdate())
        self.stdout.write(self.style.SUCCESS(
            f"CTO year-end: {counts['carried']} line(s) carried over, {counts['pending']} awaiting the COH, "
            f"{counts['forfeited']} forfeited."
        ))
