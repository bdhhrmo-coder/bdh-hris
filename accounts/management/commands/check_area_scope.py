"""Part C (2026-11-27): read-only check of the data that decides what each
approver may see. Changes nothing. Run on the test PC (or by ICTU on the live
server) with:  python manage.py check_area_scope

It lists employee IDs only (no names or other personal data)."""

from django.core.management.base import BaseCommand

from accounts.models import RoleAssignment as R
from employees.models import Employee


class Command(BaseCommand):
    help = "Read-only: Supervisor lines and employee sections/units that affect who sees which requests."

    def handle(self, *args, **options):
        out = self.stdout.write
        problems = 0

        wide = (R.objects.filter(R.in_force_q(), role=R.SUPERVISOR, section__isnull=True, unit__isnull=True)
                .select_related("employee"))
        out("1. Supervisor lines with no section and no unit (they act hospital-wide; must be fixed):")
        for ra in wide:
            out(f"   - line {ra.pk}: employee {ra.employee.employee_id}{' (OIC)' if ra.is_oic else ''}")
        out(f"   Total: {wide.count()}")
        problems += wide.count()

        staff = Employee.objects.filter(is_active=True, archived_at__isnull=True)
        no_section = staff.filter(sections__isnull=True).exclude(
            role_assignments__role__in=[R.CHIEF_OF_HOSPITAL, R.ADMINISTRATIVE_OFFICER]).distinct()
        out("2. Active employees with no section (no Supervisor sees their requests; they go to the AO):")
        for e in no_section:
            out(f"   - {e.employee_id}")
        out(f"   Total: {no_section.count()}")

        no_unit = staff.filter(sections__units__isnull=False, units__isnull=True).distinct()
        out("3. Employees in a section that has units, but in no unit (only the section head sees them):")
        for e in no_unit:
            out(f"   - {e.employee_id}")
        out(f"   Total: {no_unit.count()}")

        multi = [e for e in staff.prefetch_related("sections", "units")
                 if len(e.sections.all()) > 1 or len(e.units.all()) > 1]
        out("4. Employees in more than one section or unit (float staff; every head of those areas sees them):")
        for e in multi:
            out(f"   - {e.employee_id}")
        out(f"   Total: {len(multi)}")

        out("Nothing was changed." + (" Item 1 needs fixing." if problems else " No hospital-wide Supervisor lines."))
