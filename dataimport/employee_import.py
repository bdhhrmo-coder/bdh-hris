"""
Employee master-list import (go-live, 2026-10-06).

HR fills the "Employees" sheet of the downloaded template; the system
checks every row and shows a preview; nothing is saved until HR confirms,
and nothing at all is saved if any row has an error.

Owner decisions (2026-10-06):
  - HR Administrator and HR Processor may run it.
  - A row whose Employee ID already exists UPDATES that employee. Blank
    cells never erase existing data. Every changed field is written to
    EmployeeEditHistory (the same audit trail as the edit screen).
  - New login accounts get a random temporary password, printed on a slip
    for HR to hand out, and the person must set their own password at
    first login (Employee.must_change_password).

Safety rules built in:
  - Username defaults to the Employee ID (staff can log in with either).
  - Roles: every employee gets "Employee". Extra roles in the file are only
    applied when an HR Administrator runs the import (CLAUDE.md §3: the HR
    Administrator manages delegated user access), and "System
    Administrator" can never be given by import. Roles are only ever added,
    never removed, by an import.
  - "Supervisor" is scoped to the employee's own section(s) in the file -
    never hospital-wide - so they only see their own staff's requests.
"""

import secrets
from dataclasses import dataclass, field

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from accounts.models import RoleAssignment
from employees.models import Employee, EmployeeEditHistory
from orgstructure.models import Section, Unit

from .common import parse_date, read_sheet, split_list, text

SHEET = "Employees"

# (key, header, required_for_new)
COLUMNS = [
    ("employee_id", "Employee ID*", True),
    ("surname", "Surname*", True),
    ("first_name", "First Name*", True),
    ("middle_name", "Middle Name", False),
    ("name_extension", "Name Extension", False),
    ("employment_status", "Employment Status*", True),
    ("date_hired", "Date Hired*", True),
    ("sections", "Section(s)*", True),
    ("units", "Unit(s)", False),
    ("position", "Position", False),
    ("salary_grade", "Salary Grade", False),
    ("item_plantilla_no", "Item/Plantilla No.", False),
    ("appointment_type", "Appointment Type", False),
    ("date_of_appointment", "Date of Appointment", False),
    ("original_appointment_date", "Original Appointment Date", False),
    ("shift_hours", "Shift Hours (8 or 12)", False),
    ("roles", "Additional Role(s)", False),
    ("username", "Username", False),
    ("date_of_birth", "Date of Birth", False),
    ("sex_at_birth", "Sex at Birth", False),
    ("civil_status", "Civil Status", False),
    ("sss_number", "SSS No.", False),
    ("pagibig_number", "Pag-IBIG No.", False),
    ("philhealth_number", "PhilHealth No.", False),
    ("tin_number", "TIN", False),
    ("gsis_number", "GSIS No.", False),
    ("residential_address", "Residential Address", False),
    ("permanent_address", "Permanent Address", False),
    ("telephone_mobile", "Telephone/Mobile", False),
    ("email", "Email", False),
]
TEXT_FIELDS = [
    "surname", "first_name", "middle_name", "name_extension", "position", "salary_grade", "item_plantilla_no",
    "appointment_type", "sss_number", "pagibig_number", "philhealth_number", "tin_number", "gsis_number",
    "residential_address", "permanent_address", "telephone_mobile", "email",
]
DATE_FIELDS = ["date_hired", "date_of_appointment", "original_appointment_date", "date_of_birth"]
CHOICE_FIELDS = {
    "employment_status": Employee.EMPLOYMENT_STATUS_CHOICES,
    "sex_at_birth": Employee.SEX_CHOICES,
    "civil_status": Employee.CIVIL_STATUS_CHOICES,
}
LABELS = {key: header.replace("*", "") for key, header, _ in COLUMNS}

# Roles an import may add (System Administrator is deliberately absent).
IMPORTABLE_ROLES = [r for r in RoleAssignment.ROLE_CHOICES
                    if r[0] not in (RoleAssignment.EMPLOYEE, RoleAssignment.SYSTEM_ADMINISTRATOR)]

PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def temporary_password():
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(10))


def _choice_value(raw, choices):
    """Accept either the stored code or the label, any capitalisation."""
    wanted = raw.strip().lower()
    for code, label in choices:
        if wanted in (str(code).lower(), label.lower()):
            return code
    return None


