from django.conf import settings
from django.db import models


class RoleAssignment(models.Model):
    """
    Ties an Employee to a Role. An employee can hold more than one role at
    once (e.g. HR Processor + Supervisor), so this is a separate table
    rather than a single field on Employee (CLAUDE.md Section 3).

    OIC coverage is ad-hoc delegated authority triggered by a Supervisor,
    not a separate role — it is represented here as an assignment of the
    SUPERVISOR/ADMINISTRATIVE_OFFICER/CHIEF_OF_HOSPITAL role with
    is_oic=True and delegated_by set, scoped to a section/unit.
    """

    EMPLOYEE = "EMPLOYEE"
    SUPERVISOR = "SUPERVISOR"
    HR_PROCESSOR = "HR_PROCESSOR"
    HR_ADMINISTRATOR = "HR_ADMINISTRATOR"
    ADMINISTRATIVE_OFFICER = "ADMINISTRATIVE_OFFICER"
    CHIEF_OF_HOSPITAL = "CHIEF_OF_HOSPITAL"
    SYSTEM_ADMINISTRATOR = "SYSTEM_ADMINISTRATOR"

    ROLE_CHOICES = [
        (EMPLOYEE, "Employee"),
        (SUPERVISOR, "Supervisor"),
        (HR_PROCESSOR, "HR Processor"),
        (HR_ADMINISTRATOR, "HR Administrator"),
        (ADMINISTRATIVE_OFFICER, "Administrative Officer (AO)"),
        (CHIEF_OF_HOSPITAL, "Chief of Hospital (COH)"),
        (SYSTEM_ADMINISTRATOR, "System Administrator"),
    ]

    # Roles that can hold ad-hoc OIC delegation, per CLAUDE.md / confirmed
    # Phase 1 decision: OIC extends to Supervisor, AO, and COH.
    OIC_ELIGIBLE_ROLES = {SUPERVISOR, ADMINISTRATIVE_OFFICER, CHIEF_OF_HOSPITAL}

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.CASCADE, related_name="role_assignments"
    )
    role = models.CharField(max_length=30, choices=ROLE_CHOICES)

    # Scope for Supervisor-type roles (a Supervisor may oversee a section,
    # or a unit within Nursing Service). Left blank for hospital-wide roles
    # such as HR Administrator, AO, COH, System Administrator.
    section = models.ForeignKey(
        "orgstructure.Section", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    unit = models.ForeignKey(
        "orgstructure.Unit", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    is_oic = models.BooleanField(
        default=False,
        help_text="True if this is ad-hoc OIC (Officer-in-Charge) delegated authority, "
        "not a permanent assignment.",
    )
    delegated_by = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="delegations_made",
        help_text="Supervisor who triggered this OIC delegation. Required when is_oic is True.",
    )

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(
        null=True, blank=True, help_text="Leave blank for an ongoing/permanent assignment."
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee", "role"]

    def __str__(self):
        label = dict(self.ROLE_CHOICES).get(self.role, self.role)
        if self.is_oic:
            label = f"{label} (OIC)"
        return f"{self.employee} — {label}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.is_oic and self.role not in self.OIC_ELIGIBLE_ROLES:
            raise ValidationError(
                "OIC delegation is only applicable to Supervisor, Administrative Officer, "
                "or Chief of Hospital roles."
            )
        if self.is_oic and not self.delegated_by_id:
            raise ValidationError("An OIC assignment must record who delegated it.")
