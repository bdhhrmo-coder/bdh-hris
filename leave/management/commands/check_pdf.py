"""
python manage.py check_pdf

One-line check for ICTU after installing or updating the server (added
2026-11-03): finds LibreOffice and converts a small test form to PDF the same
way the printable forms do. Prints OK, or the exact reason it failed.

Run it from the same account the HRIS service runs under when possible; the
private profile used by leave/pdf_convert.py makes the service account work
too.
"""

import openpyxl
from django.core.management.base import BaseCommand, CommandError

from leave.pdf_convert import find_libreoffice, workbook_to_pdf


class Command(BaseCommand):
    help = "Check that the printable forms can be converted to PDF on this server."

    def handle(self, *args, **options):
        try:
            exe = find_libreoffice()
        except RuntimeError as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"LibreOffice found: {exe}")
        wb = openpyxl.Workbook()
        wb.active["A1"] = "BDH HRIS PDF check"
        try:
            pdf = workbook_to_pdf(wb, "Test form")
        except RuntimeError as exc:
            raise CommandError(str(exc))
        if not pdf.startswith(b"%PDF"):
            raise CommandError("LibreOffice ran but did not produce a PDF file.")
        self.stdout.write(self.style.SUCCESS(f"OK - printable forms can be converted to PDF ({len(pdf):,} bytes)."))
