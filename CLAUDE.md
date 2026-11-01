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
- **In scope (pending approved HR memo, 2026-11-01).** No provider chosen yet. Build a swappable SMS backend (like `EMAIL_BACKEND`) with a logging stub until BDH picks a provider.
- Sent to the employee's mobile number on file, for the same events as email. Short text only — no remarks or reasons. No opt-out. Employees with no mobile number are skipped, and the skip is logged for HR.

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

**OIC coverage:** Ad-hoc delegation triggered by the Supervisor themself (not pre-scheduled by HR Admin). One Supervisor can be designated OIC across multiple sections.

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
| **Attendance Correction (formal)** | Employee → Supervisor → HR → AO |
| **Attendance Correction (minor administrative)** | HR Processor → HR Admin (no formal approval chain needed) |
| **Official Business** | Employee → Supervisor → HR → AO → COH |
| **Official Time** | Employee → HR → AO → COH (no Supervisor step) |
| **Travel** | Employee → Supervisor → AO → COH (no HR step) |
| **Authorized OT/restday/holiday work** (pending approved HR memo — see §7.1) | Filed by Supervisor/HR/AO, not the employee. Supervisor-filed: HR → AO → COH (Nursing: Unit Head → Chief Nurse → HR → AO → COH). HR-filed: AO → COH. AO-filed: COH |

**⚠️ Critical:** Regular Leave must NOT be routed through the internal approval workflow engine — it's data-entry/monitoring only. Building this into the same generic workflow as other request types risks incorrectly implying internal approval authority that doesn't exist.

### 6.3 Output Forms
- COSP Leave: custom BDH-branded form
- Other leave types: CSC Form No. 6 layout

### 6.4 Self-Service Restrictions
Employee profile edits are self-service only for: contact information, address, civil status — and all such edits require Administrator approval before taking effect.

---

## 7. CTO (Compensatory Time Off)

Applies to both regular and COSP employees. Based on BDH's approved (Dec 16, 2025) HR Policy Revision Proposal on CTO and Exchange of Duty.

- **Earned from:** authorized OT, rest-day work, holiday work, certified emergency duties. **Not applicable to the Chief of Hospital.**
- **Conversion multiplier:** OT hours × **1.0** (weekday) or × **1.5** (rest day/holiday), per CSC-DBM Joint Circular No. 2, s.2004. **Multipliers must be configurable, not hardcoded** — do not assume these values are permanently fixed in code.
- **Monthly cap:** max 5 CTO days/month, no 3+ consecutive days — except for separating employees (§7.1), who are exempt from both limits
- **Filing, forfeiture and deadlines:** see §7.1, which replaces the earlier rules here. CTO filing deadline is Nov 30; CTO usage dates cannot extend past Dec 15 (except via §7.1 carry-over and separation rules).

### 7.1 OT and CTO claim rules — PENDING APPROVED HR MEMO (decided 2026-11-01)

Decided by the project owner in a 2026-11-01 design session; these **replace** the 2026-09-28 decisions (HR-filed claims, 1st-of-next-month filing window, COH exception for Nov/Dec work). They are pending an approved HR memo. Do not re-ask or change them without explicit confirmation.

**General**
- OT never becomes pay. Approved OT does not create CTO credit by itself. Unused CTO lapses and is never paid.
- The COH can never be on an OT line (CTO not applicable).
- Biometric attendance can inform HR review but never blocks an approval.

**OT requests** (own model, not an `OfficialRequest` type)
- Filed only by a Supervisor (Unit Head in Nursing), HR Processor, HR Administrator or AO — never by the employee. Past dates need a typed reason.
- Batch: a header plus lines of one employee + one date each, with approved hours and day type (weekday / rest day / holiday) per line.
- A filer may add anyone except the COH and themselves. Lines dated after an employee's last day of service are blocked. Each Supervisor/Unit Head covering the employee gets an in-app notice when the employee is put on someone else's OT line (information only).
- If the filer adds someone who is on the batch's own approval route, warn and suggest a separate batch; do not block.
- Routing: see §6.2. A batch is approved or returned as a whole. A return needs a typed remark, goes back to the filer, and the chain restarts.
- Chief Nurse = the Supervisor whose role covers the whole Nursing Service Section; Unit Heads = Supervisors scoped to one Nursing unit. No new role.

