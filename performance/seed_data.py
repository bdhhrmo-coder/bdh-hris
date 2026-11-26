"""Seed values (CLAUDE.md §13C; Design Package v1.1 §3 and §7.3). Loaded by
migration 0002. All numbers are DRAFT settings the PMT/COH approved; HR
changes them on Performance Setup with an approval reference."""

from datetime import date

APPROVAL = "PMT/COH decisions 2026-11-24 (relayed by the owner)"
EFFECTIVE = date(2026, 1, 1)

SETTINGS = {
    # TPE weights (decision 1)
    "w_rank_perf": "50", "w_rank_sup": "30", "w_rank_peer": "10", "w_rank_att": "10",
    "w_sup_obj": "40", "w_sup_top": "20", "w_sup_sub": "30", "w_sup_peer": "10",
    # attendance and deductions
    "absence_points": "15", "tardy_points": "3", "deduction_cap": "15",
    # fairness (decision 3)
    "min_confidential_n": "3", "trim_threshold": "0", "outlier_gap": "1.5", "max_no_share": "0.5",
    "peer_cap": "4", "peers_per_ratee": "3", "peer_proposals": "5", "contact_share": "0.5",
    "fast_seconds_per_item": "5",
    # windows and due dates (working days)
    "dispute_days": "5", "explanation_days": "5", "deduction_review_days": "5", "cosp_rating_days": "5",
    # periods (decision 5)
    "pre_expiry_days": "60", "pre_expiry_replace_days": "30", "regular_track_enabled": "0",
    "cosp_open_days_before_end": "5",
    # performance description bands (2026-11-24)
    "band_exemplary": "90", "band_strong": "80", "band_solid": "70", "band_developing": "60",
    # development plans (step 6) and missed check-ins (D11, 2026-11-26)
    "dev_commitments": "2", "dev_checkins_min": "1", "dev_checkins_max": "2", "checkin_missed_days": "5",
    # reminders and time estimates (step 7, v1.1 §8)
    "reminder_share_1": "0.5", "reminder_share_2": "0.8", "reminder_days_before_close": "2",
    "checkin_reminder_days": "2", "form_minutes_a_supervisor": "5", "form_minutes_a_peer": "3",
    "form_minutes_b": "9", "form_minutes_c": "4", "conference_minutes": "20", "checkin_minutes": "10",
}

SETTING_LABELS = {
    "w_rank_perf": "Rank-and-file weight: Performance", "w_rank_sup": "Rank-and-file weight: Supervisor",
    "w_rank_peer": "Rank-and-file weight: Peer", "w_rank_att": "Rank-and-file weight: Attendance",
    "w_sup_obj": "Supervisor weight: Objective performance", "w_sup_top": "Supervisor weight: Top management",
    "w_sup_sub": "Supervisor weight: Subordinates", "w_sup_peer": "Supervisor weight: Peers",
    "absence_points": "Points off attendance per unauthorized absence", "tardy_points": "Points off per tardy day",
    "deduction_cap": "Deduction cap per period",
    "min_confidential_n": "Minimum raters for a confidential group", "trim_threshold":
    "Trim highest and lowest from this many raters (0 = never)", "outlier_gap": "Outlier gap (points)",
    "max_no_share": "Most N/O allowed on a form (share)", "peer_cap": "Most peer forms per rater per window",
    "peers_per_ratee": "Peers per employee", "peer_proposals": "Peers an employee may propose",
    "contact_share": "Working contact: share of the period", "fast_seconds_per_item": "Fast-rating flag (seconds per item)",
    "dispute_days": "Contest window (working days)", "explanation_days": "Explanation window (working days)",
    "deduction_review_days": "Deduction review window (working days)", "cosp_rating_days":
    "COSP forms due after the period ends (working days)", "pre_expiry_days": "Pre-expiry period: days before the contract ends",
    "pre_expiry_replace_days": "A COSP period ending this close to pre-expiry is replaced by it (days)",
    "regular_track_enabled": "Regular employees included (1 = yes; waits for CSC FO and PHRMO)",
    "cosp_open_days_before_end": "COSP periods open this many days before they end (daily job)",
    "band_exemplary": "Exemplary from", "band_strong": "Strong from", "band_solid": "Solid from",
    "band_developing": "Developing from", "dev_commitments": "Commitments per development plan",
    "dev_checkins_min": "Fewest check-in dates", "dev_checkins_max": "Most check-in dates",
    "checkin_missed_days": "A check-in is marked missed this many working days after its date",
    "reminder_share_1": "First rater reminder (share of the rating time)", "reminder_share_2": "Second rater reminder",
    "reminder_days_before_close": "Last rater reminder (days before the due date)", "checkin_reminder_days":
    "Check-in reminder (days before)", "form_minutes_a_supervisor": "Minutes: Form A by the supervisor",
    "form_minutes_a_peer": "Minutes: Form A by a peer", "form_minutes_b": "Minutes: Form B", "form_minutes_c":
    "Minutes: Form C", "conference_minutes": "Minutes: feedback conference", "checkin_minutes": "Minutes: check-in",
}

