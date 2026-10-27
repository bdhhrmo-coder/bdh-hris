"""
Leave routing permissions.

CRITICAL rule from CLAUDE.md §6.2: Regular Leave (and every other CSC-listed
type) must NEVER be routed through the internal approval engine — only COSP
Leave does. This is enforced structurally, not just by convention: every
queue/action helper below filters on leave_type.requires_full_routing so a
Supervisor/AO/COH simply cannot see or act on a non-COSP-Leave application,
regardless of its status.
"""

from accounts.models import RoleAssignment
from employees.permissions import get_acting_employee  # re-exported for convenience

from .models import LeaveApplication


# viewing=True (Batch 7, owner 2026-10-13): use in VIEW-ONLY checks. An
# approver who has handed an area to an OIC keeps view access to it but
# cannot act on it; action checks keep the default viewing=False.

def _has_active_role(acting_employee, role_code, viewing=False):
    if acting_employee is None:
        return False
    return acting_employee.has_role(role_code, viewing=viewing)


def is_hr(acting_employee, viewing=False):
    return _has_active_role(acting_employee, RoleAssignment.HR_PROCESSOR, viewing) or _has_active_role(
        acting_employee, RoleAssignment.HR_ADMINISTRATOR, viewing
    )


def is_administrative_officer(acting_employee, viewing=False):
    return _has_active_role(acting_employee, RoleAssignment.ADMINISTRATIVE_OFFICER, viewing)


def is_chief_of_hospital(acting_employee, viewing=False):
    return _has_active_role(acting_employee, RoleAssignment.CHIEF_OF_HOSPITAL, viewing)


def is_supervisor_of(acting_employee, target_employee, viewing=False):
    """
    True if acting_employee holds an active Supervisor (or OIC-as-Supervisor)
    RoleAssignment whose section/unit overlaps the target's assignments.
    """
    if acting_employee is None:
        return False
    target_section_ids = set(target_employee.sections.values_list("id", flat=True))
    target_unit_ids = set(target_employee.units.values_list("id", flat=True))
    for ra in acting_employee.active_role_assignments(viewing=viewing).filter(role=RoleAssignment.SUPERVISOR):
        if ra.section_id is None and ra.unit_id is None:
            return True  # hospital-wide supervisor assignment
        if ra.section_id and ra.section_id in target_section_ids:
            return True
        if ra.unit_id and ra.unit_id in target_unit_ids:
            return True
    return False


def employees_with_role(role_code):
    """
    All employees holding an active RoleAssignment of this role code.

    Used for notification fan-out (Phase 10, CLAUDE.md §11): the HR/AO/COH
    routing steps aren't assigned to a named individual per request — any
    holder of that role may act — so "who should be notified" for those
    steps is every current holder of the role, the same set implicitly
    used by is_hr/is_administrative_officer/is_chief_of_hospital above.
    """
    from employees.models import Employee

    # Batch 7: same test as has_role (dates in force, OIC hand-over), so an
    # OIC is notified only during the OIC period and the absent approver
    # is not notified for the area handed over.
    candidate_ids = set(RoleAssignment.objects.filter(role=role_code, is_active=True).values_list(
        "employee_id", flat=True
    ))
    employee_ids = [e.pk for e in Employee.objects.filter(pk__in=candidate_ids) if e.has_role(role_code)]
    return Employee.objects.filter(pk__in=employee_ids).distinct()


def hr_employees():
    """Every active HR Processor or HR Administrator — mirrors is_hr's
    either/or check, but returns the people instead of testing one."""
    return employees_with_role(RoleAssignment.HR_PROCESSOR) | employees_with_role(RoleAssignment.HR_ADMINISTRATOR)


def supervisors_of(target_employee):
    """
    Every employee who is an active Supervisor (or OIC-as-Supervisor) of
    target_employee's sections/units — mirrors is_supervisor_of's logic,
    but returns every matching supervisor instead of testing one candidate,
    for notification fan-out.
    """
    return [
        candidate
        for candidate in employees_with_role(RoleAssignment.SUPERVISOR)
        if is_supervisor_of(candidate, target_employee)
    ]


# -- No self-approval; next level up (owner decision 2026-10-26) ------------
#
# Nobody acts on a request they filed (or, for an Exchange of Duty, a
# request they are a party to), at any step. Who acts instead:
#   Supervisor step - a Supervisor one level ABOVE the filer. For an
#       ordinary employee that is any Supervisor of their section/unit, as
#       before. For a filer who is a Supervisor themself, only someone with
#       a broader assignment counts: a unit head's request goes to the
#       section-wide Supervisor (e.g. the Chief Nurse), never to a fellow
#       head of the same unit. If there is nobody above, the AO endorses
#       (hierarchy COH -> AO -> Section Heads); if the AO is the filer, the
#       COH (the AO's own request goes straight to the COH).
#   HR / ICTU step  - any other holder of that role.
#   AO step         - the AO; if the AO is the filer, the COH.
#   COH step        - the COH, never on their own request.
# The person who acts is stamped with their own name and position, so the
# printout shows exactly who endorsed.
# View-only checks (viewing=True) are not affected.