**Approver is the subject** (OT requests and CTO claims)
- A person on the request/claim (even one line of a batch) cannot act on it. Another holder of the same role takes the step: Supervisor step → another Supervisor covering the employee's section/unit incl. OIC; Chief Nurse step → another Supervisor covering the whole Nursing Service Section; HR step → any HR Processor or HR Administrator; AO/COH → any other holder. If none exists, the step is skipped and the skip is audit-logged.

**CTO claims**
- Filed by the employee any time after the workday. One claim may cover several of that employee's lines; it is approved or returned as a whole, but credit, effective date, filing deadline and year earned are tracked per line.
- **OT claims:** lines come from any of the employee's approved OT lines; actual hours ≤ approved hours per line. Attachments: Allowed to Work form, DTR/logbook copy, OT Accomplishment Report.
- **Medical Transport claims:** no OT request; the employee enters date, hours and day type, and HR verifies. Attachments: Trip Ticket plus Certificate of Appearance or logbook copy. Always separate claims from OT claims; same routing as OT claims.
- **Exception claims:** HR Administrator only (never for themselves), required reason, no OT request, routed AO → COH. They **skip the filing-deadline check** — the typed reason is the justification (decided 2026-11-01). The resulting credit still follows the carry-over rules by its work date. This is also the only way the COH can reinstate credit after a rejected carry-over reason.
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
- Credit from **work done** on or before Nov 30 that is still unused after Dec 15 carries over only if a Supervisor/Unit Head covering the employee submits a written reason by end of Dec 16 and the COH approves. One reason per employee per year covers all their at-risk credits, goes straight to the COH, and may be submitted early. Pending credit cannot be used; COH rejection forfeits it at once.
- Credit from work done after Nov 30, and any credit whose effective date is after Dec 15, carries over with no reason.
- **Who submits the reason (decided 2026-11-01):** a Supervisor (Unit Head in Nursing) covering the employee. If none covers them: the Chief Nurse for Nursing staff; otherwise an HR Administrator. An HR Administrator's own credit is submitted by the AO; the AO's own credit by an HR Administrator. The submitter is never the employee, and the COH decides every reason.
- **Rejection:** final for that employee and year (no second reason). A rejection before Dec 15 leaves the credit usable until Dec 15; it is then forfeited on Dec 17. A rejection after Dec 15 forfeits the pending credit at once.
- On Dec 17, credits with no reason submitted are forfeited. Credits with a reason stay pending until the COH decides.
- A carried-over credit faces the next year's Dec 15 deadline and needs a new reason if still unused.
- Reminders to Supervisors and HR (at-risk list) and to the COH (undecided reasons); dates in settings (default Dec 1, Dec 10, morning of Dec 16). The employee is notified of forfeiture, of a reason submitted for them, and of the COH decision.

**Separating employees**
- HR (Processor or Administrator) sets a separation status (COSP: not renewing / resigned; Regular: resigned / retired) and last day of service; audit-logged. Never for themselves (added 2026-11-01 after a security review: the status waives their own CTO limits) — another HR Processor or Administrator does it.
- Waived: effective-date wait, Dec 15 use deadline, 5-day monthly limit, 3-consecutive-day limit. The Supervisor still approves CTO dates.
- Their pending claims are flagged to approvers and HR. After the last day of service, remaining credit lapses (nightly job) and claims still in the chain lapse with no credit.
- Clearing the status restores normal rules; CTO already used stays used.

