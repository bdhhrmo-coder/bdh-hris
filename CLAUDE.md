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
| **Exchange of Duty** | Employee A/B → Supervisor → HR → AO → COH |
| **Attendance Correction (formal)** | Employee → Supervisor → HR → AO |
| **Attendance Correction (minor administrative)** | HR Processor → HR Admin (no formal approval chain needed) |
| **Official Business** | Employee → Supervisor → HR → AO → COH |
| **Official Time** | Employee → HR → AO → COH (no Supervisor step) |
| **Travel** | Employee → Supervisor → AO → COH (no HR step) |
| **Authorized OT/restday/holiday work** | Employee → Supervisor → HR → AO → COH |

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
- **Monthly cap:** max 5 CTO days/month, no 3+ consecutive days
- **Filing:** claims filed the month after the compensable workday, with required attachments (Allowed to Work form, DTR/logbook copy, OT Accomplishment Report)
- **Forfeiture:** CTO must be used within the calendar year earned; auto-forfeited otherwise unless the Chief of Hospital grants a documented exception
- **Deadlines:** CTO filing deadline is Nov 30 each year; CTO usage dates cannot extend past Dec 15

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
