"""
Shared "filled .xlsx form -> PDF" step for the CSC Form 6 and COSP Leave
form (leave/csc_form6.py, leave/cosp_leave_form.py), done by running
LibreOffice headless.

Why this exists: both forms used to call a command literally named
"libreoffice". That name works on Linux/macOS but not on Windows, where the
program is soffice.exe and usually isn't on PATH - so on the Windows Server
deployment target (CLAUDE.md §2) the printable forms would have failed even
with LibreOffice installed. The lookup order here is:

  1. settings.LIBREOFFICE_PATH (env BDH_HRIS_LIBREOFFICE_PATH), if set
  2. "libreoffice" or "soffice" on PATH
  3. LibreOffice's usual Windows install folders
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

_WINDOWS_DEFAULTS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def find_libreoffice() -> str:
    """Return the LibreOffice executable to run, or raise RuntimeError with
    an instruction the System Administrator can act on."""
    configured = getattr(settings, "LIBREOFFICE_PATH", "") or ""
    if configured:
        if os.path.isfile(configured):
            return configured
        raise RuntimeError(
            f"BDH_HRIS_LIBREOFFICE_PATH is set to '{configured}', but no file exists there."
        )

    for name in ("libreoffice", "soffice"):
        found = shutil.which(name)
        if found:
            return found

    for candidate in _WINDOWS_DEFAULTS:
        if os.path.isfile(candidate):
            return candidate

    raise RuntimeError(
        "LibreOffice was not found, so the form cannot be converted to PDF. "
        "Install LibreOffice on the server, or set BDH_HRIS_LIBREOFFICE_PATH in .env "
        "to the full path of soffice.exe (see DEPLOYMENT.md)."
    )


def _convert_to_pdf(save, filename: str, label: str) -> bytes:
    """Save the document with `save(path)` to a temp file, convert it to PDF
    with headless LibreOffice, and return the PDF bytes.

    Each conversion uses its own throw-away LibreOffice profile folder
    (-env:UserInstallation, fix 2026-11-03). Without it, LibreOffice run by
    the Windows service account often does nothing - no profile it can use,
    a first-start dialog nobody can click, or a LibreOffice window already
    open on the server that takes the job and drops it - and the form "cannot
    be converted to PDF". A private profile also lets two people print at
    the same moment."""
    executable = find_libreoffice()
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / filename
        save(src)
        profile = (Path(tmpdir) / "lo_profile").resolve().as_uri()
        try:
            result = subprocess.run(
                [
                    executable, f"-env:UserInstallation={profile}",
                    "--headless", "--norestore", "--nologo", "--nodefault", "--nolockcheck",
                    "--convert-to", "pdf", "--outdir", tmpdir, str(src),
                ],
                capture_output=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{label} PDF conversion timed out after 120 seconds ({executable}).")
        pdf_path = src.with_suffix(".pdf")
        if not pdf_path.exists():
            detail = (result.stderr or result.stdout or b"").decode(errors="replace").strip() or "no message"
            raise RuntimeError(f"{label} PDF conversion failed (exit code {result.returncode}, "
                               f"{executable}): {detail}")
        return pdf_path.read_bytes()


def workbook_to_pdf(workbook, label: str) -> bytes:
    """An openpyxl Workbook -> PDF bytes. `label` names the form in errors."""
    return _convert_to_pdf(workbook.save, "form.xlsx", label)


def docx_to_pdf(document, label: str) -> bytes:
    """A python-docx Document -> PDF bytes (the Travel Order, 2026-11-03)."""
    return _convert_to_pdf(document.save, "form.docx", label)