def _role_code(raw):
    wanted = raw.strip().lower()
    for code, label in IMPORTABLE_ROLES:
        short = label.split(" (")[0].lower()
        abbrev = label[label.find("(") + 1:label.find(")")].lower() if "(" in label else None
        if wanted in (code.lower(), label.lower(), short, abbrev, code.replace("_", " ").lower()):
            return code
    return None


@dataclass
class RowPlan:
    row: int
    employee_id: str
    name: str
    action: str = "new"                 # "new" | "update" | "no change"
    changes: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)
    units: list = field(default_factory=list)
    roles: list = field(default_factory=list)
    username: str = ""
    creates_login: bool = False


def build_plan(uploaded_file, actor_employee):
    """Read and check every row. Returns a list of RowPlan (errors inside
    each). Raises common.SheetError when the file itself is unusable."""
    rows = read_sheet(uploaded_file, SHEET, [(k, h) for k, h, _ in COLUMNS])
    can_assign_roles = actor_employee is not None and actor_employee.has_role(RoleAssignment.HR_ADMINISTRATOR)
    sections_by_name = {s.name.lower(): s for s in Section.objects.filter(is_active=True)}
    units = list(Unit.objects.filter(is_active=True).select_related("section"))

    plans, seen_ids, seen_usernames = [], {}, {}
    for row_no, raw in rows:
        eid = text(raw["employee_id"])
        name = f'{text(raw["surname"])}, {text(raw["first_name"])}'.strip(", ")
        p = RowPlan(row=row_no, employee_id=eid, name=name)
        plans.append(p)

        if not eid:
            p.errors.append("Employee ID is required.")
            continue
        if eid.lower() in seen_ids:
            p.errors.append(f"Employee ID {eid} is also on row {seen_ids[eid.lower()]}.")
        seen_ids[eid.lower()] = row_no

        existing = Employee.objects.filter(employee_id__iexact=eid).select_related("user").first()
        p.action = "update" if existing else "new"

        # -- plain values ------------------------------------------------
        for key in TEXT_FIELDS:
            value = text(raw[key])
            if value:
                p.values[key] = value
        if "email" in p.values:
            try:
                validate_email(p.values["email"])
            except ValidationError:
                p.errors.append(f'Email "{p.values["email"]}" is not valid.')
        bad_keys = set()
        for key in DATE_FIELDS:
            value, err = parse_date(raw[key])
            if err:
                bad_keys.add(key)
                p.errors.append(f"{LABELS[key]}: {err}.")
            elif value:
                p.values[key] = value
        for key, choices in CHOICE_FIELDS.items():
            value = text(raw[key])
            if value:
                code = _choice_value(value, choices)
                if code is None:
                    bad_keys.add(key)
                    allowed = ", ".join(label for _, label in choices)
                    p.errors.append(f'{LABELS[key]} "{value}" is not one of: {allowed}.')
                else:
                    p.values[key] = code
        shift = text(raw["shift_hours"])
        if shift:
            if shift not in ("8", "12"):
                p.errors.append(f'Shift Hours must be 8 or 12, not "{shift}".')
            else:
                p.values["shift_hours"] = int(shift)

        # -- required for a new employee ---------------------------------
        if p.action == "new":
            for key, header, required in COLUMNS:
                if (required and key not in ("sections", "employee_id")
                        and key not in p.values and key not in bad_keys):
                    p.errors.append(f"{LABELS[key]} is required for a new employee.")

        # -- sections, units, roles --------------------------------------
        for sname in split_list(raw["sections"]):
            section = sections_by_name.get(sname.lower())
            if section is None:
                p.errors.append(f'Section "{sname}" not found (see the "Sections and Units" sheet).')
            else:
                p.sections.append(section)
        if p.action == "new" and not p.sections and not split_list(raw["sections"]):
            p.errors.append("Section(s) is required for a new employee.")
        for uname in split_list(raw["units"]):
            matches = [u for u in units if u.name.lower() == uname.lower()]
            if len(matches) != 1:
                p.errors.append(f'Unit "{uname}" not found (see the "Sections and Units" sheet).')
            else:
                p.units.append(matches[0])
        for rname in split_list(raw["roles"]):
            code = _role_code(rname)
            if code is None:
                if rname.strip().lower() in ("system administrator", "system_administrator"):
                    p.errors.append("System Administrator cannot be given by import.")
                elif rname.strip().lower() != "employee":
                    p.errors.append(f'Role "{rname}" is not recognised.')
                continue
            p.roles.append(code)
        if p.roles and not can_assign_roles:
            p.errors.append("Only an HR Administrator may assign roles by import. Leave Additional Role(s) blank.")
        if RoleAssignment.SUPERVISOR in p.roles and not (p.sections or (existing and existing.sections.exists())):
            p.errors.append("A Supervisor needs at least one Section (they supervise that section).")

        # -- login account -----------------------------------------------
        username = text(raw["username"]) or eid
        p.username = username
        if existing is None or existing.user is None:
            p.creates_login = True
            if username.lower() in seen_usernames:
                p.errors.append(f'Username "{username}" is also on row {seen_usernames[username.lower()]}.')
            elif User.objects.filter(username__iexact=username).exists():
                p.errors.append(f'Username "{username}" is already used by another account.')
            seen_usernames[username.lower()] = row_no

        # -- what will change --------------------------------------------
        if existing is None:
            p.changes = ["New employee and login account"]
        else:
            for key, value in p.values.items():
                if getattr(existing, key) != value:
                    p.changes.append(LABELS.get(key, key))
            if p.sections and set(p.sections) != set(existing.sections.all()):
                p.changes.append("Section(s)")
            if p.units and set(p.units) != set(existing.units.all()):
                p.changes.append("Unit(s)")
            new_roles = [r for r in p.roles if not existing.has_role(r)]
            if new_roles:
                p.changes.append("Role(s) added")
            if p.creates_login:
                p.changes.append("New login account")
            if not p.changes:
                p.action = "no change"
    return plans


