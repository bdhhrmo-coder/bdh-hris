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
- `BDH_HRIS_DEBUG=0`
- `BDH_HRIS_ALLOWED_HOSTS` - the hostname(s)/IP staff will use to reach it,
  e.g. `bdh-hris,10.20.30.40`
- `BDH_HRIS_DB_NAME`, `BDH_HRIS_DB_USER`, `BDH_HRIS_DB_PASSWORD` - matching
  the PostgreSQL database/role from step 1
- `BDH_HRIS_MEDIA_ROOT` - a folder for uploaded supporting documents
  (medical certificates, IDs). These are confidential (CLAUDE.md §10); put
  this outside any web-served folder and restrict its NTFS permissions to
  the account the service runs as.
- `BDH_HRIS_BACKUP_DIR` - see §6 below

See every setting's comment in `.env.example` for details - don't guess at
one that isn't clear; ask the project owner.

## 4. Initialize the database and static files

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
the file and re-run `install_service.ps1` - it's safe to re-run and picks
up the new values. Simple service commands you'll use day to day:

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

Register it once, from an elevated PowerShell/Command Prompt:

```powershell
schtasks /create /tn "BDH HRIS Nightly Backup" /tr "C:\BDH-HRIS\scripts\run_backup.bat" /sc daily /st 02:00 /ru SYSTEM
```

(`/st 02:00` = 2:00 AM; change to whatever fits the hospital's quiet hours.
`/ru SYSTEM` runs it without needing a specific user's password stored -
fine as long as the SYSTEM account can reach PostgreSQL and the backup
folder; if PostgreSQL is set to only trust specific Windows users, use
`/ru <a-service-account>` instead and you'll be prompted for its password.)

Verify it once by hand:

```powershell
C:\BDH-HRIS\scripts\run_backup.bat
```

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
      Processor, HR Administrator, AO, COH, System Administrator) and
      confirmed each sees only what CLAUDE.md §3 says they should
- [ ] Submitted one of each request type end-to-end (Leave, CTO, Exchange
      of Duty, Attendance Correction, Official Business/Time/Travel/OT) and
      confirmed it routes and prints correctly
- [ ] Confirmed `BDH_HRIS_DEBUG=0` in the real `.env` (a stray `DEBUG=1` in
      production would leak internal details in error pages)
- [ ] Ran the backup once by hand and confirmed a real `.sql` file appears
- [ ] Rebooted the server once and confirmed the BDH-HRIS service comes
      back up on its own, with no one needing to log in and start it
