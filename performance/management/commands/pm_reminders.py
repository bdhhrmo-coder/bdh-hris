from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Performance Management, daily: rater reminders and development-plan check-in reminders."

    def handle(self, *args, **options):
        from performance.monitoring import send_reminders

        forms, checks = send_reminders()
        self.stdout.write(f"{forms} rater reminder(s), {checks} check-in reminder(s).")
