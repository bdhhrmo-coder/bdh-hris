"""
Year-end CTO forfeiture sweep — CLAUDE.md §7: "CTO must be used within the
calendar year earned; auto-forfeited otherwise unless the Chief of Hospital
grants a documented exception."

See cto/balances.py's module docstring for why a whole-remaining-balance
sweep at each year-end is equivalent to a strict "only forfeit what was
earned in year Y" calculation, under the assumption that this command is
run every year without a gap.

Usage:
    python manage.py forfeit_expired_cto --year 2026 --actor-username hradmin
    python manage.py forfeit_expired_cto --year 2026 --actor-username hradmin --dry-run
    python manage.py forfeit_expired_cto --year 2026 --actor-username hradmin --exempt EMP-0007 --exempt EMP-0012

A Chief-of-Hospital-approved exception is handled by --exempt (repeatable,
takes an employee_id): that employee's balance is left untouched this run,
and --exempt-reason is recorded on a NOTE-only ledger entry of zero days so
the exception itself is auditable, not just invisible. Running this command
twice for the same year is safe: an employee whose balance was already
forfeited to zero produces no further transaction.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cto.balances import compute_available_cto_balance
from cto.models import CTOCreditTransaction
from cto.permissions import is_cto_eligible
from employees.models import Employee


class Command(BaseCommand):
    help = "Forfeit each eligible employee's remaining CTO balance as of December 31 of the given year."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True, help="Calendar year whose balances to forfeit.")
        parser.add_argument(
            "--actor-username", required=True,
            help="Username of the HR/System Administrator running this sweep (recorded on each ledger entry).",
        )
        parser.add_argument(
            "--exempt", action="append", default=[], dest="exempt_employee_ids",
            help="Employee ID to exclude from this run (COH-approved exception). Repeatable.",
        )
        parser.add_argument(
            "--exempt-reason", default="",
            help="Reason recorded for each --exempt employee (e.g. 'COH exception, memo dated ...').",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be forfeited without writing any ledger entries.",
        )

    def handle(self, *args, **options):
        year = options["year"]
        dry_run = options["dry_run"]
        exempt_ids = set(options["exempt_employee_ids"])
        exempt_reason = options["exempt_reason"]

        try:
            actor = User.objects.get(username=options["actor_username"])
        except User.DoesNotExist:
            raise CommandError(f"No user named '{options['actor_username']}'.")

        cutoff = date(year, 12, 31)
        forfeited_count = 0
        exempted_count = 0

        for employee in Employee.objects.all():
            if not is_cto_eligible(employee):
                continue  # Chief of Hospital — CTO not applicable (§7).

            if employee.employee_id in exempt_ids:
                exempted_count += 1
                self.stdout.write(f"EXEMPTED: {employee} — {exempt_reason or '(no reason given)'}")
                if not dry_run:
                    CTOCreditTransaction.objects.create(
                        employee=employee, transaction_type=CTOCreditTransaction.ADJUSTMENT,
                        days=Decimal("0"), transaction_date=cutoff, created_by=actor,
                        notes=f"COH exception, {year} forfeiture skipped: {exempt_reason or '(no reason given)'}",
                    )
                continue

            balance = compute_available_cto_balance(employee, as_of_date=cutoff)
            if balance <= 0:
                continue

            self.stdout.write(f"FORFEIT: {employee} — {balance} day(s) as of {cutoff}")
            forfeited_count += 1
            if not dry_run:
                with transaction.atomic():
                    CTOCreditTransaction.objects.create(
                        employee=employee, transaction_type=CTOCreditTransaction.FORFEITED,
                        days=-balance, transaction_date=cutoff, created_by=actor,
                        notes=f"Auto-forfeited unused CTO balance as of {cutoff} (CLAUDE.md §7).",
                    )

        verb = "Would forfeit" if dry_run else "Forfeited"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} balances for {forfeited_count} employee(s); {exempted_count} exempted."
        ))
