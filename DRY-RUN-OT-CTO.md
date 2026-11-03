# OT / CTO dry run — branch `ot-cto-stage1`

For the test PC only. **Never run these steps on the live server**: they
create fake test logins and need `BDH_HRIS_DEBUG=1` for the seeding step.

The rules being tested are in CLAUDE.md §7.1 (pending approved HR memo).
Stage 2 (carry-over reasons, Dec 17 forfeiture, reminders, separation
status) is **not** in this build.

---

## A. Put the branch on the test PC

Run everything from PowerShell in the project folder (below: `C:\BDH-HRIS`).

1. **Load `.env` into this window.** `manage.py` does not read `.env` by
   itself (only the Windows service gets those settings, through NSSM).
   Without this step, `migrate` quietly works on a local SQLite file
   instead of PostgreSQL.
   ```powershell
   cd C:\BDH-HRIS
   Get-Content .env | Where-Object { $_ -match '^\s*[^#\s][^=]*=' } | ForEach-Object {
       $k, $v = $_ -split '=', 2; Set-Item "env:$($k.Trim())" $v.Trim() }
   .venv\Scripts\python manage.py shell -c "from django.db import connection as c; print(c.vendor, c.settings_dict['NAME'])"
   ```
   The last line must print `postgresql` and the test database name. If
   it prints `sqlite`, stop and fix `.env`.

2. **Back up the test database** (see DEPLOYMENT.md §8):
   `scripts\run_backup.bat`, and check `backup.log` for an `OK` line.

3. **Confirm there is no real OT/CTO data.** The new migrations drop the
   old CTO claim tables. Run this **before** switching branches:
   ```powershell
   .venv\Scripts\python manage.py shell -c "from official_requests.models import OfficialRequest as R; from cto.models import CTOCreditEntry as E; print('OT requests:', R.objects.filter(request_type='OT_RESTDAY_HOLIDAY').count(), '| CTO claims:', E.objects.count())"
   ```
   Both must be `0`. If not, stop and tell the project owner.

4. **Stop the service, switch branch, update:**
   ```powershell
   nssm stop BDH-HRIS
   git fetch
   git checkout ot-cto-stage1
   git pull
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\python manage.py migrate
   .venv\Scripts\python manage.py collectstatic --noinput
   ```

5. **Copy the test-account command.** `accounts\management\commands\seed_test_accounts.py`
   is kept out of git on purpose. Copy the updated file from the
   developer machine (USB or shared folder) into the same folder on the
   test PC, replacing any older copy.

6. **Create the test logins** (asks once for a shared password):
   ```powershell
   $env:BDH_HRIS_DEBUG = "1"
   .venv\Scripts\python manage.py seed_test_accounts
   Remove-Item env:BDH_HRIS_DEBUG
   ```

7. **Check the seeded CTO setup:**
   ```powershell
   .venv\Scripts\python manage.py shell -c "from cto.models import CTOMultiplierRate as R; print(list(R.objects.values_list('effective_date','weekday_multiplier','restday_holiday_multiplier')))"
   ```
   Expect a 2025-12-16 rate of `1.00` / `1.50`. The six CTO document
   requirements (3 for OT, 3 for Medical Transport) are created by the
   migrations. Step C2 shows them on screen.

8. **Check the PC clock** is on Manila time (UTC+08:00), then start:
   `nssm start BDH-HRIS`. Browse to `http://<test-pc>:8000/login/`.

**To undo:** `nssm stop BDH-HRIS`, restore the backup from step 2,
`git checkout main`, and `nssm start BDH-HRIS`.

---

## B. Test accounts

All share the password typed in step A6.