COMPETENCIES = [
    # (instrument, code, name, sort)
    ("A", "RESPECT", "Respect", 1), ("A", "INTEGRITY", "Integrity", 2), ("A", "QUALITY", "Quality Service", 3),
    ("A", "TEAMWORK", "Teamwork", 4),
    ("B", "STRAT", "Thinking Strategically and Creatively", 1), ("B", "CHANGE", "Leading Change", 2),
    ("B", "COLLAB", "Building Collaborative and Inclusive Working Relationships", 3),
    ("B", "COACH", "Managing Performance and Coaching for Results", 4),
    ("B", "HIPO", "Creating and Nurturing a High-Performing Organization", 5),
    ("Q", "LEADQ", "Leader questions (not scored)", 1),
    ("C", "SELF", "Self-assessment", 1),
]

# (code, competency code, clinical / first-line, non-clinical / higher-leader)
FORM_A = [
    ("A1", "RESPECT", "Treats patients and watchers with courtesy and patience, and explains what will be done before doing it.",
     "Treats clients and visitors with courtesy and patience, and explains the process and what to expect."),
    ("A2", "RESPECT", "Protects patient privacy and dignity: uses screens or draping, keeps voice low, doesn't discuss patients in public areas.",
     "Keeps client and co-worker information private; doesn't discuss others' records or personal matters in public areas."),
    ("A3", "RESPECT", "Listens without interrupting and stays calm with co-workers, including at handover and when someone disagrees.",
     "Listens without interrupting and stays calm with co-workers, including in meetings and when someone disagrees."),
    ("A4", "INTEGRITY", "Charts care accurately and on time, recording only what was actually done and observed.",
     "Keeps records, reports and transactions accurate and complete, reporting only what was actually done."),
    ("A5", "INTEGRITY", "Reports own errors, near misses and incidents promptly instead of hiding them or blaming others.",
     "Owns up to own mistakes promptly and helps fix them instead of hiding them or blaming others."),
    ("A6", "INTEGRITY", "Follows protocols and uses medicines, supplies and duty time only for their proper purpose.",
     "Follows procedures and uses funds, supplies, equipment and work time only for their proper purpose."),
    ("A7", "QUALITY", "Performs assessments, procedures and orders correctly, including safety checks such as patient identification.",
     "Completes tasks correctly the first time and checks own work before passing it on."),
    ("A8", "QUALITY", "Responds to patient calls and needs within the expected time, and tells the patient when there will be a delay.",
     "Acts on requests within the expected processing time, and tells the client when there will be a delay."),
    ("A9", "QUALITY", "Considers the whole patient (comfort, concerns, family) when giving care and instructions.",
     "Makes sure the client leaves knowing what was done and what to do next."),
    ("A10", "TEAMWORK", "Gives complete, clear handovers and endorsements so the next person can continue care safely.",
     "Passes on complete information and documents so the next person can continue the work without delay."),
    ("A11", "TEAMWORK", "Helps co-workers when the workload is heavy (admissions surges, codes) without being asked repeatedly.",
     "Helps co-workers when the workload is heavy without being asked repeatedly."),
    ("A12", "TEAMWORK", "Works cooperatively with other units and sections (laboratory, pharmacy, radiology, billing) to get things done for the patient.",
     "Works cooperatively with other units and sections, including clinical areas, to finish shared tasks."),
]

