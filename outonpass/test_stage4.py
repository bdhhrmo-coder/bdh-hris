"""Out-on-Pass stage 4: the slip printout, the monthly report and HR setup (CLAUDE.md 13B)."""

from datetime import date, datetime, time

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import RoleAssignment as RA
from employees.models import Employee
from orgstructure.models import Section

from . import report, rules
from .models import CospContractPeriod, OutOnPass as P, OutOnPassAction, PassOpeningUsage
from .slip import build_document


# These tests exercise the module itself, so it is switched on regardless of
# the start date (outonpass/availability.py, 2026-11-24).
_IN_USE = override_settings(OUT_ON_PASS_START_DATE=None)


def setUpModule():
    _IN_USE.enable()


def tearDownModule():
    _IN_USE.disable()

DAY = date(2027, 2, 3)


def at(h, m=0, d=DAY):
    return timezone.make_aware(datetime.combine(d, time(h, m)))


def emp(username, section=None, roles=(), status="REGULAR", under="", position=""):
    u = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=u, employee_id=username.upper(), surname=username.title(), first_name="T",
                                employment_status=status, cosp_under=under, position=position)
    if section:
        e.sections.add(section)
    for r in roles:
        RA.objects.create(employee=e, role=r, section=section if r == RA.SUPERVISOR else None)
    return e


class Base(TestCase):
    def setUp(self):
        self.lab = Section.objects.get(name="Laboratory Section")
        self.head = emp("labhead", self.lab, [RA.SUPERVISOR], position="Medical Technologist III")
        self.staff = emp("staff", self.lab, position="Medical Technologist I")
        self.ao = emp("ao", roles=[RA.ADMINISTRATIVE_OFFICER], position="Administrative Officer V")
        self.coh = emp("coh", roles=[RA.CHIEF_OF_HOSPITAL])
        self.hradmin = emp("hradmin", roles=[RA.HR_ADMINISTRATOR])

    def make(self, e, d=DAY, kind=P.ORDINARY, status=P.COMPLETED, out=time(10), back=time(11), charged=None,
             excess=0, over=0):
        p = P.objects.create(employee=e, kind=kind, pass_date=d, time_out=out, time_in=back,
                             reason_type=P.REASON_MEDICAL, reason="Check-up", status=status, filed_by=e.user,
                             minutes_charged=charged, excess_minutes=excess, over_balance_minutes=over,
                             confirmed_coverage=True, confirmed_no_surge=True)
        return p


class SlipTests(Base):
    def text(self, doc):
        out = [p.text for p in doc.paragraphs]
        for row in doc.tables[0].rows:
            for c in row.cells:
                out.append(c.text)
        return "\n".join(out)

    def test_completed_pass_fills_every_part(self):
        p = self.make(self.staff, charged=47)
        P.objects.filter(pk=p.pk).update(tapped_out_at=at(10, 2), tapped_in_at=at(10, 49))
        p.refresh_from_db()
        OutOnPassAction.objects.create(out_on_pass=p, action="submit", resulting_status=P.SUBMITTED,
                                       acted_by=self.staff.user)
        OutOnPassAction.objects.create(out_on_pass=p, action="endorse", resulting_status=P.ENDORSED,
                                       acted_by=self.head.user)
        OutOnPassAction.objects.create(out_on_pass=p, action="approve", resulting_status=P.APPROVED,
                                       acted_by=self.ao.user)
        t = self.text(build_document(p))
        for expected in (p.pass_number, "☒ Permanent", "☒ Personal pass", "☒ Medical appointment", "Check-up",
                         "☒ Endorsed", "☒ The unit can be covered.", "☒ No surge", "DIGITALLY ENDORSED",
                         "LABHEAD", "☒ Within all limits", "☒ Approved", "DIGITALLY APPROVED", "AO",
                         "10:02 AM", "10:49 AM", "0 h 47 min"):
            self.assertIn(expected, t)

    def test_pending_and_returned(self):
        p = self.make(self.staff, status=P.SUBMITTED)
        OutOnPassAction.objects.create(out_on_pass=p, action="submit", resulting_status=P.SUBMITTED,
                                       acted_by=self.staff.user)
        self.assertIn("PENDING", self.text(build_document(p)))
        OutOnPassAction.objects.create(out_on_pass=p, action="return", resulting_status=P.RETURNED,
                                       acted_by=self.head.user, notes="Short-staffed")
        P.objects.filter(pk=p.pk).update(status=P.RETURNED)
        p.refresh_from_db()
        t = self.text(build_document(p))
        self.assertIn("☒ Returned.", t)
        self.assertIn("Short-staffed", t)

    def test_pgss_and_coh(self):
        guard = emp("guard", status="COSP", under="PGSS")
        self.assertIn("☒ PGSS (Security)", self.text(build_document(self.make(guard, status=P.SUBMITTED))))
        p = self.make(self.coh, status=P.RECORDED)
        P.objects.filter(pk=p.pk).update(recorded_only=True)
        p.refresh_from_db()
        self.assertIn("NOT APPLICABLE", self.text(build_document(p)))

    def test_print_view_permissions(self):
        p = self.make(self.staff, status=P.SUBMITTED)
        other = emp("pharm", Section.objects.get(name="Pharmacy Section"))
        self.client.force_login(other.user)
        self.assertEqual(self.client.get(reverse("outonpass:print_slip", args=[p.pk])).status_code, 403)
        self.client.force_login(self.staff.user)
        r = self.client.get(reverse("outonpass:print_slip", args=[p.pk]))
        self.assertIn(r.status_code, (200, 302))  # 302 only where LibreOffice is missing
        if r.status_code == 200:
            self.assertEqual(r["Content-Type"], "application/pdf")


