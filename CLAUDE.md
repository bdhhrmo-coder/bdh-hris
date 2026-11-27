# CLAUDE.md — Bataraza District Hospital (BDH) HRIS

This file gives Claude Code persistent context for this project. Read this before making changes. Business rules here reflect actual approved BDH policy — do not alter leave, CTO, or approval logic without explicit confirmation from the project owner.

---

## 1. Project Overview

A full-stack, on-premises Human Resource Information System (HRIS) for Bataraza District Hospital, a DOH-licensed Level 1 government hospital under the Provincial Government of Palawan. Manages leave, CTO, duty exchange, attendance correction, official business/time, travel, and overtime/restday/holiday work requests for ~200 employees.

This is a from-scratch build. Nothing exists yet.

---

## 2. Tech Stack

- **Backend:** Python + Django
- **Database:** PostgreSQL
- **Frontend:** Django Templates + HTMX + Alpine.js (no separate SPA/JS framework)
- **Deployment target:** Windows Server, on-prem, local hospital network only
  - **App server:** Waitress (pure-Python WSGI server) run as a Windows Service via NSSM
  - **Static files:** WhiteNoise (served directly by Django/Waitress — no separate nginx/IIS needed)
  - **HTTPS:** Not required — HTTP only, local network access only
  - **Rationale:** Minimal moving parts, no IIS/nginx configuration, maintainable by a small IT team without a dedicated sysadmin

### Backups
- Nightly full `pg_dump`, automated via Windows Task Scheduler (or a scheduled Django management command)
- Stored as timestamped `.sql` files (e.g., `hris_backup_2026-09-26.sql`) in a dedicated folder on the same server (separate drive from OS/DB if available)
- Retention: manual cleanup by HR/IT — no auto-delete logic needed

### Email
- No existing SMTP server. Likely candidate: BDH's **GovMail** account (Microsoft 365 / Exchange Online, via DICT's iGovPhil program)
- **⚠️ TO CONFIRM WITH IT/DICT:** Whether SMTP AUTH client submission (`smtp.office365.com`, port 587) is enabled on the tenant, or whether OAuth2 app registration is required (Microsoft has been deprecating basic SMTP AUTH). Do not hardcode SMTP credentials until this is confirmed — build the email backend as a swappable Django `EMAIL_BACKEND` config.

### SMS
- **Out of scope (owner decision 2026-11-03, replaces the 2026-11-01 "in scope" note).** Do not build SMS. Email stays on hold until GovMail is confirmed; only in-app notifications are live.

---

## 3. User Roles