FORM_B = [
    ("B1", "STRAT", "Plans the unit's staffing, supplies and schedules ahead to prevent problems rather than react to them.",
     "Sets section priorities and plans that support the hospital's goals and OPCR targets."),
    ("B2", "STRAT", "Suggests practical improvements to unit work based on data or observations.",
     "Uses data (CSM results, KPIs, incident reports) to find problems and choose solutions."),
    ("B3", "STRAT", "Explains to staff how the unit's work links to hospital goals.",
     "Weighs risks, costs and effects on other sections before deciding."),
    ("B4", "CHANGE", "Explains new policies or procedures to staff clearly, including why they are changing.",
     "Communicates the purpose, plan and timeline of a change to everyone affected."),
    ("B5", "CHANGE", "Helps staff adjust to changes by answering questions, training them and following up.",
     "Involves affected staff and sections in planning changes and acts on their concerns."),
    ("B6", "CHANGE", "Follows new procedures personally and checks that the unit follows them.",
     "Follows through until a change is in place and reviews whether it worked."),
    ("B7", "COLLAB", "Treats all staff fairly regardless of employment type (Regular or COSP), position or personal ties.",
     "Ensures fair treatment of all staff across the section, regardless of employment type, position or personal ties."),
    ("B8", "COLLAB", "Coordinates with other units and sections to solve shared problems.",
     "Builds working arrangements with other sections and outside partners (for example the PHO, LGU and referral hospitals)."),
    ("B9", "COLLAB", "Welcomes staff ideas and concerns and responds to them.",
     "Provides ways for staff to raise concerns and reports back on what was done."),
    ("B10", "COACH", "Sets clear targets and expectations with each staff member at the start of the period.",
     "Makes sure unit heads set and review targets with their staff."),
    ("B11", "COACH", "Gives timely, specific feedback (praise and correction) based on observed work.",
     "Gives timely, specific feedback to unit heads based on observed results."),
    ("B12", "COACH", "Coaches staff to improve and supports their training requests.",
     "Develops unit heads and possible successors."),
    ("B13", "HIPO", "Recognizes good work openly and fairly.",
     "Builds a culture of recognition and accountability in the section."),
    ("B14", "HIPO", "Deals with performance or behavior problems promptly and fairly.",
     "Monitors section results against targets and acts on gaps."),
    ("B15", "HIPO", "Keeps unit workflows, roles and records clear and organized.",
     "Keeps section systems, SOPs and records in shape to support quality service."),
]
DAY_TO_DAY = {f"B{i}" for i in range(4, 13)}

LEADER_Q = [
    ("Q1", "I can raise a concern or admit a mistake to this leader without fear of being punished or embarrassed."),
    ("Q2", "During the most recent change in our area, I knew what was expected of me."),
    ("Q3", "This leader has helped me improve my work during this period."),
]

FORM_C_TEXT = [
    ("C2", "Your two main strengths at work this period", True),
    ("C3", "One area you want to improve", True),
    ("C4", "Support or training you need (optional)", False),
    ("C5", "After reading your feedback: what surprised you, and what will you act on?", False),
]

CLINICAL_SECTIONS = ["Medical Services Section", "Nursing Service Section", "Laboratory Section",
                     "Radiology Section", "Pharmacy Section"]

