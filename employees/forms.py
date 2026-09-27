from django import forms

from accounts.models import RoleAssignment

from .models import Employee
from .permissions import SYSTEM_ADMIN_ONLY_FIELDS, is_self_record_locked


class EmployeeCreateForm(forms.ModelForm):
    """
    Minimal "new hire shell" record: System Administrator only, since it has
    to assign the unique employee_id (a System-Administrator-only field —
    same rule as the edit screen). An HR Administrator fills in the rest of
    the master data (PDS fields, position, etc.) afterward through the
    ordinary Employee edit screen.
    """

    class Meta:
        model = Employee
        fields = ["employee_id", "surname", "first_name", "middle_name", "name_extension", "is_active"]


class EmployeeForm(forms.ModelForm):
    """
    Employee master-data form with role-based field locking.

    - employee_id / is_active: enabled only for a System Administrator.
    - Everything else: enabled only for an HR Administrator.
    - If the acting employee is an HR Administrator editing their own
      record, the whole form is disabled (read-only) — see
      employees.permissions.is_self_record_locked.

    `disabled=True` is used rather than just hiding the field in the
    template: Django forms ignore submitted data for a disabled field and
    fall back to the field's initial value, so this holds even if someone
    tampers with the rendered HTML.

    The `reason` field is required whenever editing an EXISTING employee
    (not required when creating a new record), and is not a model field —
    it is written to EmployeeEditHistory alongside the before/after values
    of whatever actually changed (see views.py).
    """

    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Why is this change being made?"}),
        label="Reason for change",
        help_text="Required for edits to an existing employee record.",
    )

    class Meta:
        model = Employee
        fields = [
            "employee_id",
            "is_active",
            "surname",
            "first_name",
            "middle_name",
            "name_extension",
            "date_of_birth",
            "sex_at_birth",
            "civil_status",
            "sss_number",
            "pagibig_number",
            "philhealth_number",
            "tin_number",
            "gsis_number",
            "residential_address",
            "permanent_address",
            "telephone_mobile",
            "email",
            "position",
            "item_plantilla_no",
            "salary_grade",
            "appointment_type",
            "employment_status",
            "date_of_appointment",
            "date_hired",
            "original_appointment_date",
            "sections",
            "units",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "date_of_appointment": forms.DateInput(attrs={"type": "date"}),
            "date_hired": forms.DateInput(attrs={"type": "date"}),
            "original_appointment_date": forms.DateInput(attrs={"type": "date"}),
            "residential_address": forms.Textarea(attrs={"rows": 2}),
            "permanent_address": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, acting_employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.acting_employee = acting_employee
        self.is_editing_existing = self.instance.pk is not None

        is_sysadmin = bool(
            acting_employee and acting_employee.has_role(RoleAssignment.SYSTEM_ADMINISTRATOR)
        )
        is_hr_admin = bool(
            acting_employee and acting_employee.has_role(RoleAssignment.HR_ADMINISTRATOR)
        )
        self_locked = bool(
            acting_employee
            and self.is_editing_existing
            and is_self_record_locked(acting_employee, self.instance)
        )

        for name in self.fields:
            if name == "reason":
                continue
            if self_locked:
                self.fields[name].disabled = True
            elif name in SYSTEM_ADMIN_ONLY_FIELDS:
                self.fields[name].disabled = not is_sysadmin
            else:
                self.fields[name].disabled = not is_hr_admin

        if self.is_editing_existing and not self_locked:
            self.fields["reason"].required = True
        else:
            # No history to log yet for a brand-new record, and a fully
            # self-locked form can't be submitted at all.
            self.fields["reason"].widget = forms.HiddenInput()

        # Snapshot the "before" state now, at construction time. Validating
        # a ModelForm (is_valid() -> full_clean() -> _post_clean()) mutates
        # self.instance in place with the submitted values *before* save()
        # is ever called, so reading self.instance for "old" values after
        # is_valid() would just return the new values. Capturing it here,
        # before validation runs, is what makes the before/after audit
        # trail possible.
        self._old_snapshot = {}
        self._old_m2m_snapshot = {}
        if self.is_editing_existing:
            for name in self.fields:
                if name in ("reason", "sections", "units"):
                    continue
                self._old_snapshot[name] = getattr(self.instance, name, None)
            for name in ("sections", "units"):
                if name in self.fields:
                    self._old_m2m_snapshot[name] = list(getattr(self.instance, name).all())

    def changed_editable_data(self):
        """
        Fields that actually changed value, restricted to fields this
        acting employee was allowed to edit (defence in depth alongside
        `disabled`). Returns {field_name: (old_value, new_value)}.
        Must be called after is_valid() has returned True.
        """
        changes = {}
        for name in self.fields:
            if name == "reason" or self.fields[name].disabled:
                continue
            if name not in self.changed_data:
                continue
            new = self.cleaned_data.get(name)
            if name in ("sections", "units"):
                old_objs = self._old_m2m_snapshot.get(name, [])
                old_display = ", ".join(str(o) for o in old_objs)
                new_display = ", ".join(str(o) for o in new) if new is not None else ""
                if old_display != new_display:
                    changes[name] = (old_display, new_display)
            else:
                old = self._old_snapshot.get(name)
                if old != new:
                    changes[name] = (old, new)
        return changes