Implemented as a **custom `Role` model tied to Employee** (not Django's built-in Group/Permission system), because:
- One person can hold multiple roles simultaneously
- OIC (Officer-in-Charge) coverage is ad-hoc delegated authority, not a permanent assignment

| Role | Responsibility | Cannot do |
|---|---|---|
| **Employee** | Views own attendance/leave/CTO/schedule records; submits Leave, CTO, Exchange of Duty, Attendance Correction, Official Business, Official Time, Travel, and other requests | Approve any request |
| **Supervisor** | Initial review/endorsement of requests from employees under their supervision; verifies against schedule, staffing, attendance; may endorse, return, or request clarification | Final approval; HR-level data changes |
| **HR Processor** | Day-to-day encoding/validation: imports biometric files, encodes manual transactions, validates leave/CTO applications, prepares routine reports | Approve/modify transactions beyond assigned authority |
| **HR Administrator** | Manages employee master records, leave/CTO/attendance rules, reference tables, workflows, delegated user access; authorizes HR data corrections | — |
| **Administrative Officer (AO)** | Reviews HR-processed requests for policy/documentation compliance; may return for clarification; issues recommendation to COH | — |
| **Chief of Hospital (COH)** | Final approving authority for personnel time/attendance requests within delegated authority | — |
| **System Administrator** | Technical/infra admin: accounts, auth, permissions, backups, monitoring, security | Routine HR transaction processing/approval (only touches HR data when technically necessary and authorized) |
| **ICTU Staff** | Validates formal attendance corrections whose reason is a biometric/system problem (Offline, Failed Attempt, Wrong Button/Invalid Entry), per the Missed Log Justification Form (added 2026-10-06) | Any other approval or HR data change |

**No "Chief Nurse" or "Unit Head" role (2026-10-27):** both are a **Supervisor** line; the scope decides the reach. Chief Nurse / any section head = Supervisor + Section (Unit blank); unit head = Supervisor + Unit (Section blank); every other role has no scope. The admin role form (`accounts/admin.py`) refuses a Supervisor line with no scope (that would be hospital-wide) and a scope on any other role, and lists each line's scope.

**Role lines (owner decision 2026-11-03, Option B):** one role per line; a person with several roles has several lines, and the menu/queues combine them (HR wins over Supervisor for the home-page scope). Each employee's admin page lists all their role lines (`RoleAssignmentInline`) and can add one. Changing the role on an EXISTING line is refused unless "Confirm role change" is ticked ("This replaces X with Y..."). The System Administration (Django admin) pages use the HRIS look (`templates/admin/base_site.html`: Royal Blue/Gold, BDH logo, "Back to the HRIS", no dark theme, red TEST SYSTEM bar on the test PC).

**COSP OIC (owner decision 2026-11-03, dry-run item 11):** a COSP employee may also be designated OIC, with limited reach: only for a **Supervisor area at unit level, or of a section that has no units**, and only if they belong to that unit/section (`accounts/oic.py cosp_may_cover`). Never for the Chief Nurse (the section head above unit heads, found by structure, not by name), the AO or the COH. Regular employees still need an active Supervisor role to be OIC.

**OIC coverage:** Ad-hoc delegation triggered by the Supervisor themself (not pre-scheduled by HR Admin; the one exception is the HR Administrator designation below).

**HR Administrator designates an OIC (owner decision 2026-11-16, Option B; branch `oic-hr-admin-designation`):** when an approver (Supervisor, AO or COH) cannot designate one themself, an HR Administrator may do it on their behalf on **HR Administration → OIC for an Approver** (`/oic/hr/`, `accounts/oic_hr.py`). A **reason** and a **written instruction** (PDF/JPG/PNG, max 10 MB, content-checked) are required; the same OIC eligibility rules apply; the approver and the OIC are notified; the Audit Log shows "designate (by HR)" with the HR Administrator, reason and that an instruction is attached (`designated_by_hr`, `designation_reason`, `instruction`). The instruction file opens only for HR Administrators, the AO, the COH, the approver and the OIC. The HR Administrator can end an HR-made OIC; the approver can too. The HR Administrator uses their own OIC screen for their own roles. The **System Administrator cannot** designate or end any OIC. One Supervisor can be designated OIC across multiple sections.

**One OIC for several approver roles (owner decision 2026-11-16, Option A):** one person may be OIC for the AO and the COH at the same time (for example the Chief Nurse, as the next in rank, during a short absence of both), and for several areas. No code limit; keep it that way. Known effects: a person cannot act on a request they filed, so their own requests wait at the AO/COH steps until another AO/COH or OIC is available; one person may act at several steps of the same request. Recommended: a written Office Order or memo naming the OIC for both functions.

**OIC rules (settled 2026-10-13, Batch 7):** the approver (Supervisor, AO or COH) designates the OIC on the **Officer-in-Charge (OIC)** screen (`/oic/`, `accounts/oic.py`), for one of their own role areas. Only an employee who already holds an active Supervisor role can be OIC (or a COSP employee, within the limits above). The OIC acts only between the start and end dates (every role assignment's dates are honoured, `RoleAssignment.in_force_q`). While the OIC is in force, the delegator's own assignment for that role and area is set aside (`Employee.active_role_assignments`), so the absent approver cannot act there and is not notified for it. The absent approver keeps **view-only** access to that area (records, printouts, documents, schedules, homepage figures, Dashboard, Audit Log) — view checks pass `viewing=True` (`has_role`, `is_hr`/`is_administrative_officer`/`is_chief_of_hospital`, `is_supervisor_of`); action checks and queues never do (owner, 2026-10-13). "End now" cancels an upcoming OIC or ends a running one yesterday; nothing is deleted. Designations and early endings appear in the Audit Log (module "OIC Designation").

---

## 4. Organizational Structure

**Hierarchy:** COH → AO → Section Heads → Unit Heads → Employees

**Sections** (fixed list, but keep admin-configurable):
- Medical Services Section
- Nursing Service Section — Units: OPD, ER, Isolation/Medical, Pediatric, OB/Surgical, OR/DR
- Radiology Section
- Laboratory Section
- Pharmacy Section
- Health Information Management Section
- Medical Social Service Section
- Accounting and Finance Section
- Human Resources Management Section
- General Services Section
- Procurement, Property and Supply Section
- Dietary Section

**Model notes:**
- Employee-to-Section/Unit is **many-to-many** (an employee can be attached to multiple sections/units, e.g., float staff)
- A Supervisor can oversee multiple sections when designated OIC of another section

---

## 5. Employee Master Data Model

### 5.1 Personal Information (PDS-aligned, per CS Form No. 212)
- Surname, First Name, Middle Name, Name Extension
- Date of Birth, Sex at Birth, Civil Status
- SSS Number, Pag-IBIG, PhilHealth, TIN, GSIS
- Residential Address, Permanent Address
- Telephone/Mobile, Email

### 5.2 Education History (separate table — one-to-many per employee)
- Education Level
- School
- Degree/Course
- Units Earned

### 5.3 Government/Employment Information (HRIS-specific, beyond PDS)
- Employee ID
- Position
- Item/Plantilla No.
- Salary Grade
- Appointment Type
- Employment Status
- Date of Appointment
- Date Hired
- Original Appointment Date
- Section/Unit (FK/M2M to org structure)

---

## 6. Leave Management

### 6.1 Leave Types

**Regular employees** (full CSC leave menu):
- Vacation Leave (VL) / Sick Leave (SL) — standard CSC accrual: 1.25 days/month each
- Mandatory/Forced Leave
- Maternity Leave
- Paternity Leave
- Special Privilege Leave
- Solo Parent Leave
- Study Leave
- VAWC Leave
- Rehabilitation Privilege
- Special Leave Benefit for Women
- Calamity Leave
- Adoption Leave
- Wellness Leave — 5 days/year, non-cumulative (use-it-or-lose-it)
- Emergency Leave — no fixed day-limit if justifiable under hospital policy; non-cumulative

**COSP employees:**
- COSP Leave — fixed 20 days/year per contract, accrual in nature
- Wellness Leave and Emergency Leave also available to COSP, **subject to their leave allowance/credit** (not automatically granted — check balance before allowing). Since 2026-11-03 (owner, dry-run item 3) they follow the full COSP chain and print on 01F04-A, like COSP Leave.

### 6.2 Approval Routing by Request Type

| Request Type | Routing |
|---|---|
| **COSP employees' leave** (COSP Leave, Wellness, Emergency) | Employee → Supervisor (Nursing: Unit Head → Chief Nurse) → HR process → AO recommend → COH **approves**. Days taken at COH approval. Prints on 01F04-A with digital stamps. |
| **Regular employees' leave** (all CSC types, incl. their Wellness/Emergency) | Employee → Supervisor (Nursing: Unit Head → Chief Nurse recommends) → HR **records and verifies** (days taken here) → AO **reviews and endorses** → COH **recommends for approval** → status **For Capitol approval**. The Capitol approves; the HRIS never marks it approved. HR records a Capitol return or rejection. (Owner decisions 2026-11-03, dry-run item 2; replaces "data entry and monitoring only".) |
| **CTO Application** (regular & COSP) | Employee → Supervisor → HR → AO → COH |
| **CTO Claim** (pending approved HR memo — see §7.1) | Employee → Supervisor → HR → AO → COH (Nursing: Employee → Unit Head → Chief Nurse → HR → AO → COH). Exception claims: HR Admin → AO → COH |
| **Exchange of Duty** | Employee A/B → Supervisor → HR → AO → COH |
| **Attendance Correction (formal)** | Employee → **one validator by reason** → AO. ICTU Staff validate Offline / Failed Attempt / Wrong Button-Invalid Entry; HR validates Attended meeting-activity-training / Others. No Supervisor step. (Changed 2026-10-06 by the project owner to follow the Missed Log Justification Form, BDH-ADM-AO-01F10; previously Employee → Supervisor → HR → AO.) |
| **Attendance Correction (minor administrative)** | HR Processor → HR Admin (no formal approval chain needed) |
| **Official Business (Travel Order)** | Employee → Supervisor → HR → AO → COH **recommending approval** → status **Forwarded to the Capitol** → HR records the Governor's decision (approved / disapproved with a remark). (Owner decisions 2026-11-03.) |
| **Local Official Errand** (code `TRAVEL`) | Employee → Supervisor → **AO approves** (matches F59). Kept until the Out-on-Pass / travel authorization module replaces it (owner, 2026-11-03, Option B). |
| ~~Official Time~~ | **Retired 2026-11-03**: it only meant a trip without per diem, now the Travel Order's "Expenses authorized: Official Time" option. Code kept for old records only. |
| **Authorized OT/restday/holiday work** (pending approved HR memo — see §7.1) | Filed by Supervisor/HR/AO as a group request, not the employee. Supervisor-filed: HR → AO → COH. Unit Head-filed (section head above): section head (Chief Nurse) → HR → AO → COH. HR-filed: AO → COH. AO-filed: COH |

**Official Business (Travel Order) and Local Official Errand (owner decisions 2026-11-03):**
- Names: "Official Business (Travel Order)" = an official trip outside the area covered by a **Governor-approved Travel Order**, with or without per diem; "Local Official Errand" = official work away from the workstation during office hours, nearby (Municipal Hall, bank). The stored codes `OFFICIAL_BUSINESS`/`TRAVEL` are unchanged.
- The Travel Order is approved at the Capitol (rank-and-file: COH recommends; per diem goes through the Provincial Budget Office). The COH's own Travel Order is recorded by HR (`recorded_only`, as before). HR may not return a Travel Order already at the Capitol; "Record disapproval" needs a remark.
- New fields: `expenses_authorized` (Per Diem / Official Time) and `transportation_allowed`; destination required for both types.
- **Printout** (`official_requests/travel_order.py`): BDH's own Travel Order template (`printouts/assets/travel_order_base.docx`: letterhead, footer note), filled in, **wet-signature lines, no BDH code, no digital stamps**. "Recommending approval" (COH) except on the COH's own; "Certified as to Availability of Funds" (Budget Officer) **only with Per Diem**; "Approved by" the Governor always. A group request prints one Travel Order listing everyone. Printable once forwarded (or, for the COH's own, once filed). Names of officials: `TravelOrderSettings` (System Administration). Red "TEST SYSTEM" line on the test PC.
- **F59 printout (owner request 2026-11-03, built):** Travel Authorization and Transportation Request, **BDH-ADM-AO-01F59 Rev 0** (`official_requests/f59.py`, BDH's own form `printouts/assets/f59_base.docx` filled in as it is): two identical copies on one page (requestor / HR), every value from the approved errand, digital stamps for Requested (filer), Noted (endorsing Supervisor / unit head; a Supervisor-filed group request counts the filer's step) and Approved (AO, or the COH when the AO is on it). Type of Transport (Ambulance / Service Vehicle / none) is chosen on the errand form and kept in `transportation_allowed`. "Print F59" appears once the errand is **approved**; an approved group errand prints one F59 listing every rider.

**Attendance Correction, multiple dates (settled 2026-10-07):** one request holds 1–10 dates, each with its own times and reason. **Times per date (owner 2026-11-03, dry-run item 10): AM in, AM out, PM in, PM out, OT in, OT out** (at least one, or Absent). `time_in` = AM in and `time_out` = PM out, so older lines read the same. On approval the attendance record keeps all six; undertime is still worked out from the first in (AM in, else PM in) and last regular out (PM out, else AM out), unchanged; OT logs never create CTO. F10 prints AM / PM / OT columns. All dates in one formal request must belong to the same validator (ICTU-type and HR-type reasons are filed separately). Approvers act on the whole request only (validate/approve, return, reject); **a return needs a remark**. The filer edits and resubmits the same request, which **starts again from the first step**; the return history is kept and printed on F10.

**Group (batch) filing — Travel, Official Business, OT / rest-day / holiday work (settled 2026-10-07):**
- Who files: **Supervisor** (own sections/units incl. OIC for Travel/OB; anyone for OT), **HR Processor** (any employee; all types — OT added 2026-11-02, D5), **HR Administrator** (any employee; all types), **AO** (any employee; OT only, D5). The filer cannot include themselves or the COH. OT details: §7.1.
- One batch; each employee-date is its own OfficialRequest line. Whole-batch approve/return (remark required); filer edits and resubmits, starting the route again.
- Routes (filer's step counts as done): OT/rest/holiday by Supervisor → HR → AO → COH; by HR Admin → AO → COH. **Local Official Errand by Supervisor or HR → AO approves. Official Business (Travel Order) by Supervisor → HR → AO → COH (recommending) → Capitol, HR records; by HR → Supervisor → AO → COH (recommending) → Capitol, HR records** (2026-11-03).
- The filer can never act on their own batch at a later step, even with other roles or OIC. Approved OT creates no CTO (§7.1 unchanged). A regular employee filing their own request keeps the normal route.

**No self-approval; next level up (owner decision 2026-10-26, pilot import):**
- Nobody acts on a request they filed — or, for an Exchange of Duty, one they are a party to — at **any** step (endorse, process/validate, recommend, approve, record). Applies to COSP and regular leave, CTO, Exchange of Duty, attendance corrections (formal and minor), Official Business/Time, Travel, OT and group filings; HR never files or credits their own CTO claim. Queues and notifications leave the filer out.
- **Two-level endorsement (owner decision 2026-10-27, Option B; supersedes the single Supervisor step for these cases):** where an ordinary employee's unit has a unit head AND the section has a section-wide Supervisor (Nursing: unit head, then Chief Nurse), the Supervisor step is done twice — `SUBMITTED → ENDORSED_BY_UNIT_HEAD` (unit head, action `endorse_unit`) `→ ENDORSED_BY_SUPERVISOR` (section head) — for COSP leave, CTO, Exchange of Duty, Official Business, Travel and OT. A unit with no head, a section without units/unit heads, and a unit head's own request keep one step. Either level may return. Strict order (owner, 2026-10-27, Option A): the section head cannot act at the unit-head step; an absent unit head designates an OIC. Group (batch) requests keep ONE endorsement, by the unit head or the section head. Forms print a "Unit Head" stamp before "Section Head" when the unit step applies (`printouts/stamps.py with_unit_head_step`). Helpers: `endorsers_for` (first endorsement), `second_endorsers_for`, `needs_unit_step`, `supervisor_step_result`.
- **Supervisor step = one level above the filer** (`leave/permissions.py endorsers_for`): an ordinary employee → any Supervisor of their section/unit; a **unit head** → the section-wide Supervisor (e.g. the Chief Nurse), never a fellow head of the same unit; a **section head** (no one above at Supervisor level) → the **AO**; the **AO's own request → the COH** (at the Supervisor and AO steps). The person who acts is stamped with their own name and position.
- **Endorser is not the HR step (owner decision 2026-11-03, Option B):** whoever endorsed a request in its current cycle (unit head or Supervisor step) cannot act on it at the HR step, and is left out of that HR queue and notification; another HR Processor/Administrator must process it. A resubmission starts a new cycle. Helpers: `endorser_ids`, `exclude_own_endorsements`; `can_act_hr_step(..., obj)`, `hr_step_actors(..., obj)`.
- **Multi-role staff (2026-11-03):** attach a person's employee record to their home section only; a unit headship elsewhere comes from the Supervisor + Unit role line, not from unit membership (membership in another section would route their own requests to that section's head).
- **A return or a rejection always needs a remark (owner requests 2026-11-03):** leave (COSP), CTO, Exchange of Duty, Official Business/Time/Travel/OT, group requests and attendance corrections; Return and Reject stay disabled until a remark is typed and the server refuses either without one ("Nothing was changed"). The filer's notification quotes the remark (`remark_suffix`).
- **Group requests can be edited while pending (owner request 2026-11-03):** the filer may edit a group request while no approver has acted since it was filed/resubmitted (`batch.pending_untouched`, `filer_may_edit`); it stays at its first step and is logged as "edit". Once an approver acts, only a return reopens it.
- HR/ICTU steps: any other holder of the role. AO step: the AO, or the COH when the AO is the filer. COH step: never the COH's own request.
- **The COH's own Official Business / Official Time / Travel / OT (owner decision 2026-10-26, Option A):** recorded by HR only, like regular leave — `OfficialRequest.recorded_only`, SUBMITTED → RECORDED ("Recorded (approved outside BDH)"); the approval itself is at the PHO/Capitol. The COH cannot be included in a group (batch) filing.
- **Import scope (same date):** a Supervisor with a unit on their import row supervises that unit only; with no unit, the whole section. The COH and AO may be imported with no section (hospital-wide). A unit must belong to a section on the same row.

**Leave routes (owner decisions 2026-11-03, dry-run items 2-3; replace the old "regular leave is data entry only" rule):**
- `LeaveApplication.route` is fixed when the application is (re)submitted: a COSP employee → `COSP`; a regular employee → `REGULAR` (`route_for`). Existing records were set by migration `leave 0006`.
- Regular route: approval authority stays at the Capitol/PHRMO. Inside BDH the COH only **recommends**; the final status is `FOR_CAPITOL_APPROVAL` ("For Capitol approval"). Statuses reuse the COSP codes with their own labels (`status_label`: "Recorded and verified by HR", "Reviewed and endorsed by AO", "Recommended by Section Head" after a unit-head step). Actions: `record` (HR), `review` (AO), `recommend_capitol` (COH), `capitol_return` / `capitol_reject` (HR, remark required, on the application page; "Regular leave for Capitol approval" list on the leave queue, not counted as waiting).
- **HR cannot reject a regular leave** (may return it). The Supervisor/Chief Nurse, AO and COH may return or reject. Every return/rejection needs a remark.
- **Credits:** taken when HR records and verifies (regular) or when the COH approves (COSP). **Given back automatically** (a ledger adjustment, nothing deleted) on any return or rejection after they were taken, inside BDH or by the Capitol (`_restore_credits`). A resubmission takes them again at the HR step.
- Absences, balances and Dashboard "used" figures count leave whose days are taken: `LeaveApplication.counted_q()` (approved, older recorded, for Capitol approval, or regular leave from the HR step on).
- **COH's own regular leave** (no level above): HR records and verifies it and it goes straight to For Capitol approval (assumption, matching the COH's other requests; confirm with the owner if needed).
- Printing: regular leave prints CSC Form No. 6 (no BDH code, no digital stamps); COSP route prints 01F04-A (title and credits follow the leave type). **Print timing (owner decision 2026-11-18, pilot issue 2; replaces "printable once the internal chain is complete (2026-11-18)", Nursing included):** the person who filed the application (whatever other roles they hold) can print only when every internal step is complete: COSP route = COH approved; Regular route = COH recommended ("For Capitol approval"; the Capitol's decision is not needed). Older RECORDED records stay printable. Before that, the button shows "Available once all internal approvals are complete."; returned, rejected or cancelled requests cannot be printed; a resubmission starts over. Enforced in the print views (`leave/permissions.py print_wait_reason`), so the direct link is blocked too. **CSC Form No. 6 of someone else (Part B, owner 2026-11-27): HR only** — see §16 Part B; the COSP form (01F04-A) keeps HR, AO, COH and Supervisors printing other people's applications unchanged. Only these two leave print views are affected (CTO, Exchange, Attendance, Travel/OB and Schedule forms have their own views).
- `RECORDED` (HR only) is the old regular path, kept for records filed before 2026-11-03.

### 6.3 Output Forms
- COSP employees' leave (COSP Leave, Wellness, Emergency): custom BDH-branded form, **Form No. BDH-ADM-HR-01F04-A**
- Regular employees' leave: CSC Form No. 6 layout — official CSC form, left as is (no BDH code, no digital stamp, because regular leave is approved at the Capitol); printable once the internal chain is complete (2026-11-18)
- CTO application: **BDH-ADM-HR-01F04-B**
- Exchange of Duty application: **BDH-ADM-HR-01F04-C**
- Attendance Correction (formal): Missed Log Justification Form **BDH-ADM-AO-01F10, Revision 2**

### 6.3.0 CSC Form No. 6 details (Batch 8, 2026-10-16)
- The leave application collects **6.B Details of Leave** as on the 2020 form: Vacation/Special Privilege Leave (Within the Philippines / Abroad + place; place required if abroad), Sick Leave (In Hospital / Out Patient + illness), Special Leave Benefits for Women (illness), Study Leave (Master's degree / BAR-Board review), and Other Purpose (Monetization / Terminal Leave, offered with VL and SL). Only the fields for the chosen type show and are kept; the required ones are enforced. **6.D Commutation** is collected (Requested is forced for monetization/terminal leave).
- Monetization / terminal leave has **no inclusive dates** (confirmed by the owner, 2026-10-16): the date fields are hidden and cleared (start/end dates are nullable only in that case); commutation is Requested; when HR records it the days are deducted as of the filing date.
- **Wellness Leave** requires a reason (Mental Health Care / Physical Wellness Activities / General Break from Work, per CSC MC No. 1, s. 2026 / CSC Res. No. 2501292). The 2020 form has no Wellness row, so it prints on the "Others:" line as "Wellness Leave - <reason>" (owner chose to print the reason, 2026-10-16).
- Each leave application has a details page (`/leave/<id>/`). The filer may edit it while **Submitted** (before anyone acts) or after it is **Returned**; a returned one is resubmitted and starts again from the first step. Routing and approval rules are unchanged.

### 6.3.1 Paperless Processing (settled 2026-10-06)
- The HRIS is meant to be paperless; printing is incidental, on request.
- BDH forms (01F04-A/B/C, F10) print a **"digitally processed/approved" stamp** for each routing step — the person's name, position, date and time from the request's action log — **instead of signature lines**. Steps not yet reached print as PENDING; a returned/rejected request prints a red banner with the remarks.
- Whether these stamps are accepted in place of wet signatures by the Capitol, COA or CSC is a BDH policy matter (e.g. an Office Order), not something the system decides.

### 6.3.2 Date change of approved leave or CTO; cancelling a pending one (owner decisions 2026-11-19, Part 2; branch `date-change-approved-leave-cto`, not merged)
- **Date change** (`datechange` app): the employee, on the detail page of their **own approved** request ("Request date change"): COSP leave approved by the COH; regular leave once the COH has recommended it (For Capitol approval, or older Recorded); CTO approved by the COH. Only **before the first original date**; one open change per request; new dates must be in the future; a **required justification**.
- Checks (`datechange/rules.py`): the **number of days never changes** (dates too short for it are refused; approvers check the schedule, since there is no holiday calendar — owner choice, Option A); the new dates stay in the **same year** (and for COSP Leave the same contract year — owner choice); **no overlap** with the employee's other pending/approved leave or CTO or other open date changes; CTO also passes `cto.balances.usage_rule_error` (the same code a new CTO application uses: 5 days a month, no 3+ consecutive days, last day of service) and stays within the **effective date and use deadline of the credit it already holds** (never extended). Everything is checked again before the last step.
- Route: the **same as the original** (`next_step`, built on `leave.permissions`): Supervisor (unit head then section head where two-level applies) → HR → AO → COH approves (COSP leave, CTO) or recommends (regular leave); the COH's own regular leave: HR only. HR may not reject a regular leave's change. Return/reject need a remark; the original dates stay; the employee may ask again.
- When the last step acts, the **same record takes the new dates** (`start_date`/`end_date`); days, ledger lines and CTO credit held are untouched, so the balance never moves. Printouts read the record, so they show the new dates only after approval; layouts unchanged. Regular leave: the employee is told to print the updated CSC Form No. 6 and send it to the Capitol.
- Records: `DateChangeAction` log shown on the change page, as "Date changes" on the original's page, and in the Audit Log (module "Date Change": old → new dates, justification, each step). Notifications go to the next person to act and open the change request. Waiting change requests show on the Leave and CTO approval pages and in their menu counts.
- **Cancel** (owner 2026-11-19): the filer may cancel their own leave or CTO application while it is **pending or returned** (`is_cancellable_by_filer`); regular-leave days already taken at the HR step come back (`_restore_credits`); held CTO credit is released; logged as "cancel". Approved requests are never cancelled — their dates are changed instead. CTO applications now have a detail page (`/cto/<id>/`).

### 6.3.3 CTO can cover COSP leave (owner decisions 2026-11-19, Part 3; branch `cto-covers-cosp-leave`, from the Part 2 branch, not merged)
- **COSP employees only**, on any of their COSP-route leave (COSP Leave, Wellness, Emergency). On the leave form they may tick "Charge some of these days to my CTO" and give the number of CTO days (half days allowed) and **one date range inside the leave dates** (implementation choice; a range keeps the existing CTO checks). Default: leave credits only. Chosen when filing (or when correcting a Submitted or Returned application), never on an approved leave. Regular employees never see it.
- **One application, one route:** the normal COSP leave route. The CTO days are held in a `CTOUsageApplication` linked to the leave (`cosp_leave`, `leave/cto_cover.py`) that is **never routed on its own**: it follows the leave (approved with it; returned, rejected or cancelled with it, releasing the CTO days). Queues, home-page and Dashboard CTO figures use `CTOUsageApplication.standalone()`, so such a leave is counted once, as leave. The linked record cannot be opened, printed, cancelled or date-changed on its own; everything goes through the leave.
- **CTO rules on the CTO days only**, from the existing code: credit is picked and held at filing by `plan_allocation` (effective date arrived, not past its deadline, oldest first); `usage_rule_error` checks 5 days a month, no 3+ consecutive days (counting other CTO), last day of service. Days charged to leave credits are not subject to the CTO limits.
- **Balances:** CTO held at filing; leave credits deducted at COH approval for the **rest only** (`leave_credit_days`). Both are checked at filing; at COH approval the CTO credit held is checked again (`error_at_approval`) and, if it is no longer good, the leave is **returned automatically** with the reason.
- **Date change (Part 2) keeps the split:** the CTO days move by the same number of days as the leave's start, must stay inside the new dates, and are checked again against the CTO rules and the held credit's deadline.
- **Records:** the leave's log (and so the Audit Log) records the split on filing/resubmission ("N day(s) charged to CTO (dates); M day(s) to leave credits"); the leave page shows "Charged to"; the leave queue shows "(N charged to CTO)".
- **Printed 01F04-A (owner decision 2026-11-19, Option B):** layout unchanged; only the value on the existing "No. of Days" line shows the split, e.g. "3 (2 from COSP Leave credits; 1 charged to CTO: Dec 02, 2026)" (`cosp_leave_form.days_label`). A leave with no CTO prints as before.

### 6.4 Self-Service Restrictions
Employee profile edits are self-service only for: contact information, address, civil status — and all such edits require Administrator approval before taking effect.

---

## 7. CTO (Compensatory Time Off)

Applies to both regular and COSP employees. Based on BDH's approved (Dec 16, 2025) HR Policy Revision Proposal on CTO and Exchange of Duty.

- **Earned from:** authorized OT, rest-day work, holiday work, certified emergency duties. **Not applicable to the Chief of Hospital.**
- **Conversion multiplier:** OT hours × **1.0** (weekday) or × **1.5** (rest day/holiday), per CSC-DBM Joint Circular No. 2, s.2004. **Multipliers must be configurable, not hardcoded** — do not assume these values are permanently fixed in code.
- **Monthly cap:** max 5 CTO days/month, no 3+ consecutive days — except for separating employees (§7.1), who are exempt from both limits
- **Filing, forfeiture and deadlines:** see §7.1, which replaces the earlier rules here. CTO filing deadline is Nov 30; CTO usage dates cannot extend past Dec 15 (except via §7.1 carry-over and separation rules).

### 7.1 OT and CTO claim rules — PENDING APPROVED HR MEMO (decided 2026-11-01, ported onto main 2026-11-02)

Decided by the project owner in a 2026-11-01 design session; these **replace** the 2026-09-28 decisions (HR-filed claims, 1st-of-next-month filing window, COH exception for Nov/Dec work). On 2026-11-02 the owner decided how they run on main (decisions D1-D7 and 8a-8i, marked below). They are pending an approved HR memo. Do not re-ask or change them without explicit confirmation.

**General**
- OT never becomes pay. Approved OT does not create CTO credit by itself. Unused CTO lapses and is never paid.
- CTO does not apply to the COH (Dec 16, 2025 policy). The COH is never on an OT group request. The COH files their **own** OT on the single-request form; HR records it ("Recorded (approved outside BDH)", like the COH's other requests); it never becomes a CTO claim or credit (D2, 8a; confirmed 2026-11-02). The COH has no CTO entry under My Requests.
- Biometric attendance can inform HR review but never blocks an approval.

**OT requests** (main's group filing, `official_requests/batch.py`; the separate cto OT module was removed, D4)
- An OT request is an `OfficialRequestBatch` of type OT / rest-day / holiday work; each employee-date is an `OfficialRequest` line. The day type comes from the batch's work kind, one per batch (8d). Older lines without a batch map the old flag: yes → rest day, no → weekday (8h).
- Filed only by a Supervisor, HR Processor, HR Administrator or AO — never by the employee (D5). Employees have no OT choice on the single-request form (8a; the COH's own OT is the one exception, above).
- Filing role, when someone holds several: AO, then HR Administrator, then HR Processor, then Supervisor — the first that may file the type (8b). The AO files OT only. A unit head with a section-level Supervisor above them files OT as "Unit Head" (D6).
- A filer may add anyone except the COH and themselves. Past dates need a typed reason (`past_date_reason`). Lines dated after an employee's last day of service are blocked. Each Supervisor covering the employee gets an in-app notice, information only, when the employee is put on someone else's OT request (D5).
- Duplicate guard: an employee-date already on a pending, returned or approved request can't be filed again (8e); the filer removes the line.
- Routing (D6): Supervisor-filed HR → AO → COH; Unit Head-filed section head (Chief Nurse) → HR → AO → COH; HR-filed AO → COH; AO-filed COH. The batch is approved or returned as a whole. A return needs a typed remark, goes back to the filer, and the chain restarts.
- **8i (2026-11-02):** when a Unit Head files an OT group request that includes the Chief Nurse, the Supervisor step goes to the AO (next level up; the Chief Nurse is on the request and never acts on it). If the AO is also on it, the COH takes the step (D1).
- **Editing a returned group request (security rule, 2026-11-02):** only under the same filing role it was filed with (that role decides the route), and the employees allowed come from the roles held now. If the filer's roles have changed, they file a new group request.
- The old "warn if the filer adds someone on the batch's own route" rule is dropped; the next-level-up rule below covers it.

**Who acts — next level up (D1; replaces "Approver is the subject")** (OT requests and CTO claims)
- Same rules as every other request type (§6.2, `leave/permissions.py`): Supervisor step → the unit head (incl. an OIC), else the section-level Supervisor, else the AO, else the COH; the second Supervisor step (Chief Nurse) only where two-level endorsement applies (`needs_unit_step`); HR → any other HR Processor/Administrator; AO → the AO, or the COH when the AO is on the request; COH → never on their own.
- Nobody on the request/claim (any line of a batch, the claimant, the filer of an exception claim) ever acts on it. Steps are **never skipped**: a step nobody can act on waits.
- Chief Nurse = a Supervisor covering the whole section; unit heads = Supervisors scoped to a unit. Found by role scope, not by section name (8f: `NURSING_SECTION_NAME` removed). No new role.

**CTO claims**
- Filed by the employee any time after the workday (My Requests → CTO → "+ New CTO claim"). One claim may cover several of that employee's lines; it is approved or returned as a whole, but credit, effective date, filing deadline and year earned are tracked per line. Document requirements attach to the claim (D7).
- **OT claims:** lines come from the employee's **approved** one-day OT lines (D4; recorded lines never count, D2); actual hours ≤ approved hours per line. Attachments: Allowed to Work form, DTR/logbook copy, OT Accomplishment Report.
- **Medical Transport claims:** no OT request; the employee enters date, hours and day type, and HR verifies. Attachments: Trip Ticket plus Certificate of Appearance or logbook copy. Always separate claims from OT claims; same routing as OT claims.
- **Exception claims:** HR Administrator (never for themselves); the AO only when no other HR Administrator exists (D3). Required reason, no OT request, routed AO → COH; the filer never acts on it. They **skip the filing-deadline check** — the typed reason is the justification (decided 2026-11-01). The resulting credit still follows the carry-over rules by its work date.
- **24-hour cap:** no more than 24 hours per employee per date, counting OT and Medical Transport lines together.
- **Filing deadline:** each line must be filed by Nov 30 of the year containing the 1st of the month after its workday (Nov work → Nov 30 same year; Dec work → Nov 30 next year). Judged on the claim's **first** filing date, so a returned claim does not expire while being corrected. A line already past its deadline cannot be added.
- **Returns:** typed remark required; goes back to the employee; the chain restarts.
- **Day type:** from the OT line. HR Processor or HR Administrator may correct it on the claim (not the OT line) with a typed remark; audit-logged; credit recalculated.
- **Credit** = hours × multiplier for the day type, using the `CTOMultiplierRate` in force on the **workday**.

**When credit counts**
- Effective date = later of the 1st of the month after the workday and the COH approval date. The balance counts only credits whose effective date has arrived. **Changed on branch `item4-cto-usable-from` (owner decision 2026-11-26, Option B): effective date = the COH approval date; year earned and use deadline unchanged — see §16 Item 4.**
- Year earned = year of the effective date (December work → credited Jan 1, counts as next year's CTO).
- Use draws from the earliest effective date first, carried-over credits ahead of the rest.
- Days in a usage application still awaiting approval are held aside. Approved use dated on or before Dec 15 counts against that year even if approved later.
- The CTO usage-application chain is unchanged.

**Year-end carry-over**
- Credit from **work done** on or before Nov 30 that is still unused after Dec 15 carries over only if the employee's next level up submits a written reason by end of Dec 16 and the COH approves. One reason per employee per year covers all their at-risk credits, goes straight to the COH, and may be submitted early. Pending credit cannot be used; COH rejection forfeits it at once.
- Credit from work done after Nov 30, and any credit whose effective date is after Dec 15, carries over with no reason.
- **Who submits the reason (8g, 2026-11-02; replaces the 2026-11-01 rule):** the next level up (`endorsers_for`): the unit head, else the section-level Supervisor (Chief Nurse), else the AO — never the COH, who decides it. If nobody qualifies, an HR Administrator. An HR Administrator's own credit is submitted by the AO; the AO's own credit by an HR Administrator. The submitter is never the employee.
- **Rejection:** final for that employee and year (no second reason). A rejection before Dec 15 leaves the credit usable until Dec 15; it is then forfeited on Dec 17. A rejection after Dec 15 forfeits the pending credit at once.
- On Dec 17, credits with no reason submitted are forfeited. Credits with a reason stay pending until the COH decides.
- A carried-over credit faces the next year's Dec 15 deadline and needs a new reason if still unused.
- Reminders to Supervisors and HR (at-risk list) and to the COH (undecided reasons); dates in settings (default Dec 1, Dec 10, morning of Dec 16). The employee is notified of forfeiture, of a reason submitted for them, and of the COH decision.

**Separating employees**
- HR (Processor or Administrator) sets a separation status (COSP: not renewing / resigned; Regular: resigned / retired) and last day of service; audit-logged. Never for themselves (added 2026-11-01 after a security review: the status waives their own CTO limits) — another HR Processor or Administrator does it; the AO only when no other HR person exists (D3).
- Waived: effective-date wait, Dec 15 use deadline, 5-day monthly limit, 3-consecutive-day limit. The Supervisor still approves CTO dates.
- Their pending claims are flagged to approvers and HR. After the last day of service, remaining credit lapses (nightly job, archived employees included) and claims still in the chain lapse with no credit.
- Clearing the status restores normal rules; CTO already used stays used.

**Opening balances (Option A, decided 2026-11-01)**
- Existing CTO credit is entered as **several opening lines per employee**, each with days, date earned and a required reason (may cite the paper record). Never collapsed into one balance at a cutoff. The Data Import has no CTO column (8c; an old template with a CTO value is refused).
- Each line follows the normal rules by its own date earned: year earned = year of the date earned; credit earned on or before Nov 30 needs a carry-over reason if unused after Dec 15; credit earned after Nov 30 carries over without one. A line counts in the balance from the day it is entered.
- Date earned must be in 2026 (the year of `CTO_OPENING_BALANCE_DATE`). Older credit is not entered here; it goes through an exception claim.
- HR Administrator only, never for themselves. If no other HR Administrator exists, the AO enters them. No approval chain; every add and void is audit-logged.
- `CTO_OPENING_BALANCE_DATE` (default Nov 30, 2026) only locks entry. Up to and including that date, a wrong line is voided with a typed reason and re-entered — nothing is edited in place. A line with CTO days already held or used against it can't be voided. After the lock, corrections go through an exception claim (AO, then COH).
- Implementation: lines are `CTOClaimLine`s on the employee's single opening record (`CTOClaim.is_opening`, one per employee, never routed). See `cto/opening.py`.

**Menu (2026-11-02):** Approvals → CTO Year-end Carry-over (every approval role). HR Administration → CTO Opening Balances and Exception CTO Claim (HR Administrators; the AO only while at most one other HR Administrator exists), CTO Separation Status (HR Processors/Administrators; the AO only while at most one other HR person exists). Pages keep their own checks.

**How Stage 2 implements this (2026-11-01)**
- Every credited line has a `use_deadline`: Dec 15 of its year earned, or Dec 15 of the next year if it took effect after Dec 15. CTO can only be used on dates up to a line's deadline (this replaced the blanket "no use after Dec 15" check). An approved carry-over moves it forward one year.
- `cto_year_end` runs **daily** and does nothing new until Dec 17. It handles every credited line whose deadline and reason deadline (Dec 16) have passed: approved reason → carry over; pending → held, not usable; rejected or none → forfeited.
- `cto_separation_lapse` runs nightly. `cto_carryover_reminders` runs daily and acts only on `CTO_CARRYOVER_REMINDER_DATES`. Logic is in `cto/carryover.py` and `cto/separation.py`. Schedules: DEPLOYMENT.md §8a.
- Separation status and last day of service are `Employee.separation_status` / `last_day_of_service`. They are separate from `separation_date`, which is the System Administrator's record-retention date.
- Migration `cto 0006_ot_cto_claims` stops, changing nothing, if the old CTO ledger tables hold rows.

**Open questions (not built — ask the project owner)**
- **Exception claims after a rejected carry-over reason:** can an exception claim restore credit forfeited by a rejected carry-over reason? (Rejection is final; undecided.)
- **Reductions after the opening-balance lock:** an exception claim can only add credit. How BDH wants a wrong opening line reduced after `CTO_OPENING_BALANCE_DATE` is undecided (deferred 2026-11-01). Until then, only voiding before the lock reduces credit.

**Office Order draft (2026-11-19):** the owner asked for an Office Order stating these rules for approval and chose to add CSC-DBM JC No. 2, s. 2004's caps: **40 hours of CTO earned per month** and an **unused balance of at most 120 hours**. **Not enforced by the HRIS yet** (HR monitors by hand); build only once the Office Order is approved.

**Configuration:** `CTO_FILING_DEADLINE_MONTH_DAY`, `CTO_USAGE_CUTOFF_MONTH_DAY`, `CTO_CARRYOVER_REASON_DEADLINE_MONTH_DAY`, `CTO_CARRYOVER_REMINDER_DATES`, `CTO_OPENING_BALANCE_DATE` and reminder dates in settings; multipliers in the `CTOMultiplierRate` table.

---

## 8. Exchange of Duty

- One-for-one exchange only
- Max 3 requests/month
- Requires mutual written consent between the two employees, plus Supervisor endorsement as part of the full Employee→Supervisor→HR→AO→COH chain (Supervisor endorsement is a step within that chain, not a substitute for it)
- Must never generate extra OT, extra CTO, or extra pay (reaffirmed by the owner 2026-11-26: both duties must be the same length; coverage of a longer duty is filed as OT)
- **Timing:**
  - Normal (regular) requests: filed and approved before the next month's schedule is submitted (owner clarification 2026-11-03, §8.1)
  - Modifications to an already-approved schedule: 24–72 hours notice
  - Documented emergency: same-day filing allowed, with written follow-up within 24 hours

### 8.1 Exchange of Duty and the duty schedule (Batch 5, 2026-10-09; reworked by the owner 2026-11-03)
- **Who may exchange (owner decisions 2026-11-26; replace the 2026-11-03 same-unit rule for requests filed from then on, `DutyExchangeRequest.new_rules`; older requests keep the rule they were filed under):** partners must share a **section, in any unit** (float staff qualify when any section matches), and their **positions must be in the same Exchange group** (`exchange/schedule_checks.py location_errors`, `employees/positions.py`): both in the same group = allowed; different groups, or one in a group and the other in none = blocked; neither in a group = allowed. The comparison uses the **Position record** (`Employee.position_ref`, kept in step with the typed title on every save; case and spacing ignored), not the text. Groups seeded by migration `employees 0013`: **Nurse** (Nurse I/II/III, Clinical Nurse, ROS Clinical Nurse, ROS Clinical Nurse II, Nurse II/ Chief Nurse, Nurse II/ HRMO Designate), **Nursing Attendant** (Nursing Attendant I, Nursing Aide I/II/III), **Midwife** (Midwife II, ROS Midwifery Practitioner III), **Medical Technologist** (Clinical Medical Technologist, Medical Technologist II, ROS Clinical Medical Technologist I, ROS Medical Technologist I), **Radiologic Technologist** (Radiologic Technologist I/II, Clinical Radiologic Technologist, ROS Radiologic Technologist I/II/III), **Pharmacist** (Pharmacist I/II, Clinical Pharmacist, ROS Clinical Pharmacist I/II/III); every other position starts with none. HR Administrator keeps them on **HR Administration → Positions and Exchange Groups** (`/exchange/positions/`; can add a group; Audit Log).
- **Equal length (owner decision 2026-11-26):** both duties must have the same paid hours; an exchange never creates OT, CTO or pay (§8 kept). Coverage of a longer duty (e.g. an 8-hour supervisor covering a 12-hour duty) is filed as **OT** (group request by the Chief Nurse, HR or the AO; the person covering cannot file their own, D5) plus an HR schedule correction, not as an exchange. The unequal-length extra-hours / make-up design was dropped.
- **Duty checks (owner decisions 2026-11-26, `exchange/duty_checks.py`):** for each employee, the duty they take over is compared with their nearest duty before and after it (the duty they give up removed first), read from every schedule including drafts; only those pairs count, so a problem already elsewhere never blocks. Blocked, no override: **straight duty** (no gap or an overlap); **PM to AM** (a duty starting at or after 12:00 noon, then one starting before noon on the next calendar date, even with the rest met; each duty counts on the date it starts); **rest** (less than 8 h after an 8-hour duty, 12 h after a 12-hour duty, otherwise the length of the duty just finished; length = paid hours). **Warning only:** more duty days in a row than the employee's pattern (`Employee.shift_hours`: 12 → 3, 8 → 5), shown to the unit heads and Chief Nurse in the queue (`warnings`). Numbers on **HR Administration → Exchange of Duty Rules** (`ExchangeRules`, HR Administrator, logged). At schedule submission, rest problems next to locked exchange days are **flagged, not blocked** (D6, Option A; shown and kept in the submit log).
- **Cross-unit route (owner decision 2026-11-26):** same section, different units, both units with a unit head: **both unit heads endorse, in either order** (`ENDORSED_BY_ONE_UNIT_HEAD`, action `endorse_unit_first`, then `endorse_unit`), then the Chief Nurse and the rest of the chain. Each side's head comes from the shared lookup run for that party alone (`exchange/permissions.py unit_sides`), so OIC and next-level-up rules apply; a party who is a unit head (or a unit with no head) has no unit step on that side, and the existing single endorsement applies. Same-unit route unchanged. The 01F04-C layout is unchanged (it shows the second, completing unit-head stamp).
- **Both units' drafts:** each date belongs to the giver's schedule: the giver is OFF there and the taker is added as a row working the shift there, locked; the taker's own schedule keeps them OFF (`exchange/regular.py _moves`, `giver_schedules`).
- **Three kinds, decided by the system at filing from the month's schedule** (`DutyExchangeRequest.kind`, `exchange/regular.py kind_for`; owner decisions 2026-11-03):
  - **Regular** — that month's schedule is not submitted yet (none, or only a draft). Policy: requested **and approved before the supervisor submits** the schedule. Both shifts are picked on the request (`shift_a` = A's shift on date A, which B works; `shift_b` = B's shift on date B, which A works; duty shifts only). Filing closes **7 days before the month's cut-off**. **Cut-off dates (owner decision 2026-11-03, Option A):** HR sets one date per month, hospital-wide, on **HR Administration → Schedule Cut-off Dates** (`/schedules/cutoff-dates/`, `ScheduleCutoff`, next 6 months, date must be before the month starts) before drafts are started; new drafts copy it and drafts not yet submitted follow a change; with none set, the 20th of the month before (`SCHEDULE_CUTOFF_DAY`). The Exchange of Duty form shows the last filing day for the next two months. Not checked against a schedule (there is none yet).
  - **Change to an approved schedule** (`MODIFICATION`) — the schedule is approved/recorded: **24 hours' notice**; checked against the schedule (A on duty on date A, B on date B, each off on the other's date); shifts copied from it.
  - **Emergency** — "Documented emergency" ticked: same day allowed; written follow-up within 24 hours (checked by HR, not tracked by the system).
  - While the schedule is **submitted but not yet approved**, only an emergency can be filed. One date in an approved month and one in an unprepared month must be filed separately.
- **At COH approval:**
  - Regular → written into that month's **draft** (giver OFF, taker the shift), or into the draft when it is created later (`apply_to_drafts`, `apply_all_to_new_draft`). Those days are **locked** in the draft (`locked_cells`; `schedules.services.save_grid` ignores changes to them, the grid shows them disabled with a gold outline, the pattern tool and Excel upload skip them, and an employee with a locked day can't be removed from the schedule).
  - Change / Emergency → written into the submitted/approved schedule (`schedule_checks.apply_to_schedules`) with change history; **never into a draft**. If the schedule changed since filing, approval stops and the approver returns it.
- **Submitting a schedule** (`services._check_exchanges_before_submit`): refused while a regular exchange for that month is still in the chain after consent; one still waiting for the partner's consent is **cancelled** (both employees notified).
- **Supervisor visibility:** "Approved Exchanges of Duty" (Workforce menu, `/exchange/approved/`, by month; Supervisors see their own units/sections, HR/AO/COH all) and a card on the schedule edit and detail pages.
- No one may be on two live or approved exchanges for the same date. Max 3 per month and partner consent are unchanged. Older requests became "Emergency" or "Change" by migration `exchange 0003`.

---

## 8A. Duty Schedules — Form BDH-ADM-AO-01F50, Revision 2 (settled 2026-10-09, Batch 5)

- One schedule per section **or** unit per month; rows = employees, one shift code per day.
- **Shift codes** are kept by the **HR Administrator** (seeded: 8-5 = 8 h, lunch 12–1; 7A-7P = 12 h; 7P-7A = 12 h night; OFF). Total Hours / Total Days come from the codes.
- Prepared by the section/unit **Supervisor** (incl. OIC) **or HR**. Entry: monthly grid with dropdowns + "repeat pattern" tool, or the Excel template (F50 layout).
- Routing (whole schedule): **Prepared → Reviewed (HR) → Recommending approval (AO) → Approved (COH) → Recorded (HR)**. A return needs a remark; the resubmitted schedule restarts at HR review. The preparer never acts on it later (so an HR-prepared schedule is reviewed and recorded by a different HR person).
- **Locks at COH approval** (the approval date is stored per schedule). Shown in "My Schedule" and staff notified only after HR records it.
- After approval, changes come only from an approved Exchange of Duty (change or emergency) or an HR correction with a reason; both are logged (ScheduleChange, Audit Log). Approved regular exchanges are already in the draft and locked there (§8.1).
- Double-booking (same person on duty in two schedules on one day) is **flagged, not blocked**.
- **Save as draft (owner 2026-11-03, dry-run item 5):** a schedule stays a Draft until "Save and submit"; a Draft is seen and edited **only by its preparer** (not HR, AO or COH; nothing is routed or notified). If someone else's draft exists for the same area and month, a new one is refused with the preparer's name. The month's cut-off comes from Schedule Cut-off Dates (§8.1); after submission HR can still change one schedule's own cut-off on its page, before COH approval. The grid has cell lines on screen and in print.
- Printout: long bond landscape, **whole month on one page**, digital stamps for Prepared / Reviewed / Recommending Approval / Approved plus "Recorded by HR", from the action log (current cycle only).
- Schedules are planned shifts, not attendance.

---

## 8D. Office Order and pilot (signed by the COH 2026-10-21; owner decisions 2026-10-26)

- The HRIS Office Order is signed (digital signature 2026-10-21). **Pilot: Nursing Service, Laboratory, HRMS and Business Office, Nov 16 - Dec 18, 2026; hospital-wide Jan 11, 2027** (Section 11). Pilot data, training and the pilot Go/No-Go must be ready by Nov 13.
- **Section mapping (no new sections):** "Business Office" = **Accounting and Finance Section**; **Security Section COSP are placed under General Services Section**.
- **Returned leave stays editable and resubmittable** (Batch 8 behaviour kept). Office Order Section 6.2 ("files a new, corrected request") is to be corrected in a re-signed copy, together with the blank Order number, the "Oder" typo, the April 27 date and the bracketed [2]/[4]/[3] values.
- **Dry-run (from Nov 3) runs on the test PC** (DESKTOP-HG74U4I), on a fresh database `bdh_hris_dryrun` (reset 2026-10-26; the previous `bdh_hris` database is kept as a backup).
- **Owner decision 2026-11-03 (Option B, IT's plan):** the live server may also be used for the dry-run; ICTU then **fully uninstalls and reinstalls** it on Nov 5 before the real import (fresh database, no `BDH_HRIS_TEST_SYSTEM`, latest code). Checks before importing: no red TEST bar, an empty database apart from setup data, `manage.py check_pdf` prints OK.
- **Printable forms (fix 2026-11-03):** every PDF form goes through LibreOffice (`leave/pdf_convert.py`), now run with a private profile folder per conversion so it works under the Windows service account and when two people print at once. `python manage.py check_pdf` tests it and prints the exact reason when it fails.

---

## 8B. Homepage and Announcements (settled 2026-10-09, Batch 4)

- `/hris/home/` is everyone's landing page. **Backgrounds (Batch 10, owner request 2026-10-16, replaces "watermark on the homepage only"):** the homepage shows a photo (`HOME_WATERMARK_IMAGE`, default `images/homepage-bg.jpg`, none if unset) under an ~85% light overlay; every other logged-in page shows a faint (~6%) BDH logo + "RIGHT CARE, RIGHT HERE" behind the content (`PAGE_WATERMARK` on/off, `PAGE_WATERMARK_FIXED` to keep it still while scrolling; default scrolls). Neither appears on the login page or in print; both are display only (`{% block page_background %}` in base.html).
- KPI cards: Headcount (active, not archived); Absent Today (approved or recorded leave, or approved CTO, covering today — one per person; OB/OT/Travel excluded); Pending leave/CTO; Pending Applications plus "waiting for my action". **Biometric data never drives a homepage count.**
- Visibility (server-enforced): HR sees everyone with the leave type; AO and COH see everyone as "Leave"/"CTO" only; Supervisors see own sections; employees see only the number / their own requests.
- **Pending-request lists (owner 2026-11-03, dry-run item 4):** the HR Administrator, AO and COH see every pending request; **HR Processors see only the requests they are involved in** (waiting for their action, or acted on by them; `kpis.involved_requests`, matched per request, not per employee); **Supervisors see those plus every pending request of their own units/sections** (owner update 2026-11-03, `kpis.add_area_requests`) — the card reads "Pending requests I handle". Absent today and the month lists keep the scope above.
- **This month (dry-run item 7):** HR, AO, COH and Supervisors see "On leave this month" and "On CTO this month" beside each other on the home page (same scope and leave-type rules).
- **Notifications (dry-run item 4):** only the next person to act is notified. The OT "for your information" notice goes to the employee's own next-level endorser (unit head, else section head), not every covering Supervisor; a group request at the Supervisor step notifies the common unit head(s) when there are any, else the section head (either may still act). Regular leave at the Capitol notifies the employee and the HR person who recorded it.
- Announcements (Office Order, Policy, Activity): managed by the **HR Administrator only**; Draft → Published → Archived (auto-archive on expiry); editable only as a draft; optional signed PDF (up to `ANNOUNCEMENT_PDF_MAX_MB`, default 50 MB — higher than the 10 MB limit elsewhere); all staff or selected sections/units; staff notified on publish; every change in the Audit Log.
- Archived items stay viewable by the same audience on the Announcements page's **Archive** tab, marked "No longer in effect"; the homepage shows current items only (settled 2026-10-09).

---

## 8C. Look and Navigation (settled 2026-10-13, Batch 6)

- **Palette** (static/css/app.css `:root`): Royal Blue `#173D72` primary (top bar, headings, primary buttons); Gold `#E5B83F` accent (current-menu marker, highlights — never as text on white); Red `#C9363E` only for errors, returned/rejected status and urgent counts. Status colours: pending amber, approved green, returned/rejected red.
- **Icons:** Lucide line icons (ISC licence) kept locally in `templates/components/icon_sprite.html`; `{% load ui %}{% icon "name" %}`. Always beside a text label.
- **Menu additions (Batch 7, 2026-10-13):** My Profile (all), Profile Changes (HR Administrator) and Officer-in-Charge (approvers) under Approvals/My Requests; Import Biometric Attendance and Minor Attendance Correction (HR) under HR Administration.
- **Dark menu (owner request 2026-11-03):** the side menu is deep Royal Blue (`--nav-bg: #0f2a4f`, darker than the top bar) with light text, a slightly smaller font (0.86rem) and the gold current-page marker; hidden in print. Structure, behaviour and the phone/tablet menu are unchanged.
- **Menu hierarchy (owner request 2026-11-18, pilot issue 1):** group headers are bold and larger with a bigger arrow that rotates; sub-items are smaller, normal weight, softer colour, no icon, indented under a thin guide line (`.nav-sub` in `templates/base.html`). The gold marker is drawn by CSS on the current page's link only (the sliding JS bar was removed); a group header never gets it. A group that is only one page of the same name renders as a plain link with no arrow (none today: Home holds Home + Announcements, so it stays a group). Sidebar width 280px so labels stay on one line. Items, order and groups are unchanged.
- **Menu groups (dry-run item 1, 2026-11-03):** a menu group stays open until the user opens another group or clicks outside the menu; only the group holding the current page is open when a page loads (no remembered state).
- **Approver lists (dry-run item 9):** the leave queue shows the leave type with its 6.B details (illness left out: it is medical); the Official Business / Errand / OT queue shows the full reason/purpose.
- **Menu:** defined in ONE place, `employees/navigation.py` (groups since the menu plan of 2026-11-25, on `feature/performance`: Home, My Requests, Performance, Approvals, Workforce, Reports, HR Records, HR Settings; on main still Home, My Requests, Approvals, Workforce, Reports, HR Administration). Each item names the permission flag that decides visibility; pages keep their own server-side checks. Approvals show waiting counts. Notifications only via the header bell. "File for a Group" stays under My Requests. System Administrator gets "User Accounts and Roles" (Django admin).
- **Below 1024 px** the menu opens from a "Menu" button in the header (there was previously no menu on phones).
- Layout widths unchanged: content 1600 px, forms 880 px, tables full width.
- **Home cards per role** (max 5, actions first; `homepage/views.py _role_cards`; figure rules from 8B unchanged): HR/AO/COH — Waiting for my action, Absent today, Pending applications, Headcount. Supervisor — Waiting, Absent today (own sections), Pending in my sections. Employees (Regular/COSP) — My pending requests, Absent today (number), My balances, My schedule this week; ICTU Staff add Waiting (first); System Administrator adds a "User accounts and roles" card (Django admin) and no approval data.
- **Dashboard:** 5 "At a glance" tiles (active employees, on leave today, on CTO today, pending applications, absenteeism rate), then sections that fold and remember their state (Workforce, Attendance and leave, Applications, Workforce profile) and Reports. No figure removed or duplicated; printing opens every section.
- Wide tables on phones/tablets show a "scroll sideways" hint.

---

## 9. Attendance & Biometric Integration

- BDH has an existing biometric system; exports are **CSV and Excel**
- HRIS **imports these periodically** (batch upload by HR Processor) — no live/API integration required
- Biometric data is **advisory, not authoritative**: HR Processor can manually encode/adjust attendance regardless of what the biometric import shows
- Undertime auto-deducts from leave credits
- **Pilot decision (owner, 2026-11-03, Option C):** no biometric import during the pilot (Nov 16 - Dec 18). PALMIRA (BDH's biometric system) has **no Excel/CSV export**; it only prints its monthly report to PDF (one block per employee, 30 days side by side, 4 logs a day, names only, ~11 pages per 5 employees). PALMIRA does store a biometric ID per person. Undertime rules are unchanged (auto-deduct); during the pilot the only HRIS paths that write attendance and deduct undertime are an approved **minor attendance correction** (HR Processor -> HR Administrator) or a formal correction. A PDF reader for the PALMIRA report (Option B) comes after the pilot; matching must use the biometric ID (a one-time ID-to-employee list if the printout cannot show it), never names alone.
- **Undertime during the pilot (owner, 2026-11-03, Option A):** HR enters only the days with undertime as **minor attendance corrections** (HR Processor encodes, HR Administrator approves); approval deducts from leave credits automatically and is logged.
- **PALMIRA can print the biometric ID** on its report (confirmed 2026-11-03). **Biometric ID field (owner request 2026-11-03, built):** `Employee.biometric_id` (text, so leading zeros stay; optional; unique when filled), on the employee page and edit form, searchable in System Administration, and an optional last column "Biometric ID" in the Data Import Employees sheet (older templates without it still upload). The PALMIRA PDF reader that will match on it is **not in scope until after the pilot**.

---

## 10. Supporting Document Uploads

- **Allowed file types:** PDF, JPG/JPEG, PNG only
- **Size limit:** max 10 MB per file
- **Quantity limit:** up to 5 files per transaction
- **Validation:** system must validate file type and size, and reject unsafe/disallowed formats
- **File handling:** uploaded files must be automatically renamed/secured on storage (do not preserve original filenames as stored paths); maintain an **audit trail** of uploads and replacements
- **Configurable requirements:** document requirements should be configurable per transaction type (e.g., medical certificate required for Sick Leave)
- **Access control:** document access restricted by role and transaction authority. Medical certificates, government IDs, and similar documents are **confidential HR records** — restrict accordingly, not just behind general login

---

## 11. Notifications

- **In-app:** required
- **Email:** required — on hold pending GovMail SMTP confirmation (see Section 2)
- **SMS:** not built — out of scope (owner decision 2026-11-03)

---

## 12. Dashboard & Reporting

Must include:
- Headcount
- On leave today
- On CTO
- Pending / approved / rejected applications
- Leave balance
- Exchange requests
- Monthly statistics
- Department statistics
- Pie charts and trend graphs
- Filterable by month/quarter/year and by department
- Reports exportable to both **PDF and Excel**

---

## 13. Explicitly Deferred / Not Yet In Scope

- **RA 10173 (Data Privacy Act) audit-log/data-retention features** — include only after explicit go-ahead from the project owner. Do not add "for best practice" without that confirmation.
- **2FA** — simple username/password login is sufficient for now
- **SMS notifications** — out of scope (owner decision 2026-11-03)
- **Live/API biometric integration** — batch import only for now

---

## 13B. Out-on-Pass module (owner decisions 2026-11-14; BRANCH `feature/out-on-pass`, merge only after the pilot Go/No-Go)

Rule set: BDH-ADM-AO-01 Rev 0 (effective 2027-01-01) **as revised by the pending DICR (Rev 1)**, slip form BDH-ADM-AO-01F16. The COH must sign Rev 1 before the Dec 18 test. Do not merge to main, and do not change leave, CTO or approval logic, without the owner's say-so.

**Start date (owner decision 2026-11-24, Option B; merged to main the same day):** the module is on main ahead of the policy, so until `OUT_ON_PASS_START_DATE` (env `BDH_HRIS_OUT_ON_PASS_START`, default **2027-01-11**, the day the HRIS takes over from the Google Sheets tracker; blank = in use) its menu items and the Out-on-Pass waiting count are hidden and every page shows "not yet in use" (`outonpass/availability.py`, `in_use_required` in `urls.py`). Only HR Administration → Out-on-Pass Setup stays open, so HR can enter COSP/PGSS contract periods ahead of time. No data is changed. The module's own tests switch it on (`setUpModule`).

**Scope**
- Personal pass only. "Local Official Errand" and F59 stay as they are.
- 2026 passes are not charged. The Google Sheets tracker covers Jan 1-10, 2027; the HRIS takes over Jan 11, 2027, with a one-off "hours already used Jan 1-10" line per employee.
- PGSS security personnel get HRIS accounts. Excludes official business and approved leave.

**Gate time and hours**
- The employee taps **Out** and **In** on a hospital computer; the HRIS time is the official gate time. The guard keeps a name / pass number cross-check log.
- Hours charged = actual minutes, whole minutes, seconds ignored. **No rounding.**
- Over 2 hours by not more than 5 minutes: not counted as excess, but the minutes are still charged to the balance.
- A missed Tap Out/In: confirmed by the supervisor, time verified by the guard on duty, reviewed by HRMO before hours are charged.

**Allowance and limits (the system checks all of them at filing)**
- Per policy year (Jan 1-Dec 31): permanent 12 h; COSP 1 h per full month of contract, up to 12 h (contracts started before 2027 count from Jan 1, 2027). Non-cumulative, forfeited at year/contract end.
- Max 2 h per pass; 2 passes a month; never more than 3 a month; 1 pass a day; none in the first or last hour of the shift; max 2 employees of a unit out at once; none during a surge or Code activation.
- Violation: the system **flags it, does not process the pass and tells the user why**. No filing after the fact.
- Shift-hour rule: refuse when the duty schedule shows the shift; with no schedule, the supervisor confirms. Surge/Code and coverage: the supervisor confirms.

**Routing**
- Ordinary pass: Employee -> Supervisor (unit head, else section head; single level) -> AO or COH approves. **No HRMO step.** The AO's own pass goes to the COH. Nobody acts on their own pass. A return needs a remark.
- Third pass, exception (beyond balance or 2 h) and documented emergency pass: separate request types with a reason; Supervisor endorses, **COH alone decides**; the system marks the type. A third pass needs its proof **attached at filing**; no proof, not processed. HRMO sees these only in the monthly list. No COH, no approval.
- Time not covered (over 2 h, over balance, unapproved): permanent staff = undertime; COSP and PGSS = hours not rendered, unpaid.
- Proof documents are confidential: visible only to the filer, supervisor and approver (see section 10).
- Monthly report to the COH from the HRIS (hours per unit, third passes, exceptions, unapproved absences, low balances).

**Owner decisions 2026-11-14 (after stage 1)**
- COSP allowance: HR enters each contract period (`CospContractPeriod`); the contract's hours are usable from its first day (Option A).
- PGSS security personnel are recorded as COSP with "COSP under" = **PGSS (Security)** (Option A); they follow the COSP allowance.
- Each special type lifts only its own rule (Option A): third pass = the 2-a-month limit; exception = balance and 2-hour limits; emergency = may be filed after the fact. Every other rule still applies.
- The COH's own pass is **recorded by HR** (`recorded_only`, SUBMITTED -> RECORDED), with Tap Out / Tap In (Option B).
- Slip Part I reasons: Medical appointment, Legal matter, Government or bank transaction, Family emergency, Personal errand, Other (+ details).

**How it is built (`outonpass/`)**
- `rules.py`: allowance, balance (opening line + charged minutes + minutes held by live passes) and `check_pass`; numbers in `settings.OUT_ON_PASS_RULES`. `routing.py`: who acts. When the person at the Supervisor step may also approve the pass (AO for a section head's ordinary pass; COH for the AO's own), their action approves it in one step (confirmed by the owner 2026-11-14, Option A). An approved pass has a "Show to guard" card (`/out-on-pass/<id>/guard/`). Rules are re-checked at every step. Proof uses the documents app but its own access rule (`OutOnPass.may_view_document`).
- Stage 3 (`gate.py`): Tap Out opens `TAP_OUT_EARLY_MINUTES` (15) before the planned time out and closes at the expected time in; an emergency pass records its time out as a missed tap. Tap In charges the pass (`minutes_charged`, `excess_minutes`, `over_balance_minutes`) and sets COMPLETED. Excess / over-balance minutes are recorded and listed, **not** deducted from leave credits (no leave logic changed). Missed tap (`MissedTap`): employee -> Supervisor step (`endorsers_for`; the AO for the COH) confirms -> a guard (PGSS employee) verifies, may correct the time with a remark -> HR reviews; only then is the time recorded and the pass charged. Gate page `/out-on-pass/gate/` (guards, HR, AO, COH): today's passes, missed taps to verify, passes out but not back.

**Owner decisions 2026-11-14 (after stage 3)**
- **Time not covered** (excess over 2 h beyond the grace, minutes over the balance): recorded on the pass and listed for HR; HR enters it as a **minor attendance correction** (existing path, deducts on HR Administrator approval). No automatic leave deduction (Option A).
- **No Tap In and no accepted missed Tap In** (Option B): the pass stays open on HR's "out, not back" list; another HR Processor/Administrator closes it by entering the time in with a required remark (`gate.hr_close`, logged as `hr_close`), and it is charged then. Nothing is assumed. An approved pass never tapped out after its expected time in can be marked **not used** by HR with a remark (`hr_not_used`, CANCELLED) so it stops holding the balance.
- **Guards = PGSS security personnel** (confirmed): any active employee recorded as COSP under PGSS may verify a missed tap.

**Stage 4 (built 2026-11-14)**
- **Slip printout** (`outonpass/slip.py`, `printouts/assets/f16_base.docx` = the Rev 1 draft layout): BDH-ADM-AO-01F16 filled from the pass, boxes ticked, digital stamps for Filed / Endorsed / Approved (or Recorded by HR), gate times, missed-tap review, minutes charged and excess. "Print slip (01F16)" on every pass page. Swap the base file when the COH signs Rev 1 if the layout changes.
- **Monthly report** (`outonpass/report.py`, Reports menu; HR, AO, COH; Excel and PDF): hours per unit, third passes / exceptions / emergency passes, time not covered (HR's list for minor attendance corrections), open passes (out not back, never tapped out), low balances (`LOW_BALANCE_MINUTES`, 60), staff with third passes in 3+ months.
- **Setup** (HR Administration -> Out-on-Pass Setup; HR Administrator only, never their own records): COSP/PGSS contract periods (no overlaps) and the one-off Jan 1-10, 2027 hours (one line per employee per year). Wrong lines are voided with a reason, never edited or deleted. All of it, and every pass and missed-tap action, appears in the Audit Log (module "Out-on-Pass").

---

## 13A. Future Expansion — Integrated HR and Administrative System (owner decisions 2026-10-17)

Planned after HRIS go-live and a stable period; not in scope yet. One system, one database, one employee list; each module its own Django app reusing roles/OIC, routing, digital stamps, documents, notifications, Audit Log and the menu.
- **HR:** Recruitment and Selection, Performance Management (CSC SPMS), Learning and Development.
- **Administrative:** Job Order, Trip Request and Vehicle Dispatching, Out-on-Pass (build only after the revised policy is approved), Activity Scheduler.
- **Changed by the owner 2026-11-03:** local official errands and travel authorization (F59) move to the planned **Out-on-Pass / travel authorization** module; until it exists, the HRIS keeps "Local Official Errand". Governor-approved trips stay in the HRIS as "Official Business (Travel Order)". Vehicle dispatching links to these.
- **Applications are encoded by HR on the hospital network.** No online/public applicant portal (it would need internet exposure and Data Privacy Act work, §13).
- Suggested order (owner decision 2026-11-22, Option A): Activity Scheduler + Out-on-Pass → **Performance Management** → Job Order + Dispatching → L&D + Recruitment. Existing Google Sheets tools are migrated once, then retired.

---

## 13C. Performance Management module (TPE + 360) (owner decisions 2026-11-14 to 2026-11-26; BRANCH `feature/performance`)

**Rebuild (2026-11-26).** Steps 1-7 were first built 2026-11-24/25, but the branch was never pushed and was lost when the cloud workspace was replaced. The owner chose to rebuild them from Design Package v1.1 and these notes (D10, Option A), **pushing each step to `feature/performance` on GitHub as a backup only** (never to main). The branch starts from `exchange-section-groups`. The rules below are the approved design; where the rebuild differs it says so.

**Status.** The full design is **Design Package v1.1** (project doc `claude/BDH_PM_Module_Design_Package_v1.md`, 22 Nov 2026). Where this section and v1.1 differ, this section wins (later owner decisions). All weights, points, caps and periods are **DRAFT** settings, never code constants. Do not change leave, CTO or approval logic. Reaches main only after the pilot (after Dec 18).

**PMT/COH decisions — all eight APPROVED as written (owner, relaying the COH, 2026-11-24):**
1. TPE weights: rank-and-file 50/30/10/10; supervisors 40/20/30/10.
2. Permitted uses: development, R&R, **supplementary** input to COSP renewal, promotion and succession; never the sole basis for adverse action.
3. Fairness rules: minimum 3 raters for confidential groups; working contact = at least half the period, at least weekly; at least one non-nominated peer; peer cap 4; 5-working-day dispute window.
4. Deduction matrices (below), final decisions only, 15-point cap.
5. Periods: semestral for Regular and supervisors; COSP month one, quarterly, 60 days before expiry; 10-minute check-ins in COSP month-one and quarterly periods.
6. R&R "Highest Attendance of the Quarter": B, a separate quarterly DTR count (kept outside the module).
7. Undertime does not lower the attendance sub-score.
8. Whole module in the first cycle; regular track waits for CSC Field Office and PHRMO confirmation; protected supervisor time.

**Owner decisions on v1.1 (22 Nov 2026):** N1 = B (subordinates use all 15 Form B items); N2 = B (full 20-min conference in semestral and pre-expiry periods, 10-min check-in in COSP month-one and quarterly); N3 = B (`holiday` table kept by the HR Administrator; working days = Mon-Fri minus holidays; leave and CTO screens NOT changed); N4 = A and B (subordinate contact question **and** a dated assignment history that also serves as the employee's record of personnel actions); N5 = B (for subordinates only, the "more than half N/O" test counts items B4-B12); N6 = A (whole module, both tracks, first cycle; regular track only after CSC FO/PHRMO confirmation).

**Principles**
- The PHRMO-certified rating is the official rating for regular employees: HR Processor encodes, HR Administrator verifies, certificate reference kept; the HRIS never computes or changes it. COSP/PGSS use the COSP Performance Form (3-5 key outputs, quality/efficiency/timeliness, next-level review); the result is "BDH Performance Evaluation", never an SPMS rating.
- Two outputs, never blended: the TPE score sheet and the developmental feedback report (leads to a 2-commitment development plan; training via F54).
- Ratings are **confidential, not anonymous**: identity kept for audit; comments screened by HR; confidential groups shown only with n >= 3. No self-view, HR included.
- Form A (12 items, clinical / non-clinical wording), Form B (15 items, CSC MC No. 12, s. 2016, first-line / higher-leader wording; plus Q1-Q3 unscored), Form C (self, never scored). **Rating forms approved by the Management Committee and the COH on 2026-11-24 without comment; no wording change** (wording in v1.1 §3).

**Scoring (v1.1 §5).** Rater average excluding N/O; confidential groups need n >= 3 (peers pooled, subordinates never); **no trimming** (owner decision 2026-11-25: `trim_threshold` = 0; the code still trims if the PMT ever sets it above 0); points = mean / 5 x weight; a skipped component's weight is redistributed proportionally; attendance sub-score = 100 - 15 per unauthorized absence - 3 per tardy day, floor 0, from the DTR; deductions taken straight off the 100-point total, capped at 15, floor 0; full precision stored, one decimal shown (half up).

**Performance description** (owner decision 2026-11-24, Option A; band settings): **90-100 Exemplary**; **80-89.9 Strong**; **70-79.9 Solid**; **60-69.9 Developing**; **below 60 Needs close support**. Not SPMS adjectives.

**Deduction matrices (approved 2026-11-24; S points 2026-11-25)**
- **Rank-and-file R1-R7:** 1 completion of assigned duties (0.50, 1.00); 2 accuracy and completeness of records (0.25, 0.50, 1.00); 3 safety, SOPs and scope of duty (0.50, 1.00, 2.00); 4 handover and service continuity (0.50, 1.00); 5 timely reporting and escalation (0.50, 1.00, 2.00); 6 stewardship of property and information (0.50, 1.00, 2.00); 7 final and executory administrative decisions (0.50, 1.00, 2.00; needs the final decision's reference; never a pending case).
- **Supervisory S1-S6** (supervisor track; supervisors are also covered by R1-R7): S1 planning and staffing 1.00/2.00/3.00; S2 monitoring of section performance 0.50/1.00/2.00; S3 employee supervision 0.50/1.00/2.00; S4 compliance and corrective action 1.00/2.00/3.00; S5 risk, incident and continuity escalation 1.00/2.00/3.00; S6 directives and coordination 0.50/1.00/2.00. No per-criterion cap.
- **Additions:** X7 module tasks not done by window close (supervisor track, 1.00, cap 3); X8 request held beyond the ARTA/office-order time (supervisor track, 1.00, cap 3); X9 breach of confidentiality (everyone, 2.00/3.00, cap 6); X10 retaliation (everyone, final decision only, 2.00/3.00, cap 6).
- **Route:** anyone files with evidence (never about themselves) -> another HR Processor/Administrator validates (never the filer or the employee; may change points or dismiss, with a note) -> the employee explains within 5 working days -> the AO approves or dismisses (the COH when the AO is the employee or filer) -> review within 5 working days: HR review -> AO endorses (an HR Administrator when the AO approved it) -> COH decides -> otherwise Final. **The employee sees who filed** (due process, 2026-11-25). Only Final deductions dated inside a period count; per-criterion caps first, then 15; a deduction Final after release is a note only.

**Periods.** Newly hired COSP (first-ever contract): month one, quarterly, then 60 days before expiry; a period ending within 30 days of pre-expiry is replaced by it. Rehired COSP: semestral, then pre-expiry. Regular and supervisors: semestral, one hospital-wide window. Peers rate only in the semestral window. Renewal recommendation = simple average of the contract's period TPEs, labelled "Supplementary"; the AO and COH decide.

**Step 1 (holidays, assignment history).** `Holiday` (HR Administrator only; one active holiday per date; voided with a reason, never edited or deleted; `working_days`, `add_working_days`, `is_working_day`; not used by leave or CTO). `PersonnelActionType` (HR-kept list, switched off not deleted; 16 names loaded 2026-11-26, all confirmed by the owner: Original, Promotion, Transfer, Reemployment, Reappointment, Reinstatement (to comparable position), Demotion, Reclassification, Reassignment, Detail, Secondment, Designation, COSP engagement, COSP renewal, Separation, Opening line (go-live)). `AssignmentLine` + `AssignmentAction`: an HR Processor/Administrator encodes (never on their own record; no future dates); another HR Administrator verifies (never the encoder or the employee). Closing a line is also verified. **Current sections/units are set from the verified open lines at verification** (`sync_membership`). Opening lines: one verified line per current section and unit for every active employee without lines (HR Administrator, "as of" date, rerunnable, changes nobody's membership, no second verification). Once an employee has lines, the employee edit form and the Data Import cannot change their sections/units. Audit Log modules "Holidays" and "Assignment History".

**Step 2 (settings, instruments, periods).** `PMSetting` (append-only dated rows; a change needs an approval reference and takes effect today or later; HR Administrator only; `regular_track_enabled` = 0, `undertime_counts` = 0). `Competency` / `Indicator` (Form A 12, Form B 15 with B4-B12 `day_to_day`, Q1-Q3 not scored, Form C C2-C5); `ClinicalSection` (clinical wording default: Medical, Nursing, Laboratory, Radiology, Pharmacy) and `WordingOverride`. `RatingWindow` (draft -> open -> closed, one open at a time; opening creates a semestral period for each eligible employee; warns if the year has no holidays). `EvaluationPeriod`. Supervisor track = own Supervisor or AO line (OIC lines never count); the COH is not evaluated; regular employees only once the regular track is enabled; semestral eligibility = hired by the semester midpoint; COSP first-contract staff follow month one / quarterly. Contracts reuse `outonpass.CospContractPeriod`.

**Step 3 (raters, forms).** Self on Form C; rank-and-file immediate supervisor on Form A = `endorsers_for` (where it runs through an OIC, the absent approver who designated them rates); supervisor track: top management (AO and COH; the COH alone for the AO) and every direct report as subordinate, Form B. Peers (semestral only): employee proposes up to `peer_proposals`; supervisor or HR picks `peers_per_ratee`, at least one not proposed, nobody over `peer_cap`; a peer answering No/Applies sends the employee back to the supervisor's "Peers to finalize" list. Contact question **Yes / No**; conflict question **Applies / None apply**; either closes the form uncounted and alerts HR without naming the rater. Orientation once per window. Save and finish later; locked on submit. Rater names only to HR Administrators (never their own period).

**Step 4 (compute, review, release, contests).** Inputs (certified rating, DTR counts) encoded and verified by different HR people; COSP Performance Form by the immediate supervisor (form score < 3.0 = "PIP under BDH-HR-PM-01 applies", flag only). Compute per v1.1 §5.1. Flags (straight-line, fast < `fast_seconds_per_item`, reciprocal peers, outliers > 1.5) to the HR Administrator: keep or void with a reason. Comments released / edited / withheld with a reason. Next-level review mandatory for COSP. Release notifies employee and supervisor. My Performance Results: score sheet (weights, n, points, notes, formula, performance description) and developmental report (strengths and room to grow as wording + mean, **no item codes**; self vs others, gaps >= 1 highlighted; each group separately; released comments; Q1-Q3 for leaders); confidential groups under 3 hidden with their comments; C5 reflection; conference recorded by the supervisor; employee acknowledges receipt with an optional disagreement note. Contest within 5 working days: HR review -> AO endorses (COH when the AO is the employee) -> COH decides; upheld = Final; returned = Open again. Daily `pm_finalize`.

**Step 5 (deductions)** as above; criteria kept on HR Administration -> Deduction Criteria with a PMT/COH approval reference per change.

**Step 6 (development plans).** One plan per period, two SMART commitments (intervention Coaching / On-the-job / Training, F54 reference, responsible Employee / Supervisor / L&D, target date, evidence), 1-2 check-in dates. Supervisor drafts (prefilled from the report) -> sends after the conference is recorded -> employee agrees or asks for changes -> supervisor records each check-in date as held or missed (reason) -> closes. HR reads all plans (read-only; never their own); AO/COH no access. **Missed check-ins (owner decision 2026-11-26, D11 Option A):** a check-in still planned 5 working days after its date is marked "missed" automatically by the daily job; the supervisor can still record it afterwards, marked "late"; a missed or late check-in automatically files an X7 deduction incident against the supervisor, which then follows the normal validate -> explain -> approve route (never deducted automatically).

**Step 7 (monitoring, reminders, reports).** Performance Monitoring (HR): what waits, completion by unit, rating time (v1.1 §8.3), averages hidden under the minimum n, R&R feed (Final results only), deduction register. `pm_reminders` daily: raters at 50% / 80% of the rating time and 2 days before the due date; check-ins 2 days before. Performance Report (HR, AO, COH; Excel and PDF; no individual data). COSP renewal averages (Supplementary). Supervisor team average.

**Menu (owner approval 2026-11-25, Option B):** a **Performance** group (My Rating Tasks, My Performance Results, Development Plans, Performance Incidents; Performance Monitoring and Performance Setup for HR, Setup with tabs Settings and forms / Periods / Deduction Criteria). Performance Reviews and Contests under Approvals. HR Administration split into **HR Records** (Data Import, Assignment History, Import Biometric Attendance, Minor Attendance Correction, CTO Opening Balances, Exception CTO Claim, CTO Separation Status) and **HR Settings** (the rest). Performance Report under Reports. **Home card "Performance tasks"**: shown only while something waits for the person; **never for ICTU Staff or the System Administrator** (owner decision 2026-11-26).

**Rebuild notes (2026-11-26; how the rebuilt code reads the rules, for the owner to confirm at the pilot):**
- **D11 (confirmed by the owner 2026-11-26, Option A):** an X7 incident is filed only when the **system** marks a check-in missed (nothing recorded 5 working days after its date); a check-in the supervisor records as "missed" with a reason on time files nothing. A check-in recorded after the system marked it is kept as held, "late"; the incident already filed stays and follows the normal route (HR may dismiss it at validation). Code: `performance/devplans.py auto_miss`, run by `pm_finalize`.
- **COSP periods open by themselves (owner decision 2026-11-26, decision 1, Option B; replaces "HR plans and opens them"):** the daily `pm_finalize` job plans the periods of every live COSP contract (never back-filling a period that already ended) and opens each one `cosp_open_days_before_end` (5) days before it ends, assigning the raters then. HR may plan or open earlier by hand, and an HR Administrator may **cancel** a planned or open period with a reason (`rules.cancel_period`; live forms voided, nothing deleted).
- **Pooled peers (decision 2, Option A):** still added by an HR Administrator on the period page (group "Pooled peer", with a reason); no automatic pooling. Once peers were picked (or the window closed) and fewer than `peers_per_ratee` remain, the period page suggests names from the rest of the section and Performance Monitoring lists every period short of peers (`raters.needs_pooling`, `pool_candidates`).
- **A deduction counts in one period only (decision 3, Option B):** the earliest period covering the incident date that was not yet released; it is claimed when that period is computed (`Deduction.period`, `deductions.counts_in`, `claim`). Other periods covering the date show it as a note.
- **Peer picking stays one step (decision 4, Option A):** the immediate supervisor or HR picks; no separate HR confirmation (v1.1 §2's two steps are not used; the cap and the one non-proposed peer are enforced by the system).
- **Gaps against v1.1 fixed (2026-11-26):** Performance events reach the HRIS **Audit Log** (module "Performance": period steps, incidents, settings and criteria changes, and viewing raw comments or flagged forms, once a day per person); rows never show a score, rating, comment or which rater did what, and a closed form's actor shows as "Rater (confidential)". **Printable TPE score sheet** (`score_sheet.py`, same builder and letterhead as the 01F04 forms, `[form code to be assigned by HRMO]`, digital stamps Prepared / Reviewed / Released / Received). **Assignment history check (N4):** who rates still comes from the shared lookup; a subordinate whose verified history puts them under the supervisor for less than `contact_share` of the period is left off (logged, listed for HR), a supervisor below it is kept but flagged on the period page, former staff the history shows are suggested to HR, and an opening line counts from the period start (`raters.history_check`). **R7** resting on absences already counted under Attendance is marked by HR at validation and shown, not deducted (`Deduction.in_attendance`). **Leniency view** (ratings given by unit and per supervisor next to the peers' ratings of the same staff; peers and subordinates hidden under the minimum) on Performance Monitoring and the supervisor's My Performance Results. Rating forms **autosave** every answer (HTMX); My Rating Tasks shows **progress and minutes left**; the full orientation text and the subordinates' "If you haven't seen this, choose N/O."; the conflict answer is kept on the form. Form C item **C4** is copied into the development plan; the report has **Suggested priorities**; supervisors get the **conference guide** and a **team view** (form status, self-assessment, conference, plan per staff member). The **HR Processor** may set Form A wording. **All staff view the holiday list** (only the HR Administrator changes it); the employee, their supervisor, HR, the AO and the COH view the assignment history (links on My Profile). The contact question follows `contact_share`; the unused `undertime_counts` setting was removed (undertime never counts). A released period shows as released and can no longer be recomputed.
- Evidence for an incident is a reference at filing; files are attached through the usual Documents page (owner/HR rule, `documents/permissions.py`).
- Code map: `rules.py` (settings, tracks, windows, COSP plan), `raters.py` (who rates, peers, forms), `scoring.py` (v1.1 §5.1, flags), `results.py` (inputs, compute, review, release, contests, report), `deductions.py`, `devplans.py`, `monitoring.py`, `home.py`, `score_sheet.py`; jobs `pm_finalize` (01:00: COSP periods, finalizing, missed check-ins) and `pm_reminders` (07:15), DEPLOYMENT.md §8a. Tests: `performance/test_*.py` (v1.1 Examples 1-4 and 6 included).

**PGSS security personnel** are evaluated by the General Services Section (next-level-up lookup, no special code); their deductions follow the normal route.

**Still OPEN:** when a COSP administrative decision counts as final; privacy notice (draft for DPO review, Claude Doc), raw-data retention and identity-request rule (DPO; §13 needs the owner's go-ahead); F03 outside-section rater number (HRMO); R&R clause and workbook locations (HR).

---

## 14. Build Order (Recommended Phasing)

Do not attempt to build this in one pass. Suggested phase order:

1. Database schema + auth + custom Role model
2. Employee master data (PDS-aligned fields + Education History table) + org structure (Sections/Units, M2M)
3. Employee self-service profile edit (with Administrator approval queue)
4. Leave application flow — CSC Form 6 output, COSP custom form output, Regular Leave path (internal review, Capitol approval — changed 2026-11-03)
5. CTO engine — isolate and unit-test the multiplier/forfeiture/deadline math specifically; this is the highest compliance-risk module
6. Exchange of Duty flow
7. Attendance Correction (formal + minor-administrative paths) + biometric CSV/Excel import
8. Official Business / Official Time / Travel / Authorized OT-restday-holiday (workflow routing only, no special business rules yet)
9. Document upload system (validation, secure storage, audit trail, access control)
10. Notifications (in-app first, then email once GovMail SMTP is confirmed)
11. Dashboard + reports (PDF/Excel export) — last, once the underlying data model is stable

---

## 15. General Working Rules for Claude Code on This Project

- Never guess at business rules in Sections 6–10 — they reflect actual signed BDH policy. If a rule seems ambiguous or conflicting, stop and ask rather than assuming.
- Regular leave is reviewed inside BDH but **approved only at the Capitol** (§6.2 "Leave routes"): never add an internal "approved" status or digital stamps to it.
- Keep CTO multipliers and other policy-derived constants in configuration (settings/DB), not hardcoded in business logic.
- Prefer Django's standard idioms (class-based views, forms, migrations) over custom architecture, since this needs to be maintainable by a small, non-specialist IT team long-term.
- Write unit tests for CTO and leave-accrual math specifically — this is where compliance bugs are costly.

### Running the tests
- Command: `BDH_HRIS_DEBUG=1 venv/bin/python manage.py test`
- `BDH_HRIS_DEBUG=1` is needed locally: with DEBUG off, settings switch to WhiteNoise's manifest storage, and without a `collectstatic` run about 20 page tests fail with "Missing staticfiles manifest entry".
- **Known failures (4):** the CSC Form 6 and COSP leave-form print tests in `leave.tests` (`PrintCscForm6Tests` x2, `PrintCospLeaveFormTests` x2) need LibreOffice. Without it they fail as `302 != 200` (on current main; older code showed `FileNotFoundError: 'libreoffice'`). They pass only on a machine with LibreOffice installed. Any other failure is a real one.

---

## 16. Pilot improvements (owner checklist 2026-11-24; rebuilt 2026-11-26, one branch per item, not merged)

All six are combined on branch **`test-six-items`** for the test PC (backup on GitHub; never merged into main without the owner's say-so). Owner decisions 2026-11-26: Decision 1 = Option A (Item 5, the OIC's own request, below); Decision 2 = the 2026-11-25 screen rules for Item 6 (below). Kept as built: HR cannot correct their own duty on an approved schedule (Item 1, same as the no-self-approval rule). A recalled schedule shows as **Returned** (editable by its preparer exactly like a draft; HR keeps seeing it, with the reason).

### Item 1 – Recall a schedule; correct several days (branch `item1-schedule-recall-correct`)
- **Recall and edit:** the preparer of a submitted schedule (Submitted, Reviewed or Recommended) may take it back with a **required reason** (`rules.can_recall`, `services.recall`, `/schedules/<id>/recall/`). It becomes **Returned** (editable), is logged as "recall" (Audit Log), and HR plus whoever was next to act are notified with the reason. Resubmitting uses the normal path: logged "resubmit", back at HR review, stamps restart. HR, the AO and the COH never see the button unless they prepared the schedule; it is gone once the COH approves. A recall after the cut-off is allowed; **a resubmission after the cut-off is logged and notified to HR as "Resubmitted after cut-off (date)."** (audit fix F4, 2026-11-26). Days locked by an approved regular Exchange of Duty stay locked through a recall (tested). Source: owner checklist B1.
- **HR correction for a range:** the correction form takes From and an optional To date. Each changed day is its own `ScheduleChange` ("Changes after approval"); days already on that shift are skipped; the employee gets **one** notice listing the days; a summary "correct" line is logged. Both dates must be in the schedule's month. HR cannot correct their own duty (another HR person does it).

### Item 2 — Filing date and time (branch `item2-filing-time`)
- Every request shows the **date and time it was first filed** (Philippine time) and, after a return, **"Resubmitted"** with that time beside it: My Applications, the details page and the approver's queue for leave, CTO applications and claims, Exchange of Duty, attendance corrections, Official Business / group requests, date changes, Out-on-Pass, and duty schedules ("Submitted"). One template tag does it (`{% filed obj %}` / `{% filed obj short=1 %}`, `accounts/templatetags/ui.py filing_times`): the first filing is the record's own filing time (or, for schedules and CTO claims, the first "submit" in its log); the resubmission is the latest "resubmit" in the log. Records filed before the change show the same, since both times were always kept. Duty schedules use `{% schedule_submitted s %}`: "Submitted: <date, time>" or **"Not yet submitted"** for a draft (audit fix F5, 2026-11-26; owner checklist B2). Display only. Tests: `accounts/test_filing.py` (every list, detail and queue template carries the tag).

### Item 3 — Actual salary on CSC Form No. 6 (branch `item3-salary-csc-form6`)
- `Employee.monthly_salary` (optional): **entered by the HR Administrator only** (the field is not on the form for anyone else, HR Processors included). On screen it is seen only by the HR Administrator and by **the employee, read-only, on My Profile** (audit fix F1, 2026-11-26). It is **hidden from every System Administration page** (`EmployeeAdmin.exclude`; `LeaveApplicationAdmin` hides `salary_at_recording`). Source: owner's Nov 25 plan, checklist B3. A change is logged in the employee's history and the Audit Log as "(amount not shown)": amounts are never kept in the log.
- **Item 7 (Salary) of CSC Form No. 6** prints the monthly salary instead of the Salary Grade (layout unchanged). The amount is the one **saved when HR recorded the leave** (`LeaveApplication.salary_at_recording`), so a later salary change never alters a processed form; before recording, or for leave recorded before this change, the current salary. Blank when none is on record.
- Who sees it on the printout: **since Part B (2026-11-27) only the people who may print the form: the employee (their own) and HR Processors / HR Administrators** (`csc_form6.may_see_salary`); this replaces the earlier "filer, HR, AO and COH" rule. The COSP form (01F04-A) is unchanged.
- Existing employees: the HR Administrator enters each salary on the employee record, or loads it with the **optional last column "Monthly Salary"** of the Data Import Employees sheet (owner decision 9, 2026-11-26; audit fix F8). The column is applied **only when an HR Administrator uploads**: an HR Processor's file with a salary is refused ("Only an HR Administrator may load salaries. Leave Monthly Salary blank."). Blank keeps the salary on record; older templates without the column still upload; history keeps "(amount not shown)".
- Tests use made-up figures only.

### Item 4 — CTO credit usable at approval (branch `item4-cto-usable-from`; revised by the owner 2026-11-25 and **2026-11-26**)
- **Owner decision 2026-11-26 (CTO balance, Option B; replaces the effective-date line of §7.1 for claims approved from now on):** approved CTO credit is **usable from the day the COH approves the claim** (`cto.balances.effective_date_for`, switch `settings.CTO_CREDIT_USABLE_AT_APPROVAL = True`; False restores the earlier rule, the later of the 1st of the month after the workday and the approval date). **Unchanged on purpose:** the **year earned** still follows the month after the workday (`year_earned_for`: December work counts as next year's CTO) and so does the **use deadline**, so the year-end rules hold: December work carries over without a reason; work up to Nov 30 still needs one if unused after Dec 15. **Confirmed by the owner 2026-11-27 (Option A):** December credit approved in December is usable at once and counts as next year's CTO. Credit already approved keeps its stored dates (nothing is recomputed). The approval notice says "usable now". Filing deadline, multipliers, carry-over, separation and opening balances are unchanged. Tests: `cto/test_usable_at_approval.py`.
- **Earned vs available display (owner-confirmed 2026-11-25), kept:** "Total earned credits" counts credit at once; credit whose effective date is still ahead is noted "X days usable from [date]" (`upcoming_credit`, `usable_from_text`, `upcoming_text`) and the "Available CTO balance" and the CTO application page count only credit usable today. With Option B a claim's credit is usable on approval, so this note now shows only if the switch is turned off. Tests: `cto/test_upcoming.py` (run with the switch off).

### Item 5 – OIC for the COH and the AO (branch `item5-oic-coh-ao`; owner decisions 2026-11-24)
- **Who may be OIC** (`accounts/oic.py may_be_oic`, used by both OIC screens): any Supervisor (unit heads, section heads incl. the Chief Nurse) for any approver area; **the AO also for the COH** (the COH's OIC list shows the AO; a Supervisor's list does not). COSP staff keep their limits (`cosp_may_cover`): never for the Chief Nurse, the AO or the COH, even if they hold a Supervisor line.
- **HR Administrator recording a signed designation:** a **reference number** is now required (`RoleAssignment.designation_reference`, migration `accounts 0005`), with the dates, reason and instruction as before. The Audit Log says which way it was made ("Designated by the approver in the system" or "Entered by HR Administrator ... from a signed written designation ref. no. ...").
- **An OIC acting again on a request** (`leave/permissions.py oic_repeat_guard`; **owner Decision 1, 2026-11-26, replaces the Nov 25 automatic remark**): at the AO or COH step, when the person holds that role only as OIC and already acted on the same request in this cycle (since it was last submitted), **a typed remark is required** ("You acted on this request earlier. To act on it again as OIC for the COH, type a remark first ... Nothing was changed."). The request keeps "[OIC - acted earlier on this request] Approved as OIC for the COH, after recommending it earlier: <remark>"; the Audit Log action reads "... (OIC, acted earlier)"; the filer's notice quotes only the typed remark (`strip_oic_tag`). Applied to leave (COSP and regular), CTO applications, CTO claims and exception claims, **CTO carry-over decisions** (`cto/carryover.decide_reason`; an OIC never decides their own reason, and their own reason is left out of their list), Exchange of Duty, attendance corrections, Official Business/Errand/OT (single and group), date changes and **duty schedules** (`schedules/services.act`). Carry-over reasons now appear in the Audit Log (module "CTO Carry-over"). Out-on-Pass keeps its own rule (the endorser never approves). Source: owner checklist B5 and Decision 1 (audit fixes F2/F3).
- **Approval lists** (leave, CTO, Exchange of Duty, attendance, Official Business/OT, CTO carry-over, duty schedules) show "This list includes requests waiting for you as OIC for the AO" while an OIC is in force (`templates/components/oic_queue_label.html`, from the same context processor as the banner).
- Tests: `accounts/test_oic_coh_ao.py`, `accounts/test_oic_routes.py` (each route refuses without a remark; own request never at the AO/COH step for the AO, Chief Nurse, a section Supervisor and a Unit Head as OIC).
- **While an OIC is in force:** a banner on every page ("You are acting as Officer-in-Charge: OIC for the AO (name) until ..."; `accounts/context_processors.py`), and notices for the AO / COH step sent to the OIC start "As OIC for the AO: " / "As OIC for the COH: " (`leave/permissions.py _with_role_except` marks them, `notifications.services.notify` adds the prefix).
- **The OIC's own request (owner decision 2026-11-26, Option A — keep the existing rule):** never acted on by them and never in their queue. When they are OIC for the AO only, the COH takes the AO-level step ("the COH when the AO is the filer"); only when one person is OIC for both the AO and the COH does it wait until the OIC ends.

### Item 6 – Leave and CTO on the schedule (branch `item6-leave-cto-on-schedule`; revised by the owner 2026-11-25)
- `schedules/leave_marks.py` reads the requests each time (nothing stored, no leave/CTO rule changed). **Shown** on the grid, the edit screen and the F50 printout (owner-confirmed rule, 2026-11-25): **LV** = approved leave only (`counted_q`); **CTO** = approved **or pending** CTO. Pending leave shows nothing; there is **no "LV?" or "CTO?"** anywhere. CTO days covering part of a COSP leave show as that leave. Returned, rejected and cancelled requests give no mark. An approved date change moves the mark (it reads the record's dates). Internally the kinds stay apart (`LV`, `LV_PENDING`, `CTO`, `CTO_PENDING`; `shown()` maps them for display).
- **LV and CTO codes** (seeded by migration `schedules 0004` if HR has none; `settings.SCHEDULE_LEAVE_CODES`): accepted only on a day with a **matching** request, **pending or approved**: **LV only on a leave day, CTO only on a CTO day** (owner Decision 10, 2026-11-26; audit fix F6; `leave_marks.code_error`; `SCHEDULE_LEAVE_CODES` = (leave code, CTO code); migration `schedules 0005` updates the seeded descriptions). Elsewhere the cell keeps its old value and the reason is listed (page message; the Excel upload lists the refused cells).
- A duty shift on a day of **approved** leave or CTO is a **warning only** (edit and detail pages); saving and submitting still work. Pending requests never warn.
- F50 printout: LV / CTO printed on days with no duty shift (blank or OFF), plus a legend line. Totals still come from the shift codes.

### Part B – Who prints CSC Form No. 6 (branch `partB-csc6-print`; owner decisions 2026-11-27)
- Regular employees' leave only (`leave/permissions.py may_print_csc6`, checked in `print_csc_form6`): **HR (HR Processor or HR Administrator) at any stage**; **the filer only their own, once it is For Capitol approval** (or the older Recorded) — returned, rejected and cancelled never; **nobody else**: not the Chief Nurse, a Unit Head or Supervisor, the AO, the COH, any OIC, an absent approver with view-only access, or the System Administrator, at any stage. Rights follow the person's relationship to the form: an approver's own leave prints after approval like any filer; **an HR person's own form follows the filer rule** (Decision 3, Option A). A person with several roles prints another's form only through an HR role.
- Server-side: a pasted link from anyone else gets a plain page (403) "Only HR can print another employee's CSC Form No. 6. You can print your own once it is For Capitol approval." The button is left out of the detail page and the approval queue for them (`{% csc6_hidden a as no_print %}`).
- Unchanged: layout, salary item 7 (now printed only for the filer and HR, since only they print), viewing on screen, the COSP form 01F04-A and every other form. No approval step depends on printing. Tests: `leave/test_csc6_print.py`.

