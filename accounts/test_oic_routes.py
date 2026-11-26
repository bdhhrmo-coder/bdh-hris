"""Item 5, audit fixes F2/F3 (owner Decision 1, 2026-11-26). Made-up data only.

1. An OIC for the AO or COH who already acted on a request in this cycle must type a remark to act
   again; the remark reads "<Done> as OIC for the COH, after <earlier step> it earlier: <remark>",
   is kept on the request and flagged in the Audit Log. Checked on every route where the AO or COH acts.
2. The OIC's own request: for each kind of OIC (the AO, the Chief Nurse, a section Supervisor and a
   Unit Head), they are never an actor at the AO or COH step of a request they filed.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auditlog.aggregation import audit_rows
from employees.models import Employee
from leave.models import LeaveApplication, LeaveType
from leave.permissions import OIC_REPEAT_TAG, ao_step_actors, coh_step_actors, oic_repeat_guard
from orgstructure.models import Section, Unit

from .models import RoleAssignment as R


def person(username, status="REGULAR", section=None, unit=None, role=None, role_section=None, role_unit=None):
    user = User.objects.create_user(username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(), first_name="T",
                                date_hired=date(2020, 1, 1), employment_status=status)
    if section:
        e.sections.add(section)
    if unit:
        e.units.add(unit)
    if role:
        R.objects.create(employee=e, role=role, section=role_section, unit=role_unit)
    return e


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.lab = Section.objects.get(name="Laboratory Section")
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.er = Unit.objects.get(name="ER")
        self.head = person("r5head", section=self.lab, role=R.SUPERVISOR, role_section=self.lab)
        self.chief_nurse = person("r5cn", section=self.nursing, role=R.SUPERVISOR, role_section=self.nursing)
        self.unit_head = person("r5uh", section=self.nursing, unit=self.er, role=R.SUPERVISOR, role_unit=self.er)
        self.staff = person("r5staff", status=Employee.EMPLOYMENT_STATUS_COSP, section=self.lab)
        self.regular = person("r5reg", section=self.lab)
        self.hr = person("r5hr", role=R.HR_PROCESSOR)
        self.hradmin = person("r5hra", role=R.HR_ADMINISTRATOR)
        self.ao = person("r5ao", role=R.ADMINISTRATIVE_OFFICER)
        self.coh = person("r5coh", role=R.CHIEF_OF_HOSPITAL)

    def oic(self, who, role):
        delegator = self.ao if role == R.ADMINISTRATIVE_OFFICER else self.coh
        return R.objects.create(employee=who, role=role, is_oic=True, delegated_by=delegator,
                                start_date=self.today, end_date=self.today + timedelta(days=5))

    def post(self, who, url, data):
        self.client.force_login(who.user)
        return self.client.post(url, data, follow=True)

    def assert_refused(self, response):
        self.assertContains(response, "You acted on this request earlier.")


class RemarkRequiredOnEveryRoute(Base):
    """The AO, OIC for the COH, recommended at the AO step; acting again at the COH step needs a remark."""

    def setUp(self):
        super().setUp()
        self.oic(self.ao, R.CHIEF_OF_HOSPITAL)

    def leave(self, employee, route, status):
        code = "COSP_LEAVE" if route == LeaveApplication.ROUTE_COSP else "VL"
        app = LeaveApplication.objects.create(
            employee=employee, leave_type=LeaveType.objects.get(code=code), route=route, status=status,
            start_date=self.today + timedelta(days=20), end_date=self.today + timedelta(days=20),
            number_of_days=Decimal("1"))
        app.actions.create(action="recommend" if route == LeaveApplication.ROUTE_COSP else "review",
                           resulting_status=status, acted_by=self.ao.user)
        return app

    def test_cosp_leave_refused_then_accepted_with_the_wording(self):
        app = self.leave(self.staff, LeaveApplication.ROUTE_COSP, LeaveApplication.RECOMMENDED_BY_AO)
        url = reverse("leave:leave_action", args=[app.pk])
        self.assert_refused(self.post(self.ao, url, {"action": "approve", "notes": ""}))
        app.refresh_from_db()
        self.assertEqual(app.status, LeaveApplication.RECOMMENDED_BY_AO)  # nothing changed
        self.post(self.ao, url, {"action": "approve", "notes": "Staffing checked"})
        app.refresh_from_db()
        self.assertEqual(app.status, LeaveApplication.APPROVED)
        last = app.actions.order_by("-pk").first()
        self.assertEqual(last.notes, f"{OIC_REPEAT_TAG} Approved as OIC for the COH, after recommending it "
                                     "earlier: Staffing checked")
        flagged = [r for r in audit_rows(module="Leave") if "OIC, acted earlier" in r["action"]]
        self.assertEqual(len(flagged), 1)

    def test_regular_leave(self):
        app = self.leave(self.regular, LeaveApplication.ROUTE_REGULAR, LeaveApplication.RECOMMENDED_BY_AO)
        self.assert_refused(self.post(self.ao, reverse("leave:leave_action", args=[app.pk]),
                                      {"action": "recommend_capitol", "notes": ""}))
        app.refresh_from_db()
        self.assertEqual(app.status, LeaveApplication.RECOMMENDED_BY_AO)

    def test_cto_application(self):
        from cto.models import CTOUsageApplication as U

        app = U.objects.create(employee=self.staff, start_date=self.today + timedelta(days=9),
                               end_date=self.today + timedelta(days=9), number_of_days=Decimal("1"),
                               status=U.RECOMMENDED_BY_AO)
        app.actions.create(action="recommend", resulting_status=U.RECOMMENDED_BY_AO, acted_by=self.ao.user)
        self.assert_refused(self.post(self.ao, reverse("cto:cto_action", args=[app.pk]),
                                      {"action": "approve", "notes": ""}))
        app.refresh_from_db()
        self.assertEqual(app.status, U.RECOMMENDED_BY_AO)

    def claim(self, exception=False):
        from cto.models import STEP_AO, STEP_COH, CTOClaim, RoutedRecord

        claim = CTOClaim.objects.create(
            employee=self.staff, filed_by=(self.hradmin if exception else self.staff).user,
            exception_reason="Paper record of December duty" if exception else "",
            status=RoutedRecord.PENDING, current_step=STEP_COH, first_filed_on=self.today)
        claim.actions.create(action="submit", acted_by=claim.filed_by)
        claim.actions.create(action="approve", step=STEP_AO, acted_by=self.ao.user)
        return claim

    def _claim_case(self, exception):
        from cto import routing
        from cto.models import STEP_COH

        claim = self.claim(exception)
        claim.route = ",".join(routing.claim_route(claim))
        claim.save(update_fields=["route"])
        self.assertTrue(routing.can_act(self.ao, claim))
        r = self.post(self.ao, reverse("cto:claim_action", args=[claim.pk]), {"action": "approve", "notes": ""})
        self.assert_refused(r)
        claim.refresh_from_db()
        self.assertEqual(claim.current_step, STEP_COH)

    def test_cto_claim(self):
        self._claim_case(exception=False)

    def test_cto_exception_claim(self):
        self._claim_case(exception=True)

    def test_exchange_of_duty(self):
        from exchange.models import DutyExchangeRequest as X

        other = person("r5staff2", status=Employee.EMPLOYMENT_STATUS_COSP, section=self.lab)
        x = X.objects.create(employee_a=self.staff, employee_b=other, date_a=self.today + timedelta(days=10),
                             date_b=self.today + timedelta(days=11), status=X.RECOMMENDED_BY_AO)
        x.actions.create(action="recommend", resulting_status=X.RECOMMENDED_BY_AO, acted_by=self.ao.user)
        self.assert_refused(self.post(self.ao, reverse("exchange:exchange_action", args=[x.pk]),
                                      {"action": "approve", "notes": ""}))
        x.refresh_from_db()
        self.assertEqual(x.status, X.RECOMMENDED_BY_AO)

    def test_date_change(self):
        from datechange.models import DateChangeRequest as DC

        app = self.leave(self.staff, LeaveApplication.ROUTE_COSP, LeaveApplication.APPROVED)
        change = DC.objects.create(
            kind=DC.KIND_LEAVE if hasattr(DC, "KIND_LEAVE") else DC.KIND_CHOICES[0][0], employee=self.staff,
            leave_application=app, old_start_date=app.start_date, old_end_date=app.end_date,
            new_start_date=app.start_date + timedelta(days=3), new_end_date=app.end_date + timedelta(days=3),
            number_of_days=Decimal("1"), justification="Family event", status=DC.RECOMMENDED_BY_AO)
        change.actions.create(action="recommend", resulting_status=DC.RECOMMENDED_BY_AO, acted_by=self.ao.user)
        self.assert_refused(self.post(self.ao, reverse("datechange:action", args=[change.pk]),
                                      {"action": "approve", "notes": ""}))
        change.refresh_from_db()
        self.assertEqual(change.status, DC.RECOMMENDED_BY_AO)

    def test_official_business_single(self):
        from official_requests.models import OfficialRequest as O

        req = O.objects.create(request_type=O.OFFICIAL_BUSINESS, employee=self.staff, purpose="Seminar",
                               destination="Puerto Princesa", start_date=self.today + timedelta(days=5),
                               end_date=self.today + timedelta(days=5), status=O.RECOMMENDED_BY_AO)
        req.actions.create(action="recommend", resulting_status=O.RECOMMENDED_BY_AO, acted_by=self.ao.user)
        r = self.post(self.ao, reverse("official_requests:request_action", args=[req.pk]),
                      {"action": "recommend_capitol", "notes": ""})
        req.refresh_from_db()
        self.assertEqual(req.status, O.RECOMMENDED_BY_AO)
        self.assert_refused(r)

    def test_ot_group_request(self):
        from official_requests.models import OfficialRequest as O
        from official_requests.models import OfficialRequestBatch as B

        batch = B.objects.create(request_type=O.OT_RESTDAY_HOLIDAY, purpose="Census surge",
                                 filed_by=self.hradmin.user, filer_role="HR_ADMIN", status=O.RECOMMENDED_BY_AO)
        O.objects.create(request_type=O.OT_RESTDAY_HOLIDAY, employee=self.staff, purpose="Census surge",
                         start_date=self.today + timedelta(days=2), end_date=self.today + timedelta(days=2),
                         hours_requested=Decimal("4"), batch=batch, status=O.RECOMMENDED_BY_AO)
        batch.actions.create(action="recommend", resulting_status=O.RECOMMENDED_BY_AO, acted_by=self.ao.user)
        r = self.post(self.ao, reverse("official_requests:batch_action", args=[batch.pk]),
                      {"action": "approve", "notes": ""})
        batch.refresh_from_db()
        self.assertEqual(batch.status, O.RECOMMENDED_BY_AO)
        self.assert_refused(r)

    def test_duty_schedule_refused_then_accepted(self):
        from schedules.models import DutySchedule as S

        nxt = (self.today.replace(day=1) + timedelta(days=40)).replace(day=1)
        sched = S.objects.create(year=nxt.year, month=nxt.month, section=self.lab, prepared_by=self.head.user,
                                 preparer_role=S.FILER_CHOICES[0][0], status=S.RECOMMENDED,
                                 cutoff_date=nxt - timedelta(days=10))
        sched.actions.create(action="submit", resulting_status=S.SUBMITTED, acted_by=self.head.user)
        sched.actions.create(action="recommend", resulting_status=S.RECOMMENDED, acted_by=self.ao.user)
        url = reverse("schedules:action", args=[sched.pk])
        self.assert_refused(self.post(self.ao, url, {"action": "approve", "notes": ""}))
        sched.refresh_from_db()
        self.assertEqual(sched.status, S.RECOMMENDED)
        self.post(self.ao, url, {"action": "approve", "notes": "Coverage complete"})
        sched.refresh_from_db()
        self.assertEqual(sched.status, S.APPROVED)
        self.assertTrue(sched.actions.order_by("-pk").first().notes.startswith(
            f"{OIC_REPEAT_TAG} Approved as OIC for the COH, after recommending it earlier"))
        self.assertTrue(any("OIC, acted earlier" in r["action"] for r in audit_rows(module="Duty Schedule")))

    def test_carry_over_reason(self):
        from cto import carryover
        from cto.models import CTOCarryOverReason

        record = CTOCarryOverReason.objects.create(employee=self.head, year=self.today.year,
                                                   reason="Lab surge", submitted_by=self.ao)
        with self.assertRaisesMessage(Exception, "You acted on this request earlier."):
            carryover.decide_reason(self.ao, record, True, "", self.today)
        record.refresh_from_db()
        self.assertEqual(record.status, CTOCarryOverReason.PENDING)
        carryover.decide_reason(self.ao, record, True, "Justified", self.today)
        record.refresh_from_db()
        self.assertEqual(record.decision_note, f"{OIC_REPEAT_TAG} Approved as OIC for the COH, after submitting "
                                               "it earlier: Justified")
        self.assertTrue(any("OIC, acted earlier" in r["action"] for r in audit_rows(module="CTO Carry-over")))

    def test_own_carry_over_reason_is_refused(self):
        from cto import carryover
        from cto.models import CTOCarryOverReason

        record = CTOCarryOverReason.objects.create(employee=self.ao, year=self.today.year, reason="x",
                                                   submitted_by=self.hradmin)
        with self.assertRaisesMessage(Exception, "your own CTO"):
            carryover.decide_reason(self.ao, record, True, "ok", self.today)


class AOStepRoutes(Base):
    """OIC for the AO who endorsed as Supervisor earlier (section head = Laboratory)."""

    def setUp(self):
        super().setUp()
        self.oic(self.head, R.ADMINISTRATIVE_OFFICER)

    def test_local_official_errand_ao_approves(self):
        from official_requests.models import OfficialRequest as O

        req = O.objects.create(request_type=O.TRAVEL, employee=self.staff, purpose="Bank deposit",
                               destination="Municipal Hall", start_date=self.today + timedelta(days=1),
                               end_date=self.today + timedelta(days=1), status=O.ENDORSED_BY_SUPERVISOR)
        req.actions.create(action="endorse", resulting_status=O.ENDORSED_BY_SUPERVISOR, acted_by=self.head.user)
        r = self.post(self.head, reverse("official_requests:request_action", args=[req.pk]),
                      {"action": "approve", "notes": ""})
        req.refresh_from_db()
        self.assertEqual(req.status, O.ENDORSED_BY_SUPERVISOR)
        self.assert_refused(r)
        self.post(self.head, reverse("official_requests:request_action", args=[req.pk]),
                  {"action": "approve", "notes": "Errand needed today"})
        req.refresh_from_db()
        self.assertEqual(req.status, O.APPROVED)
        self.assertIn("Approved as OIC for the AO, after endorsing it earlier: Errand needed today",
                      req.actions.order_by("-pk").first().notes)

    def test_attendance_correction(self):
        from attendance.models import AttendanceCorrectionRequest as A

        R.objects.create(employee=self.head, role=R.HR_PROCESSOR)  # validated it as HR earlier
        corr = A.objects.create(correction_type=A.FORMAL, employee=self.staff, validator="HR",
                                filed_by=self.staff.user, status=A.VALIDATED)
        corr.actions.create(action="validate", resulting_status=A.VALIDATED, acted_by=self.head.user)
        r = self.post(self.head, reverse("attendance:correction_action", args=[corr.pk]),
                      {"action": "approve", "notes": ""})
        corr.refresh_from_db()
        self.assertEqual(corr.status, A.VALIDATED)
        self.assert_refused(r)

    def test_oic_who_did_not_act_earlier_needs_no_remark(self):
        app = LeaveApplication.objects.create(
            employee=self.staff, leave_type=LeaveType.objects.get(code="COSP_LEAVE"),
            route=LeaveApplication.ROUTE_COSP, status=LeaveApplication.PROCESSED_BY_HR,
            start_date=self.today + timedelta(days=20), end_date=self.today + timedelta(days=20),
            number_of_days=Decimal("1"))
        app.actions.create(action="process", resulting_status=LeaveApplication.PROCESSED_BY_HR, acted_by=self.hr.user)
        self.post(self.head, reverse("leave:leave_action", args=[app.pk]), {"action": "recommend", "notes": ""})
        app.refresh_from_db()
        self.assertEqual(app.status, LeaveApplication.RECOMMENDED_BY_AO)
        self.assertEqual(app.actions.order_by("-pk").first().notes, "")


class OwnRequestEveryKindOfOIC(Base):
    """The OIC never acts on their own request, at the AO or the COH step, whoever they are."""

    def check(self, oic_person):
        for role, actors in ((R.ADMINISTRATIVE_OFFICER, ao_step_actors), (R.CHIEF_OF_HOSPITAL, coh_step_actors)):
            line = self.oic(oic_person, role)
            self.assertIn(oic_person, actors([self.staff]), role)  # acts for others
            self.assertNotIn(oic_person, actors([oic_person]), role)  # never on their own
            line.delete()

    def test_the_ao_as_oic_for_the_coh(self):
        line = self.oic(self.ao, R.CHIEF_OF_HOSPITAL)
        self.assertIn(self.ao, coh_step_actors([self.staff]))
        self.assertNotIn(self.ao, coh_step_actors([self.ao]))
        line.delete()

    def test_the_chief_nurse(self):
        self.check(self.chief_nurse)

    def test_a_section_supervisor(self):
        self.check(self.head)

    def test_a_unit_head(self):
        self.check(self.unit_head)

    def test_reviewed_request_guard_for_each_kind(self):
        """The same reviewed-request rule for every kind of OIC (shared guard)."""
        for who in (self.chief_nurse, self.head, self.unit_head):
            line = self.oic(who, R.CHIEF_OF_HOSPITAL)
            app = LeaveApplication.objects.create(
                employee=self.staff, leave_type=LeaveType.objects.get(code="COSP_LEAVE"),
                route=LeaveApplication.ROUTE_COSP, status=LeaveApplication.RECOMMENDED_BY_AO,
                start_date=self.today + timedelta(days=20), end_date=self.today + timedelta(days=20),
                number_of_days=Decimal("1"))
            app.actions.create(action="endorse", resulting_status="ENDORSED_BY_SUPERVISOR", acted_by=who.user)
            err, _ = oic_repeat_guard(who, app, R.CHIEF_OF_HOSPITAL, "", action="approve")
            self.assertIn("You acted on this request earlier.", err)
            err, notes = oic_repeat_guard(who, app, R.CHIEF_OF_HOSPITAL, "ok", action="approve")
            self.assertIsNone(err)
            self.assertEqual(notes, f"{OIC_REPEAT_TAG} Approved as OIC for the COH, after endorsing it earlier: ok")
            line.delete()


class QueueLabel(Base):
    def test_queues_say_which_oic_role(self):
        self.oic(self.head, R.ADMINISTRATIVE_OFFICER)
        self.client.force_login(self.head.user)
        for name in ("leave:leave_queue", "cto:cto_queue", "exchange:exchange_queue",
                     "attendance:correction_queue", "official_requests:request_queue", "cto:carry_over",
                     "schedules:list"):
            page = self.client.get(reverse(name))
            self.assertContains(page, "This list includes requests waiting for you as", msg_prefix=name)
            self.assertContains(page, "OIC for the AO", msg_prefix=name)

    def test_no_label_without_an_oic(self):
        self.client.force_login(self.ao.user)
        self.assertNotContains(self.client.get(reverse("leave:leave_queue")), "This list includes requests")
