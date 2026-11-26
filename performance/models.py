"""Performance Management (TPE + 360), CLAUDE.md §13C and Design Package
v1.1 §7.3. Rebuilt 2026-11-26. Every policy number is a PMSetting row, never
a code constant."""

from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL
EMP = "employees.Employee"


class PMSetting(models.Model):
    """Append-only dated settings: a change is a new row with an approval
    reference, effective today or later; the value in force on a date is the
    latest row with effective_from <= that date."""

    key = models.CharField(max_length=60)
    value = models.CharField(max_length=120)
    effective_from = models.DateField()
    approval_ref = models.CharField(max_length=120)
    entered_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    entered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["key", "-effective_from", "-pk"]


class Competency(models.Model):
    A, B, C, Q = "A", "B", "C", "Q"
    INSTRUMENTS = [(A, "Form A"), (B, "Form B"), (C, "Form C"), (Q, "Leader questions")]
    instrument = models.CharField(max_length=1, choices=INSTRUMENTS)
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=120)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["instrument", "sort_order"]
        constraints = [models.UniqueConstraint(fields=["instrument", "code"], name="pm_comp_code")]

    def __str__(self):
        return self.name


class Indicator(models.Model):
    """One item. wording_1 = clinical (Form A) / first-line (Form B);
    wording_2 = non-clinical / higher-leader. Form C text items use wording_1."""

    competency = models.ForeignKey(Competency, on_delete=models.PROTECT, related_name="indicators")
    code = models.CharField(max_length=6, unique=True)
    wording_1 = models.TextField()
    wording_2 = models.TextField(blank=True)
    scored = models.BooleanField(default=True)
    day_to_day = models.BooleanField(default=False, help_text="B4-B12: the N/O test for subordinates (N5).")
    is_text = models.BooleanField(default=False, help_text="Form C short-text item.")
    required = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]

    def __str__(self):
        return self.code

    def wording(self, track):
        return self.wording_2 if str(track) == "2" and self.wording_2 else self.wording_1


class ClinicalSection(models.Model):
    """Sections whose staff get the clinical wording of Form A by default."""

    section = models.OneToOneField("orgstructure.Section", on_delete=models.CASCADE, related_name="+")


class WordingOverride(models.Model):
    employee = models.OneToOneField(EMP, on_delete=models.CASCADE, related_name="pm_wording")
    track = models.CharField(max_length=1, choices=[("1", "Clinical"), ("2", "Non-clinical")])
    reason = models.CharField(max_length=255)
    set_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    set_at = models.DateTimeField(auto_now=True)


class RatingWindow(models.Model):
    DRAFT, OPEN, CLOSED = "DRAFT", "OPEN", "CLOSED"
    STATUSES = [(DRAFT, "Draft"), (OPEN, "Open"), (CLOSED, "Closed")]
    name = models.CharField(max_length=60)
    semester = models.CharField(max_length=7, unique=True, help_text="e.g. 2027-S1")
    start_date = models.DateField(help_text="First day of the semester rated.")
    end_date = models.DateField(help_text="Last day of the semester rated.")
    opens_on = models.DateField()
    closes_on = models.DateField()
    status = models.CharField(max_length=6, choices=STATUSES, default=DRAFT)
    created_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return self.name