| Login | Role in this test | Section / unit |
|---|---|---|
| `testlabstaff` | Employee (COSP) — non-Nursing claims | Laboratory |
| `testlabsup` | Supervisor — files OT, endorses Lab claims | Laboratory (whole section) |
| `testnurse` | Employee — Nursing claims | Nursing / ER |
| `testunithead` | Unit Head — files Nursing OT, endorses ER claims | Nursing / ER unit |
| `testsupervisor` | **Chief Nurse** (section-wide Nursing Supervisor) | Nursing (whole section) |
| `testhrprocessor` | HR Processor | — |
| `testhradmin` | HR Administrator | — |
| `testhradmin2` | Second HR Administrator | — |
| `testao` | Administrative Officer | — |
| `testcoh` | Chief of Hospital | — |
| `testemployee`, `testcosp` | Other Nursing staff (no unit) | Nursing |

Everything below starts from **CTO → CTO queue** (`/cto/queue/`) or
**My CTO** (`/cto/mine/`).

**Dates:** claims can only be filed after the workday, and credit counts
from the 1st of the month after the workday. Use **October 2026** dates
for OT. They need a past-date reason, and once approved the credit is
usable straight away. Opening balances lock after **Nov 30, 2026**.

---

## C. Click-through checklist

Tick each box, and note the step number for anything that behaves
differently.