class ReportTests(Base):
    def test_figures(self):
        self.make(self.staff, charged=47)
        self.make(self.staff, d=date(2027, 2, 4), charged=130, excess=10)
        self.make(self.head, kind=P.THIRD, charged=60)
        self.make(self.head, d=date(2027, 2, 5), kind=P.EXCEPTION, charged=150, excess=30, over=0)
        self.make(self.staff, d=date(2027, 2, 9), status=P.REJECTED)
        self.make(self.staff, d=date(2027, 3, 2), charged=20)
        r = report.monthly(2027, 2)
        self.assertEqual(r["totals"]["passes"], 4)
        self.assertEqual(r["totals"]["minutes"], 47 + 130 + 60 + 150)
        self.assertEqual((r["totals"]["third"], r["totals"]["exception"]), (1, 1))
        self.assertEqual(len(r["special"]), 2)
        self.assertEqual(len(r["not_covered"]), 2)

    def test_low_balance_and_repeaters(self):
        PassOpeningUsage.objects.create(employee=self.staff, year=2027, minutes_used=680, reason="tracker",
                                        entered_by=self.hradmin.user)
        for m in (1, 2, 3):
            self.make(self.head, d=date(2027, m, 10), kind=P.THIRD, charged=10)
        r = report.monthly(2027, 3)
        self.assertIn(self.staff, [x["employee"] for x in r["low"]])
        self.assertEqual([e for e, _ in r["repeaters"]], [self.head])

    def test_access_and_exports(self):
        url = reverse("outonpass:report")
        self.client.force_login(self.staff.user)
        self.assertEqual(self.client.get(url).status_code, 403)
        for who in (self.hradmin, self.ao, self.coh):
            self.client.force_login(who.user)
            self.assertEqual(self.client.get(url + "?month=2027-02").status_code, 200)
        self.make(self.staff, charged=47)
        r = self.client.get(url + "?month=2027-02&format=xlsx")
        self.assertIn("spreadsheetml", r["Content-Type"])
        r = self.client.get(url + "?month=2027-02&format=pdf")
        self.assertEqual(r["Content-Type"], "application/pdf")


class SetupTests(Base):
    def post(self, who, data):
        self.client.force_login(who.user)
        return self.client.post(reverse("outonpass:setup"), data)

    def test_contract_add_overlap_void(self):
        cosp = emp("cosp", self.lab, status="COSP")
        self.post(self.hradmin, {"what": "contract", "employee": cosp.pk, "start_date": "2027-01-01",
                                 "end_date": "2027-06-30"})
        self.assertEqual(rules.allowance_minutes(cosp, 2027), 6 * 60)
        self.post(self.hradmin, {"what": "contract", "employee": cosp.pk, "start_date": "2027-06-01",
                                 "end_date": "2027-12-31"})
        self.assertEqual(CospContractPeriod.objects.count(), 1)  # overlaps: refused
        c = CospContractPeriod.objects.get()
        self.post(self.hradmin, {"what": "void_contract", "pk": c.pk, "reason": ""})
        c.refresh_from_db()
        self.assertIsNone(c.voided_at)
        self.post(self.hradmin, {"what": "void_contract", "pk": c.pk, "reason": "Wrong end date"})
        self.assertEqual(rules.allowance_minutes(cosp, 2027), 0)

    def test_opening_line_and_own_record(self):
        self.post(self.hradmin, {"what": "opening", "employee": self.staff.pk, "year": 2027, "hours": 1,
                                 "minutes": 30, "passes_used_in_month": 1, "reason": "Tracker"})
        self.assertEqual(rules.used_minutes(self.staff, 2027), 90)
        self.post(self.hradmin, {"what": "opening", "employee": self.hradmin.pk, "year": 2027, "hours": 1,
                                 "minutes": 0, "passes_used_in_month": 0, "reason": "Tracker"})
        self.assertFalse(PassOpeningUsage.objects.filter(employee=self.hradmin).exists())

    def test_only_hr_administrator(self):
        self.client.force_login(self.ao.user)
        self.assertEqual(self.client.get(reverse("outonpass:setup")).status_code, 403)

    def test_audit_log_shows_setup_and_pass_actions(self):
        from auditlog.aggregation import audit_rows
        self.post(self.hradmin, {"what": "opening", "employee": self.staff.pk, "year": 2027, "hours": 0,
                                 "minutes": 45, "passes_used_in_month": 0, "reason": "Tracker"})
        p = self.make(self.staff, status=P.SUBMITTED)
        OutOnPassAction.objects.create(out_on_pass=p, action="submit", resulting_status=P.SUBMITTED,
                                       acted_by=self.staff.user)
        actions = [r["action"] for r in audit_rows(module="Out-on-Pass")]
        self.assertIn("Opening hours added", actions)
        self.assertIn("submit", actions)