def _ids(filers):
    return {f.pk for f in filers}


def _supervisor_assignments(employee, viewing=False):
    return list(employee.active_role_assignments(viewing=viewing).filter(role=RoleAssignment.SUPERVISOR))


def _is_next_level_supervisor(candidate, filer, viewing=False):
    """candidate may endorse filer's request at the Supervisor step."""
    own = _supervisor_assignments(filer)
    if not own:
        return is_supervisor_of(candidate, filer, viewing)
    if any(ra.section_id is None and ra.unit_id is None for ra in own):
        return False  # hospital-wide already; nobody above at Supervisor level
    own_section_ids = {ra.section_id for ra in own if ra.section_id}
    filer_section_ids = set(filer.sections.values_list("id", flat=True))
    for ra in _supervisor_assignments(candidate, viewing):
        if ra.section_id is None and ra.unit_id is None:
            return True
        if ra.section_id and ra.section_id in filer_section_ids and ra.section_id not in own_section_ids:
            return True
    return False


def _with_role_except(role_code, filers):
    excluded = _ids(filers)
    return [e for e in employees_with_role(role_code) if e.pk not in excluded]


def coh_step_actors(filers):
    return _with_role_except(RoleAssignment.CHIEF_OF_HOSPITAL, filers)


def ao_step_actors(filers):
    """The AO; the COH instead when the AO is the filer."""
    return _with_role_except(RoleAssignment.ADMINISTRATIVE_OFFICER, filers) or coh_step_actors(filers)


# -- Two-level endorsement (owner decision 2026-10-27, Option B) -------------
# Where an ordinary employee's unit has a unit head AND the section has a
# section-wide Supervisor above it (Nursing: unit head, then Chief Nurse),
# the Supervisor step is done twice, in order:
#   SUBMITTED --unit head--> ENDORSED_BY_UNIT_HEAD --section head--> ENDORSED_BY_SUPERVISOR
# A unit with no head, or a section with no section-wide Supervisor, keeps
# the single Supervisor step. A filer who is a Supervisor (a unit head)
# has no unit step: their request goes straight to the section head.

def _unit_level_endorsers(filers):
    """Unit heads (unit-scoped Supervisors, incl. an OIC) of an ordinary filer's unit."""
    out = []
    for s in _with_role_except(RoleAssignment.SUPERVISOR, filers):
        s_units = {ra.unit_id for ra in _supervisor_assignments(s) if ra.unit_id}
        if not s_units:
            continue
        for f in filers:
            if _supervisor_assignments(f):
                continue  # a Supervisor's own request has no unit step
            if s_units & set(f.units.values_list("id", flat=True)):
                out.append(s)
                break
    return out


def _covers_at_section_level(candidate, filer):
    if _supervisor_assignments(filer):
        return _is_next_level_supervisor(candidate, filer)
    filer_section_ids = set(filer.sections.values_list("id", flat=True))
    for ra in _supervisor_assignments(candidate):
        if ra.section_id is None and ra.unit_id is None:
            return True
        if ra.section_id and ra.section_id in filer_section_ids:
            return True
    return False


def _section_level_endorsers(filers):
    return [s for s in _with_role_except(RoleAssignment.SUPERVISOR, filers)
            if any(_covers_at_section_level(s, f) for f in filers)]


def _is_coh_filed(filers):
    return any(f.has_role(RoleAssignment.CHIEF_OF_HOSPITAL) for f in filers)


def endorsers_for(filers):
    """Who acts FIRST at the Supervisor step for a request filed by `filers`
    (one employee, or both parties of an Exchange of Duty): the unit head
    when there is one, otherwise the section-level Supervisor, otherwise the
    AO (or the COH for the AO's own request). Never a filer; nobody when a
    filer is the COH (no level above)."""
    filers = list(filers)
    if _is_coh_filed(filers):
        return []
    return _unit_level_endorsers(filers) or _section_level_endorsers(filers) or ao_step_actors(filers)


def second_endorsers_for(filers):
    """Who endorses after the unit head (the section-level Supervisor)."""
    filers = list(filers)
    if _is_coh_filed(filers):
        return []
    return _section_level_endorsers(filers)


def needs_unit_step(filers):
    """True when the Supervisor step is done twice (unit head, then section head)."""
    filers = list(filers)
    return not _is_coh_filed(filers) and bool(_unit_level_endorsers(filers)) \
        and bool(_section_level_endorsers(filers))


UNIT_STEP = "UNIT"              # the endorsement leads to ENDORSED_BY_UNIT_HEAD
SUPERVISOR_STEP = "SUPERVISOR"  # the endorsement completes the Supervisor step


