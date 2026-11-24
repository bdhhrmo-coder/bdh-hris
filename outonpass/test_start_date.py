"""Out-on-Pass start date (owner decision 2026-11-24, Option B).

Before OUT_ON_PASS_START_DATE the module's menu items are hidden and its
pages show "not yet in use"; HR Administration -> Out-on-Pass Setup stays open.
"""

from datetime import date

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import RoleAssignment as RA

from .test_views import emp

FUTURE = date(2099, 1, 1)
PAST = date(2000, 1, 1)


def menu_urls(client):
    return [i["url"] for g in client.get("/hris/home/").context["nav_groups"] for i in g["items"]]


class StartDateTests(TestCase):
    def setUp(self):
        self.staff = emp("staff")
        self.hra = emp("hra", roles=[RA.HR_ADMINISTRATOR])

    def test_default_start_is_jan_11_2027(self):
        self.assertEqual(settings.OUT_ON_PASS_START_DATE, date(2027, 1, 11))

    @override_settings(OUT_ON_PASS_START_DATE=FUTURE)
    def test_before_start_menu_hidden_and_pages_closed(self):
        self.client.force_login(self.staff.user)
        self.assertFalse([u for u in menu_urls(self.client) if u.startswith("/out-on-pass/")])
        for name in ("outonpass:mine", "outonpass:new", "outonpass:queue", "outonpass:gate", "outonpass:report"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)
            self.assertTemplateUsed(resp, "outonpass/not_in_use.html")
            self.assertContains(resp, "not yet in use")
        resp = self.client.post(reverse("outonpass:new"), {})
        self.assertTemplateUsed(resp, "outonpass/not_in_use.html")

    @override_settings(OUT_ON_PASS_START_DATE=FUTURE)
    def test_before_start_setup_stays_open_for_hr_administrator(self):
        self.client.force_login(self.hra.user)
        self.assertEqual([u for u in menu_urls(self.client) if u.startswith("/out-on-pass/")],
                         [reverse("outonpass:setup")])
        resp = self.client.get(reverse("outonpass:setup"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateNotUsed(resp, "outonpass/not_in_use.html")

    @override_settings(OUT_ON_PASS_START_DATE=FUTURE)
    def test_before_start_signed_out_goes_to_login(self):
        resp = self.client.get(reverse("outonpass:mine"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(settings.LOGIN_URL, resp["Location"])

    @override_settings(OUT_ON_PASS_START_DATE=PAST)
    def test_from_start_date_menu_and_pages_work(self):
        self.client.force_login(self.staff.user)
        self.assertIn(reverse("outonpass:mine"), menu_urls(self.client))
        resp = self.client.get(reverse("outonpass:mine"))
        self.assertTemplateNotUsed(resp, "outonpass/not_in_use.html")
        self.assertTemplateUsed(resp, "outonpass/mine.html")

    @override_settings(OUT_ON_PASS_START_DATE=None)
    def test_blank_start_date_means_in_use(self):
        self.client.force_login(self.staff.user)
        self.assertTemplateUsed(self.client.get(reverse("outonpass:mine")), "outonpass/mine.html")
