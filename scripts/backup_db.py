"""
Nightly full-database backup (CLAUDE.md §2).

Run on a schedule via Windows Task Scheduler (see run_backup.bat in this
same folder and DEPLOYMENT.md for how to register it) - not as a Django
management command, on purpose: a backup job should still be able to run
and report a clear failure even if the Django app itself is broken, so it
only needs `pg_dump` and the same BDH_HRIS_DB_* environment variables the
app already uses (one source of truth for DB connection settings, not two).

What it does:
  - Runs `pg_dump` against the configured database.
  - Writes a timestamped .sql file (CLAUDE.md's example: hris_backup_
    2026-09-26.sql - this adds a time component so two runs on the same day
    never silently overwrite each other) into BDH_HRIS_BACKUP_DIR.
  - Logs a one-line result (success + file size, or the failure) to
    backup.log in that same folder, and also prints it to stdout so it
    shows up in Task Scheduler's history if capture is enabled.
  - Exits non-zero on failure, so a failed nightly backup shows up as a
    failed task in Task Scheduler instead of silently doing nothing.

What it deliberately does NOT do (CLAUDE.md §2, "Retention: manual cleanup
by HR/IT - no auto-delete logic needed"): it never deletes or rotates old
backup files. That housekeeping is left to HR/IT to do by hand.

Configuration (environment variables):
  BDH_HRIS_DB_NAME       required - same value the Django app uses.
  BDH_HRIS_DB_USER        default: bdh_hris
  BDH_HRIS_DB_PASSWORD    default: "" (blank - not recommended; see
                           DEPLOYMENT.md for using a .pgpass file instead)
  BDH_HRIS_DB_HOST        default: localhost
  BDH_HRIS_DB_PORT        default: 5432
  BDH_HRIS_BACKUP_DIR     default: <project>/backups
                           IT should point this at a separate drive from
                           the OS/DB drive where possible (CLAUDE.md §2).
  BDH_HRIS_PG_DUMP_PATH   default: "pg_dump" (assumes it's on PATH). Set
                           the full path if PostgreSQL's bin folder isn't
                           on PATH, e.g.
                           "C:\\Program Files\\PostgreSQL\\16\\bin\\pg_dump.exe"

If BDH_HRIS_DB_NAME isn't set (i.e. the app is still running on the SQLite
dev fallback), this script exits with a clear message rather than trying
to pg_dump a database that doesn't exist - SQLite has no equivalent backup
step here; a plain file copy of db.sqlite3 covers dev/test use only and
isn't part of the production backup story.
"""

import datetime
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    db_name = os.environ.get("BDH_HRIS_DB_NAME")
    if not db_name:
        print(
            "BDH_HRIS_DB_NAME is not set - this looks like the SQLite dev "
            "fallback, not the production PostgreSQL database. Nothing to "
            "back up here; set BDH_HRIS_DB_NAME (and the other BDH_HRIS_DB_* "
            "variables) to point this at PostgreSQL before scheduling this "
            "script.",
            file=sys.stderr,
        )
        return 1

    db_user = os.environ.get("BDH_HRIS_DB_USER", "bdh_hris")
    db_password = os.environ.get("BDH_HRIS_DB_PASSWORD", "")
    db_host = os.environ.get("BDH_HRIS_DB_HOST", "localhost")
    db_port = os.environ.get("BDH_HRIS_DB_PORT", "5432")
    pg_dump_path = os.environ.get("BDH_HRIS_PG_DUMP_PATH", "pg_dump")

    backup_dir = Path(os.environ.get("BDH_HRIS_BACKUP_DIR", PROJECT_DIR / "backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now()
    out_file = backup_dir / f"hris_backup_{now:%Y-%m-%d_%H%M%S}.sql"
    log_file = backup_dir / "backup.log"

    env = os.environ.copy()
    if db_password:
        env["PGPASSWORD"] = db_password

    cmd = [
        pg_dump_path,
        "--host", db_host,
        "--port", str(db_port),
        "--username", db_user,
        "--format", "plain",
        "--no-password",
        "--file", str(out_file),
        db_name,
    ]

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)

    with log_file.open("a", encoding="utf-8") as log:
        if result.returncode == 0 and out_file.exists():
            size_mb = out_file.stat().st_size / (1024 * 1024)
            message = f"{now:%Y-%m-%d %H:%M:%S}  OK    {out_file.name}  ({size_mb:.1f} MB)"
            print(message)
            log.write(message + "\n")
            return 0
        else:
            message = (
                f"{now:%Y-%m-%d %H:%M:%S}  FAILED  pg_dump exit code {result.returncode}\n"
                f"    stderr: {result.stderr.strip()}"
            )
            print(message, file=sys.stderr)
            log.write(message + "\n")
            # A failed dump may still have left a partial/empty file behind.
            if out_file.exists() and out_file.stat().st_size == 0:
                out_file.unlink()
            return 1


if __name__ == "__main__":
    sys.exit(main())
