from django.conf import settings
from django.db import models


class Employee(models.Model):
    """
    Employee master record (CLAUDE.md Section 5). Every system login belongs
    to an Employee — including the System Administrator — so there is no
    User-without-Employee case.

    Field-level edit permissions (enforced in employees/forms.py and
    employees/views.py, not here):
      - employee_id and is_active: System Administrator only.
      - Every other field: HR Administrator (or authorized HR Processor),
        EXCEPT an HR Administrator may not edit their own record — it is
        read-only to them, per CLAUDE.md 6.4 (closes the self-approval
        bypass). Another HR Administrator must make the edit.
    """

    SEX_MALE = "M"
    SEX_FEMALE = "F"
    SEX_CHOICES = [(SEX_MALE, "Male"), (SEX_FEMALE, "Female")]

    CIVIL_STATUS_CHOICES = [
        ("SINGLE", "Single"),
        ("MARRIED", "Married"),
        ("WIDOWED", "Widowed"),
        ("SEPARATED", "Separated"),
        ("DIVORCED", "Divorced"),
    ]

    EMPLOYMENT_STATUS_REGULAR = "REGULAR"
    EMPLOYMENT_STATUS_COSP = "COSP"
    EMPLOYMENT_STATUS_CHOICES = [
        (EMPLOYMENT_STATUS_REGULAR, "Regular"),
        (EMPLOYMENT_STATUS_COSP, "COSP"),
    ]

    # --- Login link -----------------------------------------------------
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee",
        null=True,
        blank=True,
        help_text="System login account for this employee.",
    )

    # --- 5.1 Personal Information (PDS-aligned, CS Form No. 212) --------
    surname = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    name_extension = models.CharField(max_length=10, blank=True, help_text="Jr., Sr., III, etc.")

    date_of_birth = models.DateField(null=True, blank=True)
    sex_at_birth = models.CharField(max_length=1, choices=SEX_CHOICES, blank=True)
    civil_status = models.CharField(max_length=10, choices=CIVIL_STATUS_CHOICES, blank=True)

    sss_number = models.CharField("SSS Number", max_length=20, blank=True)
    pagibig_number = models.CharField("Pag-IBIG Number", max_length=20, blank=True)
    philhealth_number = models.CharField("PhilHealth Number", max_length=20, blank=True)
    tin_number = models.CharField("TIN", max_length=20, blank=True)
    gsis_number = models.CharField("GSIS Number", max_length=20, blank=True)

    residential_address = models.TextField(blank=True)
    permanent_address = models.TextField(blank=True)

    # Self-service editable (subject to Administrator approval) per 6.4:
    telephone_mobile = models.CharField("Telephone/Mobile", max_length=50, blank=True)
    email = models.EmailField(blank=True)

    # --- 5.3 Government/Employment Information ---------------------------
    employee_id = models.CharField(
        max_length=20, unique=True, help_text="System Administrator only."
    )
    position = models.CharField(max_length=150, blank=True)
    item_plantilla_no = models.CharField("Item/Plantilla No.", max_length=50, blank=True)
    salary_grade = models.CharField(max_length=10, blank=True)
    appointment_type = models.CharField(max_length=100, blank=True)
    employment_status = models.CharField(
        max_length=10, choices=EMPLOYMENT_STATUS_CHOICES, default=EMPLOYMENT_STATUS_REGULAR
    )
    date_of_appointment = models.DateField(null=True, blank=True)
    date_hired = models.DateField(null=True, blank=True)
    original_appointment_date = models.DateField(null=True, blank=True)

    sections = models.ManyToManyField(
        "orgstructure.Section",
        blank=True,
        related_name="employees",
        help_text="An employee may be attached to more than one section (e.g. float staff).",
    )
    units = models.ManyToManyField(
        "orgstructure.Unit", blank=True, related_name="employees"
    )

    is_active = models.BooleanField(
        default=True, help_text="System Administrator only. Controls login/account status."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["surname", "first_name"]

    def __str__(self):
        full = f"{self.surname}, {self.first_name}"
        if self.middle_name:
            full += f" {self.middle_name[0]}."
        return f"{full} ({self.employee_id})"

    @property
    def full_name(self):
        parts = [self.surname + ",", self.first_name, self.middle_name, self.name_extension]
        return " ".join(p for p in parts if p)

    # --- Role helpers -----------------------------------------------------
    def active_role_assignments(self):
        return self.role_assignments.filter(is_active=True)

    def has_role(self, role_code):
        return self.active_role_assignments().filter(role=role_code).exists()

    def is_system_administrator(self):
        from accounts.models import RoleAssignment

        return self.has_role(RoleAssignment.SYSTEM_ADMINISTRATOR)

    def is_hr_administrator(self):
        from accounts.models import RoleAssignment

        return self.has_role(RoleAssignment.HR_ADMINISTRATOR)


class EducationHistory(models.Model):
    """One-to-many education record per employee (CLAUDE.md 5.2)."""

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="education_history"
    )
    education_level = models.CharField(max_length=100)
    school = models.CharField(max_length=200)
    degree_course = models.CharField(max_length=200, blank=True)
    units_earned = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name_plural = "Education history"
        ordering = ["employee", "id"]

    def __str__(self):
        return f"{self.employee} — {self.education_level} @ {self.school}"


class EmployeeEditHistory(models.Model):
    """
    Audit trail for edits made to an Employee record, scoped to this screen
    only (explicit go-ahead per CLAUDE.md Section 13 — this is NOT the
    hospital-wide RA 10173 audit log, which remains deferred).

    One row per changed field per save, so the before/after values are
    unambiguous even when several fields change in the same edit. All rows
    from the same edit share edited_at (down to the second) and reason.
    """

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="edit_history"
    )
    field_name = models.CharField(max_length=100)
    field_label = models.CharField(max_length=150, blank=True)
    old_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    reason = models.TextField(help_text="Required. Why this change was made.")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee_edits_made"
    )
    edited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Employee edit history"
        ordering = ["-edited_at", "field_name"]

    def __str__(self):
        return f"{self.employee} — {self.field_label or self.field_name} @ {self.edited_at:%Y-%m-%d %H:%M}"


class EmployeeProfileEditRequest(models.Model):
    """
    Self-service profile edit request (CLAUDE.md §6.4). An employee may
    request a change to their own contact information, address, or civil
    status, but it does NOT take effect immediately — it sits here as
    PENDING until an HR Administrator approves or rejects it. Approving
    applies the change to the Employee record and logs it to
    EmployeeEditHistory like any other edit.

    Deliberately narrow to the four self-service fields named in §6.4 —
    this is not a general-purpose edit-request system.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STATUS_CHOICES = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected")]

    SELF_SERVICE_FIELDS = [
        ("telephone_mobile", "Telephone/Mobile"),
        ("email", "Email"),
        ("residential_address", "Residential address"),
        ("permanent_address", "Permanent address"),
        ("civil_status", "Civil status"),
    ]
    SELF_SERVICE_FIELD_NAMES = [name for name, _ in SELF_SERVICE_FIELDS]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="profile_edit_requests"
    )
    field_name = models.CharField(max_length=50, choices=SELF_SERVICE_FIELDS)
    old_value = models.TextField(blank=True)
    requested_value = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)

    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="profile_requests_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        label = dict(self.SELF_SERVICE_FIELDS).get(self.field_name, self.field_name)
        return f"{self.employee} — {label} ({self.get_status_display()})"