class EvaluationPeriod(models.Model):
    RANK, SUPERVISOR = "RANK", "SUPERVISOR"
    TRACKS = [(RANK, "Rank-and-file"), (SUPERVISOR, "Supervisor")]
    SEMESTRAL, MONTH1, QUARTER, PRE_EXPIRY = "SEMESTRAL", "MONTH1", "QUARTER", "PRE_EXPIRY"
    TYPES = [(SEMESTRAL, "Semestral"), (MONTH1, "COSP month one"), (QUARTER, "COSP quarterly"),
             (PRE_EXPIRY, "COSP pre-expiry")]
    PLANNED, OPEN, COMPUTED, UNDER_REVIEW, RELEASED, DISPUTED, FINAL, CANCELLED = (
        "PLANNED", "OPEN", "COMPUTED", "UNDER_REVIEW", "RELEASED", "DISPUTED", "FINAL", "CANCELLED")
    STATUSES = [(PLANNED, "Planned"), (OPEN, "Open"), (COMPUTED, "Computed"), (UNDER_REVIEW, "Next-level review"),
                (RELEASED, "Released"), (DISPUTED, "Contested"), (FINAL, "Final"), (CANCELLED, "Cancelled")]

    employee = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name="pm_periods")
    window = models.ForeignKey(RatingWindow, on_delete=models.PROTECT, null=True, blank=True, related_name="periods")
    contract = models.ForeignKey("outonpass.CospContractPeriod", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+")
    track = models.CharField(max_length=10, choices=TRACKS)
    period_type = models.CharField(max_length=10, choices=TYPES)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=12, choices=STATUSES, default=OPEN)
    # Inputs (encoded by one HR person, verified by another HR Administrator).
    certified_rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    certificate_ref = models.CharField(max_length=120, blank=True)
    certified_encoded_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    certified_verified_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    absence_days = models.PositiveSmallIntegerField(null=True, blank=True)
    tardy_days = models.PositiveSmallIntegerField(null=True, blank=True)
    dtr_encoded_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    dtr_verified_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # Result.
    attendance_score = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    subtotal = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    deduction_total = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    tpe_total = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    computed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    conference_on = models.DateField(null=True, blank=True)
    conference_by = models.ForeignKey(EMP, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    disagreement_note = models.TextField(blank=True)
    reflection = models.TextField(blank=True, help_text="Form C item C5, after release.")
    final_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "employee"]

    def __str__(self):
        return f"{self.employee} {self.get_period_type_display()} {self.start_date}-{self.end_date}"


class PeriodEvent(models.Model):
    period = models.ForeignKey(EvaluationPeriod, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=40)
    note = models.CharField(max_length=500, blank=True)
    user = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "pk"]


class RaterAssignment(models.Model):
    SELF, SUPERVISOR, PEER, POOLED, SUBORDINATE, TOP, SUP_PEER = (
        "SELF", "SUPERVISOR", "PEER", "POOLED", "SUBORDINATE", "TOP", "SUP_PEER")
    GROUPS = [(SELF, "Self"), (SUPERVISOR, "Immediate supervisor"), (PEER, "Peer"), (POOLED, "Pooled peer"),
              (SUBORDINATE, "Subordinate"), (TOP, "Top management"), (SUP_PEER, "Supervisor peer")]
    CONFIDENTIAL = {PEER, POOLED, SUBORDINATE, SUP_PEER}
    PROPOSED, ASSIGNED, IN_PROGRESS, SUBMITTED, DECLINED, CONFLICT, VOID, NOT_SELECTED = (
        "PROPOSED", "ASSIGNED", "IN_PROGRESS", "SUBMITTED", "DECLINED", "CONFLICT", "VOID", "NOT_SELECTED")
    STATUSES = [(PROPOSED, "Proposed"), (ASSIGNED, "To do"), (IN_PROGRESS, "In progress"), (SUBMITTED, "Submitted"),
                (DECLINED, "Closed: no working contact"), (CONFLICT, "Closed: conflict"), (VOID, "Voided"),
                (NOT_SELECTED, "Not selected")]
    LIVE = {ASSIGNED, IN_PROGRESS}

    period = models.ForeignKey(EvaluationPeriod, on_delete=models.CASCADE, related_name="raters")
    rater = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name="pm_rating_tasks")
    group = models.CharField(max_length=12, choices=GROUPS)
    form = models.CharField(max_length=1, choices=[("A", "Form A"), ("B", "Form B"), ("C", "Form C")])
    wording = models.CharField(max_length=1, default="1")
    nominated = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=STATUSES, default=ASSIGNED)
    contact_ok = models.BooleanField(null=True)
    due_on = models.DateField(null=True, blank=True)
    first_shown_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    flags = models.CharField(max_length=80, blank=True)
    flag_review = models.CharField(max_length=8, blank=True, help_text="PENDING / KEPT / VOIDED")
    flag_note = models.CharField(max_length=255, blank=True)
    reminders_sent = models.CharField(max_length=30, blank=True)
    added_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["period", "group", "pk"]

    @property
    def is_confidential(self):
        return self.group in self.CONFIDENTIAL


