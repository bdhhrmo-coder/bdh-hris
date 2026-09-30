"""
Tiny, dependency-free ".env" loader shared by manage.py and serve.py.

Why this exists: NSSM (the Windows Service wrapper - see
deploy/windows/install_service.ps1) was originally responsible for loading
.env into the SERVICE's own environment via its AppEnvironmentExtra
setting. In testing on a real Windows machine, NSSM silently dropped one
environment variable out of a full real .env (reproducible, but its exact
internal cause in NSSM's own command-line re-parsing wasn't worth chasing
further - values like the Django secret key are full of the kind of
punctuation that's exactly the sort of thing shell/argv re-parsing bugs
trip on). That's a fragile foundation for something as important as which
database the app connects to, so instead the app loads its own .env
directly - one less moving part, and NSSM no longer needs any special
configuration for it at all.

This deliberately does NOT depend on python-dotenv (CLAUDE.md §2 - keep
the dependency list short); it's about 15 lines of stdlib.

Behavior:
  - Only sets a variable if it is not ALREADY set in the real environment,
    so an operator can still override any single value for one run
    (e.g. `$env:BDH_HRIS_DEBUG=1; python serve.py`) without editing .env.
  - Missing .env file is not an error - matches the SQLite dev fallback
    already built into settings.py (see .env.example's own note on this).
  - Blank lines and lines starting with "#" are skipped, same convention
    as .env.example.
"""

from pathlib import Path


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from `path` into os.environ (does not override)."""
    import os

    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value
