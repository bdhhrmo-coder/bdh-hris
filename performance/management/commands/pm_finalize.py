from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Performance Management, daily: COSP periods are planned from the contracts and opened before they "
            "end (decision 1), results past the contest window become Final, deductions past their review window "
            "become Final, and development-plan check-ins overdue are marked missed (D11).")

    def handle(self, *args, **options):
        from performance import devplans, results, rules

        planned, opened = rules.auto_cosp_periods()
        n = results.finalize_due()
        m = devplans.auto_miss()
        self.stdout.write(f"{planned} COSP period(s) planned, {opened} opened; {n} result(s) final; "
                          f"{m} check-in date(s) marked missed.")
