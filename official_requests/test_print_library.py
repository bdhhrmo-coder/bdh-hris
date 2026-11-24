"""Fix 2026-11-24: "Server Error (500)" on "File for a Group".

The test PC had no python-docx (the Word library used only by the Travel
Order, F59 and Out-on-Pass slip printouts): it was never listed in
requirements.txt, and the request forms imported it through f59.py just to
get the Type of Transport choices. These tests fail on the old code.
"""

import ast
import re
import sys
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from orgstructure.models import Section

from .test_batch import emp

# Import name -> package name in requirements.txt, where they differ.
PACKAGE_FOR = {"docx": "python-docx", "psycopg2": "psycopg2-binary", "django": "Django"}


class FormsOpenWithoutPrintLibraryTests(TestCase):
    """Filing must not depend on the printout library."""

    def setUp(self):
        lab = Section.objects.get(name="Laboratory Section")
        self.sup = emp("sup", lab, "SUPERVISOR")

    def _without_docx(self):
        # None in sys.modules makes "import docx" raise ImportError, as on a
        # PC where python-docx is not installed; f59 must be imported afresh.
        patcher = mock.patch.dict(sys.modules, {"docx": None})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in [m for m in sys.modules if m.startswith("official_requests.f59")
                     or m.startswith("official_requests.travel_order")]:
            sys.modules.pop(name)

    def test_file_for_a_group_opens(self):
        self._without_docx()
        self.client.force_login(self.sup.user)
        self.assertEqual(self.client.get(reverse("official_requests:batch_file")).status_code, 200)

    def test_single_request_form_opens(self):
        self._without_docx()
        self.client.force_login(self.sup.user)
        self.assertEqual(self.client.get(reverse("official_requests:request_apply")).status_code, 200)


class RequirementsListEveryLibraryTests(SimpleTestCase):
    """Every outside library the code imports is in requirements.txt, so a
    fresh install (pip install -r requirements.txt) has all of them."""

    def test_every_third_party_import_is_listed(self):
        base = Path(settings.BASE_DIR)
        listed = {re.split(r"[=<>\[ ]", line.strip(), 1)[0].lower()
                  for line in (base / "requirements.txt").read_text().splitlines()
                  if line.strip() and not line.strip().startswith("#")}
        local = {p.name for p in base.iterdir() if (p / "__init__.py").exists()} | {p.stem for p in base.glob("*.py")}
        missing = {}
        for path in base.rglob("*.py"):
            if any(part.startswith(".") or part in ("venv", "node_modules") for part in path.relative_to(base).parts):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top in sys.stdlib_module_names or top in local:
                        continue
                    if PACKAGE_FOR.get(top, top).lower() not in listed:
                        missing.setdefault(PACKAGE_FOR.get(top, top), str(path.relative_to(base)))
        self.assertEqual(missing, {}, "Imported but not in requirements.txt (package: first file)")