def supervisor_step_result(acting_employee, filers, after_unit_endorsement=False):
    """What an endorsement by acting_employee does now: UNIT_STEP,
    SUPERVISOR_STEP, or None when this person may not endorse at this point."""
    if acting_employee is None:
        return None
    filers = list(filers)
    if after_unit_endorsement:
        return SUPERVISOR_STEP if acting_employee.pk in _ids(second_endorsers_for(filers)) else None
    if acting_employee.pk not in _ids(endorsers_for(filers)):
        return None
    return UNIT_STEP if needs_unit_step(filers) else SUPERVISOR_STEP


def can_endorse(acting_employee, filers):
    """May act at the FIRST Supervisor endorsement."""
    return acting_employee is not None and acting_employee.pk in _ids(endorsers_for(filers))


def can_endorse_second(acting_employee, filers):
    """May act at the second (section head) endorsement, after the unit head."""
    return acting_employee is not None and acting_employee.pk in _ids(second_endorsers_for(filers))


def can_act_hr_step(acting_employee, filers):
    return is_hr(acting_employee) and acting_employee.pk not in _ids(filers)


def can_act_ao_step(acting_employee, filers):
    return acting_employee is not None and acting_employee.pk in _ids(ao_step_actors(filers))


def can_act_coh_step(acting_employee, filers):
    return acting_employee is not None and acting_employee.pk in _ids(coh_step_actors(filers))


def hr_step_actors(filers):
    excluded = _ids(filers)
    return [e for e in hr_employees() if e.pk not in excluded]


def can_submit_for(acting_employee, target_employee):
    """An employee may only submit a leave application for themselves."""
    return acting_employee is not None and acting_employee.pk == target_employee.pk


def can_view_application(acting_employee, application):
    """
    Who may open/print a given leave application: the applicant, anyone in
    its routing/monitoring chain (HR, AO, COH), or the applicant's
    supervisor — the same set of people who would ever see it in a queue
    or need to hand the applicant a copy of the form.
    """
    if acting_employee is None:
        return False
    if acting_employee.pk == application.employee_id:
        return True
    if is_hr(acting_employee, True) or is_administrative_officer(acting_employee, True) \
            or is_chief_of_hospital(acting_employee, True):
        return True
    return is_supervisor_of(acting_employee, application.employee, viewing=True)


def visible_applications_for(acting_employee):
    """
    Applications this user may act on right now, scoped by role AND status.
    Used to build each role's queue — never used for the employee's own
    "my applications" list, which shows everything of theirs regardless of
    status.
    """
    if acting_employee is None:
        return LeaveApplication.objects.none()

    qs = LeaveApplication.objects.none()

    if is_hr(acting_employee):
        # HR acts on: SUBMITTED for data-entry-only types, plus
        # SUBMITTED/ENDORSED_BY_SUPERVISOR for COSP Leave.
        data_entry_only = LeaveApplication.objects.filter(
            leave_type__requires_full_routing=False, status=LeaveApplication.SUBMITTED
        )
        cosp_leave_stage = LeaveApplication.objects.filter(
            leave_type__requires_full_routing=True,
            status__in=[LeaveApplication.SUBMITTED, LeaveApplication.ENDORSED_BY_SUPERVISOR],
        )
        qs = qs | data_entry_only | cosp_leave_stage

    ao_ids = [
        app.pk for app in LeaveApplication.objects.filter(
            leave_type__requires_full_routing=True, status=LeaveApplication.PROCESSED_BY_HR
        ).select_related("employee")
        if can_act_ao_step(acting_employee, [app.employee])
    ]
    qs = qs | LeaveApplication.objects.filter(pk__in=ao_ids)

    if is_chief_of_hospital(acting_employee):
        qs = qs | LeaveApplication.objects.filter(
            leave_type__requires_full_routing=True, status=LeaveApplication.RECOMMENDED_BY_AO
        )

    # Supervisor queue is scoped per-employee, so it's filtered in Python
    # rather than a single queryset filter.
    supervisor_candidates = LeaveApplication.objects.filter(
        leave_type__requires_full_routing=True, status=LeaveApplication.SUBMITTED
    ).select_related("employee")
    supervisor_ids = [
        app.pk for app in supervisor_candidates if can_endorse(acting_employee, [app.employee])
    ]
    qs = qs | LeaveApplication.objects.filter(pk__in=supervisor_ids)

    # Second endorsement (section head), after the unit head - 2026-10-27.
    second_ids = [
        app.pk for app in LeaveApplication.objects.filter(
            leave_type__requires_full_routing=True, status=LeaveApplication.ENDORSED_BY_UNIT_HEAD
        ).select_related("employee")
        if can_endorse_second(acting_employee, [app.employee])
    ]
    qs = qs | LeaveApplication.objects.filter(pk__in=second_ids)

    # Never one's own application, at any step (owner decision 2026-10-26).
    return qs.exclude(employee=acting_employee).distinct()
