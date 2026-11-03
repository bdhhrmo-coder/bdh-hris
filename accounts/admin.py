"""
User Accounts and Roles - role lines (Django admin, System Administrator).

There is no "Chief Nurse" or "Unit Head" role: both are a SUPERVISOR line,
and the scope decides who they supervise (owner decisions 2026-10-26):
  Chief Nurse / any section head  -> Supervisor + Section (Unit blank)
  Unit head (e.g. OR/DR)           -> Supervisor + Unit (Section blank)
  HR, AO, COH, ICTU, System Admin  -> Section and Unit both blank
The form below enforces exactly that, so a Supervisor line can never be
saved without a scope (which would make that person a Supervisor of the
whole hospital).
"""

from django import forms
from django.contrib import admin

from .models import RoleAssignment

SCOPE_HELP = (
    "<b>Chief Nurse or any section head:</b> role Supervisor + Section (leave Unit blank) - "
    "supervises the whole section, including its unit heads.<br>"
    "<b>Unit head (e.g. OR/DR, ER):</b> role Supervisor + Unit (leave Section blank) - supervises that unit only.<br>"
    "<b>HR Processor, HR Administrator, AO, COH, ICTU Staff, System Administrator, Employee:</b> "
    "leave Section and Unit blank (hospital-wide).<br>"
    "A person who is also a regular employee keeps their separate Employee line."
)


ROLE_LABELS = dict(RoleAssignment.ROLE_CHOICES)


class RoleAssignmentForm(forms.ModelForm):
    # Safeguard (owner decision 2026-11-03, Option B): changing the role on an
    # EXISTING line replaces that role; it does not add one. The person must
    # tick this box to confirm; otherwise they are told to add a new line.
    confirm_role_change = forms.BooleanField(
        required=False, label="Confirm role change",
        help_text="Tick only if you mean to REPLACE this line's role. "
                  "To give an additional role, add a new line instead.")

    class Meta:
        model = RoleAssignment
        fields = "__all__"

    def clean(self):
        data = super().clean()
        role, section, unit = data.get("role"), data.get("section"), data.get("unit")
        old_role = self.instance.role if self.instance.pk else None
        if old_role and role and role != old_role and not data.get("confirm_role_change"):
            raise forms.ValidationError(
                f"This replaces {ROLE_LABELS.get(old_role, old_role)} with {ROLE_LABELS.get(role, role)}. "
                "To give an additional role, add a new line instead. If you really mean to replace it, "
                "tick \"Confirm role change\" and save again.")
        if role == RoleAssignment.SUPERVISOR:
            if not section and not unit:
                raise forms.ValidationError(
                    "A Supervisor needs a scope: choose a Section (Chief Nurse / section head) "
                    "or a Unit (unit head). Without one, the person would supervise the whole hospital.")
            if section and unit:
                raise forms.ValidationError(
                    "Choose either a Section (whole section) or a Unit (that unit only), not both.")
        elif role and (section or unit):
            raise forms.ValidationError(
                f"{dict(RoleAssignment.ROLE_CHOICES).get(role, role)} is hospital-wide: "
                "leave Section and Unit blank.")
        return data


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    form = RoleAssignmentForm
    list_display = ("employee", "role", "scope", "is_oic", "start_date", "end_date", "is_active")
    list_filter = ("role", "section", "unit", "is_oic", "is_active")
    search_fields = ("employee__surname", "employee__first_name", "employee__employee_id")
    fieldsets = (
        (None, {"fields": ("employee", "role")}),
        ("Scope (Supervisor only)", {"fields": ("section", "unit"), "description": SCOPE_HELP}),
        ("Dates", {"fields": ("start_date", "end_date", "is_active")}),
        ("Change the role on this line", {"fields": ("confirm_role_change",)}),
        ("Officer-in-Charge (set by the OIC screen; do not edit here)",
         {"fields": ("is_oic", "delegated_by"), "classes": ("collapse",)}),
    )

    def get_fieldsets(self, request, obj=None):
        sets = super().get_fieldsets(request, obj)
        if obj is None:  # a new line: nothing to replace
            sets = [fs for fs in sets if "confirm_role_change" not in fs[1]["fields"]]
        return sets

    @admin.display(description="Scope")
    def scope(self, obj):
        if obj.unit_id:
            return f"Unit only: {obj.unit.name} ({obj.unit.section.name})"
        if obj.section_id:
            return f"Whole section: {obj.section.name}"
        return "Hospital-wide"


class RoleAssignmentInline(admin.TabularInline):
    """All of one person's role lines on their employee page (owner decision
    2026-11-03, Option B). One role per line: add a line for each extra role."""

    model = RoleAssignment
    fk_name = "employee"
    form = RoleAssignmentForm
    extra = 0
    verbose_name = "role line"
    verbose_name_plural = ("Roles - one role per line. To give another role, click "
                           "\"Add another role line\"; do not change an existing line.")
    fields = ("role", "section", "unit", "start_date", "end_date", "is_active", "is_oic", "confirm_role_change")
    readonly_fields = ("is_oic",)


# Header of the System Administration pages (owner request, 2026-10-26: the
# server showed Django's default "Django administration" look). "View site"
# goes to the HRIS home page.
admin.site.site_header = "BDH HRIS - System Administration (user accounts and roles)"
admin.site.site_title = "BDH HRIS System Administration"
admin.site.index_title = "User accounts, employee records and roles"
admin.site.site_url = "/hris/home/"
