# Deploying BDH HRIS (Windows Server, on-prem)

This covers CLAUDE.md §2's deployment target: Waitress + WhiteNoise, run as
a Windows Service via NSSM, on the hospital's local network only (HTTP,
no internet exposure, no HTTPS required). No IIS, no nginx, no Docker -
deliberately, so a small IT team without a dedicated sysadmin can run and
maintain it.

Read this top to bottom once before starting; it's written as a checklist.

---

## 1. Prerequisites

Install these on the Windows Server first:

- **Python 3.11 or later** (from python.org - check "Add python.exe to PATH"
  during install)
- **PostgreSQL 14+** (from postgresql.org) - create a database and a login
  role for the app now; you'll need the name/user/password below
- **Git** (to pull the code) - or copy the project folder some other way
- **NSSM** ("the Non-Sucking Service Manager") - download the zip from
  https://nssm.cc/download, unzip it somewhere permanent, e.g.
  `C:\Tools\nssm-2.24\`. There is no installer; you'll either add its `win64`
  folder to your PATH or point the install script at it directly.

- **LibreOffice** (free, from libreoffice.org) - needed only for printing the
  leave forms (CSC Form 6 and the COSP Leave form) as PDF. Without it,
  everything else works, but "Print" on a leave application shows a
  message asking the user to contact the System Administrator. The app
  looks for `soffice.exe` on PATH and in the usual install folders
  (`C:\Program Files\LibreOffice\program\`). If you installed it
  somewhere else, set `BDH_HRIS_LIBREOFFICE_PATH` in `.env` (see step 3).

You do **not** need IIS, nginx, or Docker Desktop.

---

## 2. Get the code and set up the virtualenv

From an ordinary (non-admin) command prompt or PowerShell:

```powershell
cd C:\
git clone <your-repo-url> BDH-HRIS
cd BDH-HRIS