def _display(value):
    if value is None:
        return ""
    return str(value)


def _assign_roles(employee, roles, sections):
    RoleAssignment.objects.get_or_create(employee=employee, role=RoleAssignment.EMPLOYEE, section=None, unit=None,
                                         defaults={"is_active": True})
    for role in roles:
        if role == RoleAssignment.SUPERVISOR:
            for section in sections or employee.sections.all():
                RoleAssignment.objects.get_or_create(employee=employee, role=role, section=section, unit=None,
                                                     is_oic=False, defaults={"is_active": True})
        elif not employee.has_role(role):
            RoleAssignment.objects.create(employee=employee, role=role)


def _create_login(employee, username, credentials):
    password = temporary_password()
    user = User.objects.create_user(username=username, password=password,
                                    first_name=employee.first_name, last_name=employee.surname)
    employee.user = user
    employee.must_change_password = True
    employee.save(update_fields=["user", "must_change_password", "updated_at"])
    credentials.append({
        "employee_id": employee.employee_id, "name": employee.full_name, "position": employee.position,
        "username": username, "password": password,
        "sections": ", ".join(s.name for s in employee.sections.all()),
    })


@transaction.atomic
def apply_plan(plans, actor_user, source_name):
    """Save a plan that has NO errors. Returns (created, updated, credentials)."""
    assert not any(p.errors for p in plans), "apply_plan called with errors"
    created = updated = 0
    credentials = []
    reason = f"Employee import ({source_name})"
    for p in plans:
        if p.action == "no change":
            continue
        if p.action == "new":
            emp = Employee.objects.create(employee_id=p.employee_id, **p.values)
            emp.sections.set(p.sections)
            emp.units.set(p.units)
            _assign_roles(emp, p.roles, p.sections)
            _create_login(emp, p.username, credentials)
            created += 1
            continue

        emp = Employee.objects.select_for_update().get(employee_id__iexact=p.employee_id)
        history = []
        for key, value in p.values.items():
            old = getattr(emp, key)
            if old != value:
                history.append(EmployeeEditHistory(
                    employee=emp, field_name=key, field_label=LABELS.get(key, key),
                    old_value=_display(old), new_value=_display(value), reason=reason, changed_by=actor_user,
                ))
                setattr(emp, key, value)
        emp.save()
        for attr, new_items, label in (("sections", p.sections, "Section(s)"), ("units", p.units, "Unit(s)")):
            current = set(getattr(emp, attr).all())
            if new_items and set(new_items) != current:
                history.append(EmployeeEditHistory(
                    employee=emp, field_name=attr, field_label=label,
                    old_value="; ".join(sorted(x.name for x in current)),
                    new_value="; ".join(sorted(x.name for x in new_items)), reason=reason, changed_by=actor_user,
                ))
                getattr(emp, attr).set(new_items)
        EmployeeEditHistory.objects.bulk_create(history)
        _assign_roles(emp, p.roles, p.sections)
        if emp.user is None:
            _create_login(emp, p.username, credentials)
        updated += 1
    return created, updated, credentials
