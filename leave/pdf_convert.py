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


def workbook_to_pdf(workbook, label: str) -> bytes:
    """Save `workbook` (an openpyxl Workbook) to a temp file, convert it to
    PDF with headless LibreOffice, and return the PDF bytes. `label` names
    the form in error messages (e.g. "CSC Form 6")."""
    executable = find_libreoffice()
    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path = Path(tmpdir) / "form.xlsx"
        workbook.save(xlsx_path)
        result = subprocess.run(
            [
                executable, "--headless", "--norestore",
                "--convert-to", "pdf", "--outdir", tmpdir, str(xlsx_path),
            ],
            capture_output=True, timeout=60,
        )
        pdf_path = Path(tmpdir) / "form.pdf"
        if not pdf_path.exists():
            raise RuntimeError(f"{label} PDF conversion failed: {result.stderr.decode(errors='replace')}")
        return pdf_path.read_bytes()