py -3 -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
```

## 3. Configure the app

```powershell
copy .env.example .env
notepad .env
```

Fill in, at minimum:

- `BDH_HRIS_SECRET_KEY` - generate one:
  ```powershell
  .venv\Scripts\python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
  ```
- `BDH_HRIS_DEBUG=0` - debug is **off by default** if this line is missing;
  never set it to `1` on the real server
- `BDH_HRIS_ALLOWED_HOSTS` - the hostname(s)/IP staff will use to reach it,
  e.g. `bdh-hris,10.20.30.40`
- `BDH_HRIS_DB_NAME`, `BDH_HRIS_DB_USER`, `BDH_HRIS_DB_PASSWORD` - matching
  the PostgreSQL database/role from step 1
- `BDH_HRIS_MEDIA_ROOT` - a folder for uploaded supporting documents
  (medical certificates, IDs). These are confidential (CLAUDE.md §10); put
  this outside any web-served folder and restrict its NTFS permissions to
  the account the service runs as.
- `BDH_HRIS_BACKUP_DIR` - see §8 below
- `BDH_HRIS_LIBREOFFICE_PATH` - **optional.** Full path to `soffice.exe`,
  only if LibreOffice is installed in a non-standard folder, e.g.
  `C:\Tools\LibreOffice\program\soffice.exe`. Leave blank otherwise.

See every setting's comment in `.env.example` for details - don't guess at
one that isn't clear; ask the project owner.

## 4. Initialize the database and static files

(On a brand-new empty database there is nothing to back up yet. Every
migrate after real data exists must be preceded by a backup - see §9.)

```powershell
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py collectstatic --noinput
.venv\Scripts\python manage.py createsuperuser
```

`createsuperuser` creates the very first login - use it to sign in and set
up the first real System Administrator / HR Administrator employee records
and role assignments from inside the app afterward.

## 5. Test it before installing as a service

```powershell
.venv\Scripts\python serve.py
```

You should see `BDH HRIS: serving on http://0.0.0.0:8000 (4 threads)`.
From another machine on the same network, browse to
`http://<server-hostname-or-ip>:8000/login/` and confirm the login page
loads with the BDH logo and styling (not a plain, unstyled page - if it
looks unstyled, `collectstatic` didn't run, or `BDH_HRIS_DEBUG` is still
on and Waitress/WhiteNoise isn't the one serving static files yet).

Press Ctrl+C to stop it once confirmed. Don't leave it running in a console
window long-term - that's what the Windows Service in the next step is for.

## 6. Install as a Windows Service (Waitress + NSSM)

From an **elevated** (Run as Administrator) PowerShell prompt:

```powershell
cd C:\BDH-HRIS\deploy\windows
.\install_service.ps1
```

If `nssm.exe` isn't on your PATH:

```powershell
.\install_service.ps1 -NssmPath "C:\Tools\nssm-2.24\win64\nssm.exe"
```

This registers a service named **BDH-HRIS**, set to start automatically on
boot, logging to `C:\BDH-HRIS\logs\`. Confirm it's running:

```powershell
Get-Service BDH-HRIS
```

and browse to the app again, this time without a console window open.

**To update `.env` later** (a new setting, a rotated password, etc.), edit
the file and restart the service - serve.py loads `.env` itself on every
start, so a plain restart picks up the new values (no need to re-run
`install_service.ps1` unless you're also changing the project location or
Python path). Simple service commands you'll use day to day:

```powershell
nssm restart BDH-HRIS
nssm stop BDH-HRIS
nssm start BDH-HRIS
```

**To remove the service** (e.g. before moving the app to a different
folder): `deploy\windows\uninstall_service.ps1`. This never touches the
database or uploaded files.

## 7. Open the firewall

Staff on the hospital LAN need to reach the port Waitress listens on
(`8000` by default). From an elevated PowerShell prompt:

```powershell
New-NetFirewallRule -DisplayName "BDH HRIS" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

Adjust the port number if you changed `BDH_HRIS_PORT` in `.env`.

## 8. Set up the nightly backup

CLAUDE.md §2: a full `pg_dump` every night, saved as a timestamped `.sql`
file, ideally on a different drive than the OS/database. This project ships
`scripts/backup_db.py` (does the dump) and `scripts/run_backup.bat` (a
Task Scheduler-friendly wrapper).

Register it once, from an **elevated** (Run as Administrator) PowerShell
prompt. Change the two paths if the project isn't at `C:\BDH-HRIS`:

```powershell
$name = "BDH HRIS Nightly Backup"
$bat  = 'C:\BDH-HRIS\scripts\run_backup.bat'
$action    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$bat`"" -WorkingDirectory 'C:\BDH-HRIS\scripts'
$trigger   = New-ScheduledTaskTrigger -Daily -At 2:00AM
$settings  = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
```

Why it's set up this way (each of these broke the backup on the test PC):

- **The script path is quoted.** If the project folder has a space in its
  path (e.g. `C:\Users\WINDOWS 11\...`), an unquoted path makes Windows
  look for the wrong file, and the task fails without writing anything to
  `backup.log`. The older `schtasks /create /tr ...` form did exactly this.
- **`-WakeToRun`** lets the task wake the computer if it has gone to sleep.
  Better still, set the server's power plan to **never sleep** - a server
  that sleeps also stops serving the HRIS to staff.
- **`-StartWhenAvailable`** runs a missed backup as soon as the computer is
  back on, instead of skipping that night.
- **SYSTEM** runs it without storing anyone's password. This works because
  `.env` gives the full `pg_dump` path (`BDH_HRIS_PG_DUMP_PATH`) and the
  database password - the SYSTEM account doesn't have PostgreSQL on its PATH.

(`2:00AM` - change to whatever fits the hospital's quiet hours.)

Verify it once **as the task itself**, not just by running the .bat by hand
(running it by hand doesn't test the task's path, account or settings):

```powershell
Start-ScheduledTask -TaskName "BDH HRIS Nightly Backup"
Start-Sleep 25
Get-ScheduledTaskInfo -TaskName "BDH HRIS Nightly Backup" | Select-Object LastRunTime, LastTaskResult, NextRunTime
```

`LastTaskResult` must be `0`. Then, the morning after the first scheduled
night, check that a backup with a ~2:00 AM timestamp exists.

Check `%BDH_HRIS_BACKUP_DIR%\backup.log` afterward for an `OK` line and a
new `hris_backup_YYYY-MM-DD_HHMMSS.sql` file.

**Retention is intentionally manual** (CLAUDE.md §2) - nothing here deletes
old backups automatically. Someone on HR/IT should periodically clear out
backups that are no longer needed; how often and how far back to keep is
an operational decision for the hospital, not something to automate away.

## 9. Day-to-day operations

**Checking if it's up:** `Get-Service BDH-HRIS`, or just browse to the
login page.

**Logs:**
- App/service output: `C:\BDH-HRIS\logs\service-stdout.log` and
  `service-stderr.log` (rotate automatically at 10 MB)
- Backup history: `<BDH_HRIS_BACKUP_DIR>\backup.log`

**Deploying a code update:**

**Always back up the database first** - a migration changes the database
structure and cannot be casually undone. Run the backup by hand, and check
that a new `.sql` file appeared before continuing:

```powershell
C:\BDH-HRIS\scripts\run_backup.bat
```

Then update:

```powershell
cd C:\BDH-HRIS
git pull
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py collectstatic --noinput
nssm restart BDH-HRIS
```

**Restoring from a backup** (disaster recovery - test this once in a
non-production environment so it isn't the first time you're doing it
during an actual incident):

```powershell
psql -h localhost -U bdh_hris -d bdh_hris -f "D:\BDH-HRIS-Backups\hris_backup_2026-09-26_020000.sql"
```

---

## 10. Still outstanding (not blocked on deployment, but not done either)

Carried over from CLAUDE.md - deployment works without these, but they're
open items the project owner already flagged:

- **Email (GovMail SMTP):** CLAUDE.md §2/§11 - whether BDH's GovMail
  (Microsoft 365) tenant allows SMTP AUTH client submission on port 587, or
  needs OAuth2 app registration, hasn't been confirmed with IT/DICT yet. No
  `EMAIL_BACKEND` is configured for real sending yet; in-app notifications
  work today regardless.
- **Real biometric export columns:** the CSV/Excel importer
  (`attendance`) uses a configurable column mapping rather than hardcoded
  headers, so it's ready to be pointed at the real biometric system's
  export format whenever a sample file is available - nothing to change
  in code, just the mapping configuration once IT can provide one.

## 11. Before calling this "live" - a short QC pass

- [ ] Logged in as a test account for each role (Employee, Supervisor, HR
      Processor, HR Administrator, AO, COH, ICTU Staff, System Administrator) and
      confirmed each sees only what CLAUDE.md §3 says they should
- [ ] Submitted one of each request type end-to-end (Leave, CTO, Exchange
      of Duty, Attendance Correction, Official Business/Time/Travel/OT) and
      confirmed it routes and prints correctly
- [ ] Confirmed `BDH_HRIS_DEBUG` is `0` (or absent) in the real `.env` (a
      stray `DEBUG=1` in production would leak internal details in error pages)
- [ ] Printed one CSC Form 6 and one COSP Leave form as PDF and confirmed
      they open (this proves LibreOffice is installed and found)
- [ ] Took a backup before the first `migrate` on the server, and before
      every update after that (see §9)
- [ ] Ran the backup once **as the scheduled task** (`LastTaskResult` 0) and
      confirmed a real `.sql` file appears; the next morning, confirmed the
      ~2:00 AM backup ran on its own
- [ ] Rebooted the server once and confirmed the BDH-HRIS service comes
      back up on its own, with no one needing to log in and start it
- [ ] Set the server's power plan to **never sleep**
- [ ] Deleted every test account (`test.*`) and any sample data before
      loading real employees

---

## 12. Go-live: loading the real data (HR Tools -> Data Import)

Owner decisions (2026-10-06): in-app notifications only at go-live (email
added once GovMail SMTP is confirmed); **pilot one section first**, then
everyone.

Do these in order, on the real server, after the §11 QC pass:

1. **Back up** the database (§9).
2. **HR Tools -> Data Import -> Download template.** One Excel file with
   instructions, an Employees sheet, an Opening Balances sheet and the exact
   Section/Unit names.
3. **Employees sheet.** One row per employee. An HR Administrator should run
   this import if roles (Supervisor, HR, AO, COH, ICTU) are filled in - an
   HR Processor may import employees but not roles. Upload -> read the
   preview -> fix every red row -> upload again -> **Confirm and save**.
   Nothing is saved while any row has an error.
4. **Print the password slips** shown after the import and hand each one
   to the person personally. Then click "clear slips" - they cannot be
   shown again. (If a slip is lost, the System Administrator sets a new
   password in the admin and ticks "must change password" on the employee.)
5. **Download the template again** (its Opening Balances sheet now lists
   everyone) and fill in each balance **from the leave card, as of one
   cut-over date** - e.g. the last day of the month already credited.
   Upload -> check the preview ("balance now -> leave card") -> Confirm.
6. **Spot-check about 10% of employees**: open their leave balances and
   compare with the paper leave card. HRMO signs off before go-live.
7. **Pilot:** one section uses the HRIS for 2-4 weeks, with paper kept as
   backup. Fix what they find. Then go live for everyone on an announced
   date.

Leave balances are kept to 2 decimal places.

## 13. Yearly task: CTO forfeiture (first working day of January)

CLAUDE.md §7: CTO must be used within the calendar year earned; unused CTO
is forfeited unless the Chief of Hospital grants a documented exception.
This is deliberately run by a person, not automatically, so the COH's
exceptions stay a human decision.

1. Get the COH's list of approved exceptions (Employee IDs and reason).
2. Back up the database (§9).
3. Preview first - this changes nothing:
   ```powershell
   cd C:\BDH-HRIS
   .venv\Scripts\python manage.py forfeit_expired_cto --year 2026 --actor-username <hr-admin-username> --dry-run
   ```
4. Run it for real, adding one `--exempt` per approved exception:
   ```powershell
   .venv\Scripts\python manage.py forfeit_expired_cto --year 2026 --actor-username <hr-admin-username> --exempt EMP-0007 --exempt-reason "COH memo dated ..."
   ```
5. Keep the COH's exception memo on file. Running it twice for the same
   year does no harm. **Do not skip a year** - the calculation assumes it
   runs every year.