**Opening balances (Option A, decided 2026-11-01)**
- Existing CTO credit is entered as **several opening lines per employee**, each with days, date earned and a required reason (may cite the paper record). Never collapsed into one balance at a cutoff.
- Each line follows the normal rules by its own date earned: year earned = year of the date earned; credit earned on or before Nov 30 needs a carry-over reason if unused after Dec 15; credit earned after Nov 30 carries over without one. A line counts in the balance from the day it is entered.
- Date earned must be in 2026 (the year of `CTO_OPENING_BALANCE_DATE`). Older credit is not entered here; it goes through an exception claim.
- HR Administrator only, never for themselves. If no other HR Administrator exists, the AO enters them. No approval chain; every add and void is audit-logged.
- `CTO_OPENING_BALANCE_DATE` (default Nov 30, 2026) only locks entry. Up to and including that date, a wrong line is voided with a typed reason and re-entered — nothing is edited in place. A line with CTO days already held or used against it can't be voided. After the lock, corrections go through an exception claim (AO, then COH).
- Implementation: lines are `CTOClaimLine`s on the employee's single opening record (`CTOClaim.is_opening`, one per employee, never routed). See `cto/opening.py`.

**How Stage 2 implements this (2026-11-01)**
- Every credited line has a `use_deadline`: Dec 15 of its year earned, or Dec 15 of the next year if it took effect after Dec 15. CTO can only be used on dates up to a line's deadline (this replaced the blanket "no use after Dec 15" check). An approved carry-over moves it forward one year.
- `cto_year_end` runs **daily** and does nothing new until Dec 17. It handles every credited line whose deadline and reason deadline (Dec 16) have passed: approved reason → carry over; pending → held, not usable; rejected or none → forfeited.
- `cto_separation_lapse` runs nightly. `cto_carryover_reminders` runs daily and acts only on `CTO_CARRYOVER_REMINDER_DATES`. Logic is in `cto/carryover.py` and `cto/separation.py`.
- Separation status and last day of service are `Employee.separation_status` / `last_day_of_service`. They are separate from `separation_date`, which is the System Administrator's record-retention date.

**Open questions (not built — ask the project owner)**
- **Reductions after the opening-balance lock:** an exception claim can only add credit. How BDH wants a wrong opening line reduced after `CTO_OPENING_BALANCE_DATE` is undecided (deferred 2026-11-01). Until then, only voiding before the lock reduces credit.

**Configuration:** `CTO_FILING_DEADLINE_MONTH_DAY`, `CTO_USAGE_CUTOFF_MONTH_DAY`, `CTO_CARRYOVER_REASON_DEADLINE_MONTH_DAY`, `CTO_CARRYOVER_REMINDER_DATES`, `CTO_OPENING_BALANCE_DATE`, `NURSING_SECTION_NAME` and reminder dates in settings; multipliers in the `CTOMultiplierRate` table. If `NURSING_SECTION_NAME` matches no section, OT filing by a Supervisor and claim submission stop with an error asking an HR Administrator to fix it — the Nursing steps are never silently skipped.

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

---

## 9. Attendance & Biometric Integration

- BDH has an existing biometric system; exports are **CSV and Excel**
- HRIS **imports these periodically** (batch upload by HR Processor) — no live/API integration required
- Biometric data is **advisory, not authoritative**: HR Processor can manually encode/adjust attendance regardless of what the biometric import shows
- Undertime auto-deducts from leave credits

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
- **Email:** required — pending GovMail SMTP confirmation (see Section 2)
- **SMS:** required (pending approved HR memo) — see Section 2; provider not yet chosen

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
- **Live/API biometric integration** — batch import only for now

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
- **Known failures (4):** the CSC Form 6 and COSP leave-form print tests in `leave.tests` (`PrintCscForm6Tests`, `PrintCospLeaveFormTests`) need LibreOffice (`FileNotFoundError: 'libreoffice'`). They pass only on a machine with LibreOffice installed. Any other failure is a real one.
