"""
Nightly: lapse unused CTO and pending CTO claims of employees whose last
day of service has passed (CLAUDE.md §7.1). Safe to run more than once.

    python manage.py cto_separation_lapse
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from cto.separation import run_separation_lapse


class Command(BaseCommand):
    help = "Lapse CTO credit and pending claims after an employee's last day of service."

    def handle(self, *args, **options):
        lines, claims = run_separation_lapse(timezone.localdate())
        self.stdout.write(self.style.SUCCESS(f"Lapsed {lines} credit line(s) and {claims} claim(s)."))