### C1. Filing OT
- [ ] `testlabstaff`: the CTO queue has **no** "File OT request" button. Typing `/cto/ot/new/` shows *Forbidden*.
- [ ] `testlabsup`: **File OT request**, filed as Supervisor, line = `testlabstaff`, Oct 5, 8 h, Weekday, past-date reason left blank → error asking for a reason.
- [ ] Same form with a reason → submitted. The detail page shows "waiting for HR". Route: HR → AO → COH.
- [ ] `testlabsup`: a new request with yourself on a line → error "cannot add yourself". With `testcoh` on a line → error.
- [ ] `testlabsup`: a new request with `testlabstaff` on **Oct 5 again** → error "already on OT request #…".
- [ ] **Add row:** the form starts with 3 rows. Click **+ Add row** 3 times → 6 rows. Fill 4 rows (different dates in Oct) and leave 2 blank → submitted with 4 lines.
- [ ] `testunithead`: file OT for `testnurse`, Oct 6 → the first step is **Chief Nurse** (`testsupervisor`).
- [ ] `testhrprocessor`: file OT (as HR) for `testlabstaff`, Oct 7 → route **AO → COH**.
- [ ] `testlabsup` (the employee's own Supervisor) gets an in-app notice that `testlabstaff` was added to an OT request filed by `testhrprocessor`. It is information only: the request is **not** in `testlabsup`'s queue and there is no Approve button.
- [ ] `testao`: file OT (as AO) for `testnurse`, Oct 8 → route **COH** only.

### C2. Approving and returning OT
- [ ] `testhrprocessor`: the first request appears under "OT requests awaiting my action". **Approve the whole batch**.
- [ ] `testao`: **Return** with the remark empty → error asking for a remark. Return with a remark → status Returned.
- [ ] `testlabsup`: the returned request shows **Edit and resubmit**. Change the hours and submit → back at **HR** (the chain restarts). History shows return and resubmit.
- [ ] `testhrprocessor` → `testao` → `testcoh`: approve → **Approved**. Approving creates **no** CTO credit (`testlabstaff` My CTO balance unchanged).
- [ ] **Approver on the batch:** `testlabsup` files OT with lines for `testlabstaff` **and** `testhrprocessor` (Oct 9) → a yellow warning suggests a separate batch. At the HR step it is **not** in `testhrprocessor`'s queue, and `testhradmin` approves it instead.
- [ ] **Skipped step:** `testhrprocessor` files OT for `testao` (Oct 10) → there is no other AO, so the AO step is skipped (History: "skip … every holder is on this record"). It waits for COH.
- [ ] Finish approving the Nursing OT (Chief Nurse → HR → AO → COH).

### C3. CTO claims
- [ ] `testlabstaff`: **My CTO → New CTO claim** → OT / rest day / holiday → the claim page lists the approved OT lines. Add a line with **more hours than approved** → error. Add it with the right hours.
- [ ] **Required documents** lists Allowed to Work form, DTR/logbook copy, OT Accomplishment Report as *Missing*, and **Submit** is disabled. Upload one small PDF for each → Submit enabled → submit.
- [ ] `testlabsup` endorses (Approve). At the HR step, `testhrprocessor` uses **Correct day type** → Rest day with a remark → "Would credit" changes from 1.00 to 1.50 day(s). The OT request's line still says Weekday. History shows the correction.
- [ ] `testao`: **Return to employee** with a remark → `testlabstaff` sees Returned. **First filed** stays the original date. Resubmit → starts again at the Supervisor.
- [ ] Approve through to `testcoh` → claim Approved. My CTO → **My CTO credit** shows the line with its effective date (the approval date for October work) and year earned 2026. The balance goes up.
- [ ] **Same-month work:** `testlabsup` files OT for `testlabstaff` dated **today** (e.g. Nov 3), with no past-date reason needed, and it is approved through to COH. `testlabstaff` files a claim against it **the same day** → accepted. After COH approval, My CTO credit shows **effective Dec 1** (marked "not yet usable"), and the balance does **not** rise until Dec 1.
- [ ] **Future workday:** file OT for `testlabstaff` dated a few days ahead and approve it. A claim line for it is refused ("only be filed after the workday") until that date arrives.
- [ ] `testnurse`: claim against the Nursing OT → route **Unit Head (`testunithead`) → Chief Nurse (`testsupervisor`) → HR → AO → COH**.
- [ ] **Medical Transport:** `testlabstaff` new claim → Medical Transport → enter date, hours, day type. Documents are Trip Ticket plus Certificate of Appearance **or** Logbook copy (either one satisfies the second).
- [ ] **24-hour limit:** on a date where `testlabstaff` already claimed 8 h of OT, add a Medical Transport line of 17 h → error "More than 24 hours".
- [ ] **Exception claim:** `testhradmin` → **+ Exception CTO claim** for `testlabstaff` with a reason, work date in **2025** → accepted (no filing deadline). Route **AO → COH**. Trying it for **yourself** → error.

### C4. Opening balances (before Nov 30)
- [ ] `testhradmin` → **Opening CTO balances** → `testlabstaff`: add 1.5 days earned 2026-03-02 with reason "paper card" → listed, and the balance rises immediately.
- [ ] Add a line with date earned in **2025** → error (use an exception claim instead). Blank reason → error.
- [ ] Choose **yourself** (`testhradmin`) → "can't enter lines". `testhradmin2` can enter for `testhradmin`.
- [ ] **Void** a line with a reason → status Voided, the balance drops, nothing edited in place.
- [ ] `testlabstaff` applies for CTO using that credit, then `testhradmin` tries to void the line → error "already been used or held".
- [ ] `testlabstaff`: the opening record never appears under "My CTO claims" or in anyone's queue.

### C5. Using CTO
- [ ] `testlabstaff` **Apply for CTO**: more days than the balance → "Insufficient CTO balance". Three days in a row → refused. A sixth day in one month → refused.
- [ ] An application with an **end date after Dec 15** (e.g. Dec 16) → refused ("cannot extend past 12/15"). Stage 2 will replace this check with each credit's own use deadline, which moves when credit carries over.
- [ ] A valid 1-day application → the balance drops at once (the days are held). `testlabsup` **Return** → the balance comes back.
- [ ] A valid application approved through Supervisor → HR → AO → COH → Approved, and the balance stays reduced.

### C6. Who can see what
- [ ] `testemployee` opens a `testlabstaff` claim URL (copy it from the address bar) → *Forbidden*.
- [ ] `testlabstaff` cannot approve their own claim (no Approve button, and posting does nothing).
- [ ] `testhradmin` → **Audit log**: modules *Overtime* and *CTO Claim* appear, with "Step skipped", "Day type corrected", "Opening CTO line added/voided".

### What to send back
For each unticked box: the step number, the login used, what you expected,
what happened, and a screenshot.
