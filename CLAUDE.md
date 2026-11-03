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

**OIC coverage:** Ad-hoc delegation triggered by the Supervisor themself (not pre-scheduled by HR Admin). One Supervisor can be designated OIC across multiple sections.

**OIC rules (settled 2026-10-13, Batch 7):** the approver (Supervisor, AO or COH) designates the OIC on the **Officer-in-Charge (OIC)** screen (`/oic/`, `accounts/oic.py`), for one of their own role areas. Only an employee who already holds an active Supervisor role can be OIC. The OIC acts only between the start and end dates (every role assignment's dates are honoured, `RoleAssignment.in_force_q`). While the OIC is in force, the delegator's own assignment for that role and area is set aside (`Employee.active_role_assignments`), so the absent approver cannot act there and is not notified for it. The absent approver keeps **view-only** access to that area (records, printouts, documents, schedules, homepage figures, Dashboard, Audit Log) — view checks pass `viewing=True` (`has_role`, `is_hr`/`is_administrative_officer`/`is_chief_of_hospital`, `is_supervisor_of`); action checks and queues never do (owner, 2026-10-13). "End now" cancels an upcoming OIC or ends a running one yesterday; nothing is deleted. Designations and early endings appear in the Audit Log (module "OIC Designation").

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
- Wellness Leave and Emergency Leave also available to COSP, **subject to their leave allowance/credit** (not automatically granted — check balance before allowing)

### 6.2 Approval Routing by Request Type

| Request Type | Routing |
|---|---|
| **COSP Leave** | Employee → Supervisor → HR (Processor/Admin) → AO → COH |
| **Regular Leave** (VL/SL/etc.) | **Data entry and monitoring only** — approval authority is external, at the Provincial Capitol/PHRMO. Do NOT route through internal approval chain. |
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
- F59 (Travel Authorization and Transportation Request) printout for the errand: not built yet.

**Attendance Correction, multiple dates (settled 2026-10-07):** one request holds 1–10 dates, each with its own times and reason. All dates in one formal request must belong to the same validator (ICTU-type and HR-type reasons are filed separately). Approvers act on the whole request only (validate/approve, return, reject); **a return needs a remark**. The filer edits and resubmits the same request, which **starts again from the first step**; the return history is kept and printed on F10.

**Group (batch) filing — Travel, Official Business, OT / rest-day / holiday work (settled 2026-10-07):**
- Who files: **Supervisor** (own sections/units incl. OIC for Travel/OB; anyone for OT), **HR Processor** (any employee; all types — OT added 2026-11-02, D5), **HR Administrator** (any employee; all types), **AO** (any employee; OT only, D5). The filer cannot include themselves or the COH. OT details: §7.1.
- One batch; each employee-date is its own OfficialRequest line. Whole-batch approve/return (remark required); filer edits and resubmits, starting the route again.
- Routes (filer's step counts as done): OT/rest/holiday by Supervisor → HR → AO → COH; by HR Admin → AO → COH. **Local Official Errand by Supervisor or HR → AO approves. Official Business (Travel Order) by Supervisor → HR → AO → COH (recommending) → Capitol, HR records; by HR → Supervisor → AO → COH (recommending) → Capitol, HR records** (2026-11-03).
- The filer can never act on their own batch at a later step, even with other roles or OIC. Approved OT creates no CTO (§7.1 unchanged). A regular employee filing their own request keeps the normal route.

**No self-approval; next level up (owner decision 2026-10-26, pilot import):**
- Nobody acts on a request they filed — or, for an Exchange of Duty, one they are a party to — at **any** step (endorse, process/validate, recommend, approve, record). Applies to COSP leave, regular-leave recording, CTO, Exchange of Duty, attendance corrections (formal and minor), Official Business/Time, Travel, OT and group filings; HR never files or credits their own CTO claim. Queues and notifications leave the filer out.
- **Two-level endorsement (owner decision 2026-10-27, Option B; supersedes the single Supervisor step for these cases):** where an ordinary employee's unit has a unit head AND the section has a section-wide Supervisor (Nursing: unit head, then Chief Nurse), the Supervisor step is done twice — `SUBMITTED → ENDORSED_BY_UNIT_HEAD` (unit head, action `endorse_unit`) `→ ENDORSED_BY_SUPERVISOR` (section head) — for COSP leave, CTO, Exchange of Duty, Official Business, Travel and OT. A unit with no head, a section without units/unit heads, and a unit head's own request keep one step. Either level may return. Strict order (owner, 2026-10-27, Option A): the section head cannot act at the unit-head step; an absent unit head designates an OIC. Group (batch) requests keep ONE endorsement, by the unit head or the section head. Forms print a "Unit Head" stamp before "Section Head" when the unit step applies (`printouts/stamps.py with_unit_head_step`). Helpers: `endorsers_for` (first endorsement), `second_endorsers_for`, `needs_unit_step`, `supervisor_step_result`.
- **Supervisor step = one level above the filer** (`leave/permissions.py endorsers_for`): an ordinary employee → any Supervisor of their section/unit; a **unit head** → the section-wide Supervisor (e.g. the Chief Nurse), never a fellow head of the same unit; a **section head** (no one above at Supervisor level) → the **AO**; the **AO's own request → the COH** (at the Supervisor and AO steps). The person who acts is stamped with their own name and position.
- **Endorser is not the HR step (owner decision 2026-11-03, Option B):** whoever endorsed a request in its current cycle (unit head or Supervisor step) cannot act on it at the HR step, and is left out of that HR queue and notification; another HR Processor/Administrator must process it. A resubmission starts a new cycle. Helpers: `endorser_ids`, `exclude_own_endorsements`; `can_act_hr_step(..., obj)`, `hr_step_actors(..., obj)`.
- **Multi-role staff (2026-11-03):** attach a person's employee record to their home section only; a unit headship elsewhere comes from the Supervisor + Unit role line, not from unit membership (membership in another section would route their own requests to that section's head).
- **A return or a rejection always needs a remark (owner requests 2026-11-03):** leave (COSP), CTO, Exchange of Duty, Official Business/Time/Travel/OT, group requests and attendance corrections; Return and Reject stay disabled until a remark is typed and the server refuses either without one ("Nothing was changed"). The filer's notification quotes the remark (`remark_suffix`).
- **Group requests can be edited while pending (owner request 2026-11-03):** the filer may edit a group request while no approver has acted since it was filed/resubmitted (`batch.pending_untouched`, `filer_may_edit`); it stays at its first step and is logged as "edit". Once an approver acts, only a return reopens it.
- HR/ICTU steps: any other holder of the role. AO step: the AO, or the COH when the AO is the filer. COH step: never the COH's own request.
- **The COH's own Official Business / Official Time / Travel / OT (owner decision 2026-10-26, Option A):** recorded by HR only, like regular leave — `OfficialRequest.recorded_only`, SUBMITTED → RECORDED ("Recorded (approved outside BDH)"); the approval itself is at the PHO/Capitol. The COH cannot be included in a group (batch) filing.
- **Import scope (same date):** a Supervisor with a unit on their import row supervises that unit only; with no unit, the whole section. The COH and AO may be imported with no section (hospital-wide). A unit must belong to a section on the same row.

**⚠️ Critical:** Regular Leave must NOT be routed through the internal approval workflow engine — it's data-entry/monitoring only. Building this into the same generic workflow as other request types risks incorrectly implying internal approval authority that doesn't exist.

### 6.3 Output Forms
- COSP Leave: custom BDH-branded form, **Form No. BDH-ADM-HR-01F04-A**
- Other leave types: CSC Form No. 6 layout — official CSC form, left as is (no BDH code, no digital stamp, because regular leave is approved outside BDH)
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
- Effective date = later of the 1st of the month after the workday and the COH approval date. The balance counts only credits whose effective date has arrived.
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

**Configuration:** `CTO_FILING_DEADLINE_MONTH_DAY`, `CTO_USAGE_CUTOFF_MONTH_DAY`, `CTO_CARRYOVER_REASON_DEADLINE_MONTH_DAY`, `CTO_CARRYOVER_REMINDER_DATES`, `CTO_OPENING_BALANCE_DATE` and reminder dates in settings; multipliers in the `CTOMultiplierRate` table.

---

## 8. Exchange of Duty

- One-for-one exchange only
- Max 3 requests/month
- Requires mutual written consent between the two employees, plus Supervisor endorsement as part of the full Employee→Supervisor→HR→AO→COH chain (Supervisor endorsement is a step within that chain, not a substitute for it)
- Must never generate extra OT, extra CTO, or extra pay
- **Timing:**
  - Normal requests: filed at least 1 week before the next month's schedule is approved
  - Modifications to an already-approved schedule: 24–72 hours notice
  - Documented emergency: same-day filing allowed, with written follow-up within 24 hours

### 8.1 Settled with duty schedules (2026-10-09, Batch 5)
- Partners must hold the **same position**.
- A must be on duty on date A and B on date B; A must be **off** on date B and B off on date A.
- **Before the schedule is approved:** file at least **7 days before the schedule's cut-off date**, checked against the submitted schedule. The cut-off is set by HR per schedule (default: the 20th of the month before, `SCHEDULE_CUTOFF_DAY`). **After COH approval:** the 24-hour rule. Emergency: same day.
- A date with no submitted schedule on file is not checked against a schedule (allowed as before, with a note).
- At COH approval the swap is written into the schedule (giver → OFF, taker → the shift); every change is kept in the schedule's change history. If the schedule changed since filing, approval stops and the approver is told to return the request.

---

## 8A. Duty Schedules — Form BDH-ADM-AO-01F50, Revision 2 (settled 2026-10-09, Batch 5)

- One schedule per section **or** unit per month; rows = employees, one shift code per day.
- **Shift codes** are kept by the **HR Administrator** (seeded: 8-5 = 8 h, lunch 12–1; 7A-7P = 12 h; 7P-7A = 12 h night; OFF). Total Hours / Total Days come from the codes.
- Prepared by the section/unit **Supervisor** (incl. OIC) **or HR**. Entry: monthly grid with dropdowns + "repeat pattern" tool, or the Excel template (F50 layout).
- Routing (whole schedule): **Prepared → Reviewed (HR) → Recommending approval (AO) → Approved (COH) → Recorded (HR)**. A return needs a remark; the resubmitted schedule restarts at HR review. The preparer never acts on it later (so an HR-prepared schedule is reviewed and recorded by a different HR person).
- **Locks at COH approval** (the approval date is stored per schedule). Shown in "My Schedule" and staff notified only after HR records it.
- After approval, changes come only from an approved Exchange of Duty or an HR correction with a reason; both are logged (ScheduleChange, Audit Log).
- Double-booking (same person on duty in two schedules on one day) is **flagged, not blocked**.
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
- Announcements (Office Order, Policy, Activity): managed by the **HR Administrator only**; Draft → Published → Archived (auto-archive on expiry); editable only as a draft; optional signed PDF (up to `ANNOUNCEMENT_PDF_MAX_MB`, default 50 MB — higher than the 10 MB limit elsewhere); all staff or selected sections/units; staff notified on publish; every change in the Audit Log.
- Archived items stay viewable by the same audience on the Announcements page's **Archive** tab, marked "No longer in effect"; the homepage shows current items only (settled 2026-10-09).

---

## 8C. Look and Navigation (settled 2026-10-13, Batch 6)

- **Palette** (static/css/app.css `:root`): Royal Blue `#173D72` primary (top bar, headings, primary buttons); Gold `#E5B83F` accent (current-menu marker, highlights — never as text on white); Red `#C9363E` only for errors, returned/rejected status and urgent counts. Status colours: pending amber, approved green, returned/rejected red.
- **Icons:** Lucide line icons (ISC licence) kept locally in `templates/components/icon_sprite.html`; `{% load ui %}{% icon "name" %}`. Always beside a text label.
- **Menu additions (Batch 7, 2026-10-13):** My Profile (all), Profile Changes (HR Administrator) and Officer-in-Charge (approvers) under Approvals/My Requests; Import Biometric Attendance and Minor Attendance Correction (HR) under HR Administration.
- **Menu:** defined in ONE place, `employees/navigation.py` (groups: Home, My Requests, Approvals, Workforce, Reports, HR Administration). Each item names the permission flag that decides visibility; pages keep their own server-side checks. Approvals show waiting counts. Notifications only via the header bell. "File for a Group" stays under My Requests. System Administrator gets "User Accounts and Roles" (Django admin).
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
- **PALMIRA can print the biometric ID** on its report (confirmed 2026-11-03). The HRIS has **no biometric ID field yet**: add one to Employee (or confirm the biometric ID equals the HRIS Employee ID) before building the PDF reader after the pilot.

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

## 13A. Future Expansion — Integrated HR and Administrative System (owner decisions 2026-10-17)

Planned after HRIS go-live and a stable period; not in scope yet. One system, one database, one employee list; each module its own Django app reusing roles/OIC, routing, digital stamps, documents, notifications, Audit Log and the menu.
- **HR:** Recruitment and Selection, Performance Management (CSC SPMS), Learning and Development.
- **Administrative:** Job Order, Trip Request and Vehicle Dispatching, Out-on-Pass (build only after the revised policy is approved), Activity Scheduler.
- **Changed by the owner 2026-11-03:** local official errands and travel authorization (F59) move to the planned **Out-on-Pass / travel authorization** module; until it exists, the HRIS keeps "Local Official Errand". Governor-approved trips stay in the HRIS as "Official Business (Travel Order)". Vehicle dispatching links to these.
- **Applications are encoded by HR on the hospital network.** No online/public applicant portal (it would need internet exposure and Data Privacy Act work, §13).
- Suggested order: Activity Scheduler + Out-on-Pass → Job Order + Dispatching → L&D + Recruitment → Performance Management. Existing Google Sheets tools are migrated once, then retired.

---

## 14. Build Order (Recommended Phasing)

Do not attempt to build this in one pass. Suggested phase order:

1. Database schema + auth + custom Role model
2. Employee master data (PDS-aligned fields + Education History table) + org structure (Sections/Units, M2M)
3. Employee self-service profile edit (with Administrator approval queue)
4. Leave application flow — CSC Form 6 output, COSP custom form output, Regular Leave data-entry-only path
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
- Treat Regular Leave's "data-entry only" status as a hard constraint, not a simplification to "fix" later.
- Keep CTO multipliers and other policy-derived constants in configuration (settings/DB), not hardcoded in business logic.
- Prefer Django's standard idioms (class-based views, forms, migrations) over custom architecture, since this needs to be maintainable by a small, non-specialist IT team long-term.
- Write unit tests for CTO and leave-accrual math specifically — this is where compliance bugs are costly.

### Running the tests
- Command: `BDH_HRIS_DEBUG=1 venv/bin/python manage.py test`
- `BDH_HRIS_DEBUG=1` is needed locally: with DEBUG off, settings switch to WhiteNoise's manifest storage, and without a `collectstatic` run about 20 page tests fail with "Missing staticfiles manifest entry".
- **Known failures (4):** the CSC Form 6 and COSP leave-form print tests in `leave.tests` (`PrintCscForm6Tests` x2, `PrintCospLeaveFormTests` x2) need LibreOffice. Without it they fail as `302 != 200` (on current main; older code showed `FileNotFoundError: 'libreoffice'`). They pass only on a machine with LibreOffice installed. Any other failure is a real one.
