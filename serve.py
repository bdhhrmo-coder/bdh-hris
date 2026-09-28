"""
Production entry point for BDH HRIS (CLAUDE.md §2: Waitress, run as a
Windows Service via NSSM — see deploy/windows/install_service.ps1).

This is a plain script, not a Django management command, on purpose: NSSM
just needs one executable + one argument to supervise, and a script that
only imports Waitress and the WSGI app is the smallest thing that can break.

Usage (from the project's virtualenv):

    python serve.py

Configuration is via the same BDH_HRIS_* environment variables as the rest
of the app (see .env.example) plus:

    BDH_HRIS_HOST     interface to bind to (default 0.0.0.0 - all interfaces
                       on the hospital LAN; the server has no public NIC)
    BDH_HRIS_PORT     TCP port to listen on (default 8000)
    BDH_HRIS_THREADS  Waitress worker threads (default 4 - plenty for ~200
                       staff on an internal admin app; raise only if IT
                       observes the app queueing requests under load)

Before running this for the first time, or after a code update, run the
usual Django release steps first (see DEPLOYMENT.md):

    python manage.py migrate
    python manage.py collectstatic --noinput
"""

import os

from waitress import serve

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bdh_hris.settings")

from bdh_hris.wsgi import application  # noqa: E402 (must follow the env var above)

if __name__ == "__main__":
    host = os.environ.get("BDH_HRIS_HOST", "0.0.0.0")
    port = int(os.environ.get("BDH_HRIS_PORT", "8000"))
    threads = int(os.environ.get("BDH_HRIS_THREADS", "4"))

    print(f"BDH HRIS: serving on http://{host}:{port} ({threads} threads)")
    serve(application, host=host, port=port, threads=threads)
