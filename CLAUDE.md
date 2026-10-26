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
- Not required. No SMS integration in scope.

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
| **Exchange of Duty** | Employee A/B → Supervisor → HR → AO → COH |
| **Attendance Correction (formal)** | Employee → **one validator by reason** → AO. ICTU Staff validate Offline / Failed Attempt / Wrong Button-Invalid Entry; HR validates Attended meeting-activity-training / Others. No Supervisor step. (Changed 2026-10-06 by the project owner to follow the Missed Log Justification Form, BDH-ADM-AO-01F10; previously Employee → Supervisor → HR → AO.) |
| **Attendance Correction (minor administrative)** | HR Processor → HR Admin (no formal approval chain needed) |
| **Official Business** | Employee → Supervisor → HR → AO → COH |
| **Official Time** | Employee → HR → AO → COH (no Supervisor step) |
| **Travel** | Employee → Supervisor → AO → COH (no HR step) |
| **Authorized OT/restday/holiday work** | Employee → Supervisor → HR → AO → COH |

**Attendance Correction, multiple dates (settled 2026-10-07):** one request holds 1–10 dates, each with its own times and reason. All dates in one formal request must belong to the same validator (ICTU-type and HR-type reasons are filed separately). Approvers act on the whole request only (validate/approve, return, reject); **a return needs a remark**. The filer edits and resubmits the same request, which **starts again from the first step**; the return history is kept and printed on F10.

**Group (batch) filing — Travel, Official Business, OT / rest-day / holiday work (settled 2026-10-07):**
- Who files: **Supervisor** (own sections/units incl. OIC; all types), **HR Processor** (any employee; **Travel and Official Business only**), **HR Administrator** (any employee; all types). The filer cannot include themselves.
- One batch; each employee-date is its own OfficialRequest line. Whole-batch approve/return (remark required); filer edits and resubmits, starting the route again.
- Routes (filer's step counts as done): OT/rest/holiday by Supervisor → HR → AO → COH; by HR Admin → AO → COH. Travel by Supervisor or HR → AO → COH. **Official Business by Supervisor → HR → AO → COH; by HR → Supervisor → AO → COH** (the Supervisor still endorses).
- The filer can never act on their own batch at a later step, even with other roles or OIC. Approved OT creates no CTO (§7.1 unchanged). A regular employee filing their own request keeps the normal route.

**No self-approval; next level up (owner decision 2026-10-26, pilot import):**
- Nobody acts on a request they filed — or, for an Exchange of Duty, one they are a party to — at **any** step (endorse, process/validate, recommend, approve, record). Applies to COSP leave, regular-leave recording, CTO, Exchange of Duty, attendance corrections (formal and minor), Official Business/Time, Travel, OT and group filings; HR never files or credits their own CTO claim. Queues and notifications leave the filer out.
- **Supervisor step = one level above the filer** (`leave/permissions.py endorsers_for`): an ordinary employee → any Supervisor of their section/unit; a **unit head** → the section-wide Supervisor (e.g. the Chief Nurse), never a fellow head of the same unit; a **section head** (no one above at Supervisor level) → the **AO**; the **AO's own request → the COH** (at the Supervisor and AO steps). The person who acts is stamped with their own name and position.
- HR/ICTU steps: any other holder of the role. AO step: the AO, or the COH when the AO is the filer. COH step: never the COH's own request.
- **Open (to confirm with the owner):** who approves the COH's own Travel/Official Business — today such a request has no approver and stays pending.
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
- **Monthly cap:** max 5 CTO days/month, no 3+ consecutive days
- **Filing:** claims filed the month after the compensable workday, with required attachments (Allowed to Work form, DTR/logbook copy, OT Accomplishment Report)
- **Forfeiture:** CTO must be used within the calendar year earned; auto-forfeited otherwise unless the Chief of Hospital grants a documented exception
- **Deadlines:** CTO filing deadline is Nov 30 each year; CTO usage dates cannot extend past Dec 15

### 7.1 Settled decisions — OT to CTO credit (2026-09-28)

These are decided by the project owner. Do not re-ask or change them without explicit confirmation.

- **Approved OT does NOT automatically generate CTO credit.** CTO credit is created only when HR files a CTO claim from the approved OT request and submits it after the required attachments are in place (Allowed to Work form, DTR/logbook copy, OT Accomplishment Report — or the Medical Transport set).
- The OT approval and the CTO claim are **separate records**; the claim links back to the approved OT for the audit trail.
- **One claim per workday.** HR enters that day's actual hours from the DTR; the total across all of one OT's claims cannot exceed the OT's approved hours. HR sets the rest-day/holiday flag per claim (a multi-day OT can mix weekdays and rest days).
- **Filing window:** no earlier than the 1st of the month after the workday, and no later than that year's filing deadline (Nov 30). November/December work therefore cannot be claimed without a Chief of Hospital exception.
- The Nov 30 filing deadline and Dec 15 usage cutoff are in settings (`CTO_FILING_DEADLINE_MONTH_DAY`, `CTO_USAGE_CUTOFF_MONTH_DAY`); multipliers stay in the `CTOMultiplierRate` table.
- **Exception claims** (no OT request, e.g. certified emergency duty): HR Administrator only, with a required reason, and subject to the same document and filing-date checks.

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
- **SMS:** not required — out of scope

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
- **SMS notifications** — not needed
- **Live/API biometric integration** — batch import only for now

---

## 13A. Future Expansion — Integrated HR and Administrative System (owner decisions 2026-10-17)

Planned after HRIS go-live and a stable period; not in scope yet. One system, one database, one employee list; each module its own Django app reusing roles/OIC, routing, digital stamps, documents, notifications, Audit Log and the menu.
- **HR:** Recruitment and Selection, Performance Management (CSC SPMS), Learning and Development.
- **Administrative:** Job Order, Trip Request and Vehicle Dispatching, Out-on-Pass (build only after the revised policy is approved), Activity Scheduler.
- **Travel authority = the existing HRIS Travel request** (OfficialRequest). Do NOT build a separate Travel Authorization module; the administrative side adds only vehicle dispatching (trip, vehicle, driver), linked to the approved Travel request.
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