# (code, name, applies_to, points, cap, needs_final_ref, sort)
CRITERIA = [
    ("R1", "Completion of assigned duties", "RANK", "0.50,1.00", None, False, 1),
    ("R2", "Accuracy and completeness of records", "RANK", "0.25,0.50,1.00", None, False, 2),
    ("R3", "Safety, SOPs and scope of duty", "RANK", "0.50,1.00,2.00", None, False, 3),
    ("R4", "Handover and service continuity", "RANK", "0.50,1.00", None, False, 4),
    ("R5", "Timely reporting and escalation", "RANK", "0.50,1.00,2.00", None, False, 5),
    ("R6", "Stewardship of property and information", "RANK", "0.50,1.00,2.00", None, False, 6),
    ("R7", "Final and executory administrative decisions", "RANK", "0.50,1.00,2.00", None, True, 7),
    ("S1", "Planning and staffing", "SUPERVISOR", "1.00,2.00,3.00", None, False, 11),
    ("S2", "Monitoring of section performance", "SUPERVISOR", "0.50,1.00,2.00", None, False, 12),
    ("S3", "Employee supervision", "SUPERVISOR", "0.50,1.00,2.00", None, False, 13),
    ("S4", "Compliance and corrective action", "SUPERVISOR", "1.00,2.00,3.00", None, False, 14),
    ("S5", "Risk, incident and continuity escalation", "SUPERVISOR", "1.00,2.00,3.00", None, False, 15),
    ("S6", "Directives and coordination", "SUPERVISOR", "0.50,1.00,2.00", None, False, 16),
    ("X7", "Module tasks not done by the window close", "SUPERVISOR", "1.00", "3", False, 21),
    ("X8", "Request held beyond the ARTA / office-order processing time", "SUPERVISOR", "1.00", "3", False, 22),
    ("X9", "Breach of confidentiality of ratings or personnel records", "ALL", "2.00,3.00", "6", False, 23),
    ("X10", "Retaliation", "ALL", "2.00,3.00", "6", True, 24),
]
CRITERIA_APPROVAL = {"S": "Owner decision 2026-11-25, Option B", "R": APPROVAL, "X": APPROVAL}


def load(apps):
    PMSetting = apps.get_model("performance", "PMSetting")
    Competency = apps.get_model("performance", "Competency")
    Indicator = apps.get_model("performance", "Indicator")
    ClinicalSection = apps.get_model("performance", "ClinicalSection")
    Section = apps.get_model("orgstructure", "Section")
    Criterion = apps.get_model("performance", "DeductionCriterion")

    for key, value in SETTINGS.items():
        if not PMSetting.objects.filter(key=key).exists():
            PMSetting.objects.create(key=key, value=value, effective_from=EFFECTIVE, approval_ref=APPROVAL)
    comps = {}
    for inst, code, name, order in COMPETENCIES:
        comps[code], _ = Competency.objects.get_or_create(instrument=inst, code=code,
                                                         defaults={"name": name, "sort_order": order})
    order = 0
    for code, comp, w1, w2 in FORM_A + FORM_B:
        order += 1
        Indicator.objects.get_or_create(code=code, defaults=dict(
            competency=comps[comp], wording_1=w1, wording_2=w2, day_to_day=code in DAY_TO_DAY, sort_order=order))
    for code, text in LEADER_Q:
        order += 1
        Indicator.objects.get_or_create(code=code, defaults=dict(competency=comps["LEADQ"], wording_1=text,
                                                                 scored=False, sort_order=order))
    for code, text, required in FORM_C_TEXT:
        order += 1
        Indicator.objects.get_or_create(code=code, defaults=dict(competency=comps["SELF"], wording_1=text,
                                                                 scored=False, is_text=True, required=required,
                                                                 sort_order=order))
    # Form C item C1: one self-rating per competency (CA1-CA4 for Form A, CB1-CB5 for Form B).
    for prefix, inst in (("CA", "A"), ("CB", "B")):
        for i, comp in enumerate(Competency.objects.filter(instrument=inst).order_by("sort_order"), start=1):
            order += 1
            Indicator.objects.get_or_create(code=f"{prefix}{i}", defaults=dict(
                competency=comp, wording_1=f"Rate yourself on: {comp.name}", scored=False, sort_order=order))
    for name in CLINICAL_SECTIONS:
        sec = Section.objects.filter(name=name).first()
        if sec:
            ClinicalSection.objects.get_or_create(section=sec)
    for code, name, applies, pts, cap, final_ref, sort in CRITERIA:
        Criterion.objects.get_or_create(code=code, defaults=dict(
            name=name, applies_to=applies, points_options=pts, cap=cap, needs_final_ref=final_ref,
            sort_order=sort, approval_ref=CRITERIA_APPROVAL[code[0]]))