class Response(models.Model):
    assignment = models.ForeignKey(RaterAssignment, on_delete=models.CASCADE, related_name="responses")
    indicator = models.ForeignKey(Indicator, on_delete=models.PROTECT, related_name="+")
    value = models.PositiveSmallIntegerField(null=True, blank=True)
    not_observed = models.BooleanField(default=False)
    text = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "indicator"], name="pm_one_response")]


class Comment(models.Model):
    STRENGTH, IMPROVE = "STRENGTH", "IMPROVE"
    PENDING, RELEASED, EDITED, WITHHELD = "PENDING", "RELEASED", "EDITED", "WITHHELD"
    assignment = models.ForeignKey(RaterAssignment, on_delete=models.CASCADE, related_name="comments")
    kind = models.CharField(max_length=8, choices=[(STRENGTH, "Strength"), (IMPROVE, "To improve")])
    raw_text = models.TextField()
    released_text = models.TextField(blank=True)
    status = models.CharField(max_length=8, default=PENDING)
    screened_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=255, blank=True)


class OrientationAck(models.Model):
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name="+")
    window = models.ForeignKey(RatingWindow, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    period = models.ForeignKey(EvaluationPeriod, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    at = models.DateTimeField(auto_now_add=True)


class CospForm(models.Model):
    """COSP Performance Form for one period (v1.1 §3.5)."""

    period = models.OneToOneField(EvaluationPeriod, on_delete=models.CASCADE, related_name="cosp_form")
    supervisor = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name="+")
    remark = models.TextField(blank=True)
    score = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)


class PerfOutput(models.Model):
    form = models.ForeignKey(CospForm, on_delete=models.CASCADE, related_name="outputs")
    seq = models.PositiveSmallIntegerField()
    output = models.CharField(max_length=200)
    success_indicator = models.CharField(max_length=300)
    quality = models.PositiveSmallIntegerField(null=True, blank=True)
    efficiency = models.PositiveSmallIntegerField(null=True, blank=True)
    timeliness = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["seq"]


class ComponentScore(models.Model):
    period = models.ForeignKey(EvaluationPeriod, on_delete=models.CASCADE, related_name="components")
    component = models.CharField(max_length=10)
    label = models.CharField(max_length=40)
    mean = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    n = models.PositiveSmallIntegerField(default=0)
    pooled = models.BooleanField(default=False)
    skipped = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    weight_nominal = models.DecimalField(max_digits=6, decimal_places=2)
    weight_used = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    points = models.DecimalField(max_digits=8, decimal_places=4, default=0)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]


class DeductionCriterion(models.Model):
    RANK, SUPERVISOR, ALL = "RANK", "SUPERVISOR", "ALL"
    code = models.CharField(max_length=4, unique=True)
    name = models.CharField(max_length=200)
    applies_to = models.CharField(max_length=10, choices=[(RANK, "Everyone (rank-and-file criteria)"),
                                                          (SUPERVISOR, "Supervisors"), (ALL, "Everyone")])
    points_options = models.CharField(max_length=60, blank=True, help_text="e.g. 0.50,1.00,2.00")
    cap = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    needs_final_ref = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    approval_ref = models.CharField(max_length=120, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]

    def __str__(self):
        return f"{self.code} {self.name}"

    def options(self):
        from decimal import Decimal

        return [Decimal(x) for x in self.points_options.split(",") if x.strip()]


class CriterionChange(models.Model):
    criterion = models.ForeignKey(DeductionCriterion, on_delete=models.CASCADE, related_name="changes")
    field = models.CharField(max_length=30)
    old_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    approval_ref = models.CharField(max_length=120)
    changed_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)


