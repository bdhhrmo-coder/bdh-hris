from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Performance Management, daily: results past the contest window become Final, deductions past their "
            "review window become Final, and development-plan check-ins overdue are marked missed (D11).")

    def handle(self, *args, **options):
        from performance import devplans, results

        n = results.finalize_due()
        m = devplans.auto_miss()
        self.stdout.write(f"{n} result(s) final; {m} check-in date(s) marked missed.")