class Deduction(models.Model):
    FILED, VALIDATED, APPROVED, REVIEW_HR, REVIEW_AO, REVIEW_COH, FINAL, DISMISSED = (
        "FILED", "VALIDATED", "APPROVED", "REVIEW_HR", "REVIEW_AO", "REVIEW_COH", "FINAL", "DISMISSED")
    STATUSES = [(FILED, "Filed: HR to validate"), (VALIDATED, "Validated: employee to explain"),
                (APPROVED, "Approved: review window open"), (REVIEW_HR, "Review: HR"), (REVIEW_AO, "Review: AO"),
                (REVIEW_COH, "Review: COH decides"), (FINAL, "Final"), (DISMISSED, "Dismissed")]
    employee = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name="pm_deductions")
    criterion = models.ForeignKey(DeductionCriterion, on_delete=models.PROTECT, related_name="deductions")
    incident_date = models.DateField()
    description = models.TextField()
    evidence_ref = models.CharField(max_length=200, blank=True)
    final_decision_ref = models.CharField(max_length=200, blank=True)
    points = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default=FILED)
    filed_by = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name="+")
    filed_at = models.DateTimeField(auto_now_add=True)
    auto_source = models.CharField(max_length=60, blank=True, help_text="Filed by the system (e.g. a missed check-in).")
    explanation = models.TextField(blank=True)
    explanation_due = models.DateField(null=True, blank=True)
    review_due = models.DateField(null=True, blank=True)
    review_grounds = models.TextField(blank=True)
    final_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-filed_at"]


class DeductionEvent(models.Model):
    deduction = models.ForeignKey(Deduction, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=30)
    note = models.CharField(max_length=500, blank=True)
    user = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "pk"]


class Contest(models.Model):
    HR, AO, COH, UPHELD, RETURNED = "HR", "AO", "COH", "UPHELD", "RETURNED"
    period = models.ForeignKey(EvaluationPeriod, on_delete=models.CASCADE, related_name="contests")
    grounds = models.TextField()
    status = models.CharField(max_length=8, default=HR)
    filed_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)


class DevPlan(models.Model):
    DRAFT, AGREED, CLOSED = "DRAFT", "AGREED", "CLOSED"
    period = models.OneToOneField(EvaluationPeriod, on_delete=models.CASCADE, related_name="dev_plan")
    supervisor = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name="+")
    strengths = models.TextField(blank=True)
    growth_areas = models.TextField(blank=True)
    status = models.CharField(max_length=6, default=DRAFT,
                              choices=[(DRAFT, "Draft"), (AGREED, "Agreed"), (CLOSED, "Closed")])
    sent_at = models.DateTimeField(null=True, blank=True)
    change_note = models.TextField(blank=True)
    agreed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_note = models.TextField(blank=True)


class DevGoal(models.Model):
    INTERVENTIONS = [("COACHING", "Coaching"), ("ON_THE_JOB", "On-the-job"), ("TRAINING", "Training")]
    RESPONSIBLE = [("EMPLOYEE", "Employee"), ("SUPERVISOR", "Supervisor"), ("LND", "L&D")]
    plan = models.ForeignKey(DevPlan, on_delete=models.CASCADE, related_name="goals")
    seq = models.PositiveSmallIntegerField()
    commitment = models.TextField()
    intervention_type = models.CharField(max_length=12, choices=INTERVENTIONS)
    intervention = models.CharField(max_length=200, blank=True)
    training_request_ref = models.CharField(max_length=80, blank=True)
    responsible = models.CharField(max_length=10, choices=RESPONSIBLE)
    target_date = models.DateField()
    evidence = models.TextField(blank=True)

    class Meta:
        ordering = ["seq"]


class DevCheckin(models.Model):
    PLANNED, HELD, MISSED = "PLANNED", "HELD", "MISSED"
    goal = models.ForeignKey(DevGoal, on_delete=models.CASCADE, related_name="checkins")
    planned_on = models.DateField()
    held_on = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=7, default=PLANNED,
                              choices=[(PLANNED, "Planned"), (HELD, "Held"), (MISSED, "Missed")])
    late = models.BooleanField(default=False, help_text="Recorded after it was marked missed (D11).")
    auto_missed_at = models.DateTimeField(null=True, blank=True)
    reminded_on = models.DateField(null=True, blank=True)
    deduction = models.ForeignKey(Deduction, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["planned_on", "goal__seq"]
