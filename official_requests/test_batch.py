"""Group (batch) filing - Batch 2, Item 7 (owner decisions 2026-10-07)."""

from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import RoleAssignment
from employees.models import Employee
from leave.models import LeaveApplication, LeaveType
from notifications.models import Notification
from orgstructure.models import Section

from . import batch as rules
from .models import OfficialRequest as R
from .models import OfficialRequestAction, OfficialRequestBatch as B


def emp(username, section=None, *roles, **ra_extra):
    user = User.objects.create_user(username=username, password="x")
    e = Employee.objects.create(user=user, employee_id=username.upper(), surname=username.title(), first_name="T",
                                date_hired=date(2024, 1, 1), employment_status="REGULAR")
    if section:
        e.sections.add(section)
    for role in roles:
        RoleAssignment.objects.create(employee=e, role=role, section=section if role == "SUPERVISOR" else None,
                                      **ra_extra)
    return e


def post_data(kind, employees, lines, apply_same=True, purpose="Hospital outreach", destination="Brooke's Point",
              **extra):
    data = {"kind": kind, "purpose": purpose, "destination": destination,
            "past_date_reason": "Filed after the duty",  # OT dates here are past (CLAUDE.md §7.1)
            "employees": [str(e.pk) for e in employees],
            "lines-TOTAL_FORMS": str(len(lines)), "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000"}
    if apply_same:
        data["apply_same"] = "on"
    for i, line in enumerate(lines):
        for k, v in line.items():
            data[f"lines-{i}-{k}"] = str(v.pk) if k == "employee" else v
    data.update(extra)
    return data


class BatchTests(TestCase):
    def setUp(self):
        self.lab = Section.objects.get(name="Laboratory Section")
        self.er = Section.objects.get(name="Pharmacy Section")
        self.sup = emp("sup", self.lab, "SUPERVISOR")
        self.a = emp("staffa", self.lab)
        self.b = emp("staffb", self.lab)
        self.outsider = emp("outsider", self.er)
        self.proc = emp("proc", None, "HR_PROCESSOR")
        self.hradmin = emp("hradmin", None, "HR_ADMINISTRATOR")
        self.ao = emp("ao", None, "ADMINISTRATIVE_OFFICER")
        self.coh = emp("coh", None, "CHIEF_OF_HOSPITAL")

    def file(self, who, data, url=None):
        self.client.force_login(who.user)
        return self.client.post(url or reverse("official_requests:batch_file"), data)

    def act(self, who, batch, action, notes=""):
        self.client.force_login(who.user)
        return self.client.post(reverse("official_requests:batch_action", args=[batch.pk]),
                                {"action": action, "notes": notes})

    def ot_lines(self):
        return [{"date": "2026-09-05", "time_from": "17:00", "time_to": "21:00"},
                {"date": "2026-09-06", "time_from": "20:00", "time_to": "02:00", "note": "night"}]

    # -- filing --------------------------------------------------------------

    def test_supervisor_files_one_batch_with_a_line_per_employee_date(self):
        self.file(self.sup, post_data("OT", [self.a, self.b], self.ot_lines()))
        batch = B.objects.get()
        self.assertEqual((batch.filer_role, batch.status, batch.type_label), ("SUPERVISOR", "SUBMITTED", "Authorized overtime"))
        lines = list(batch.active_lines())
        self.assertEqual(len(lines), 4)
        night = R.objects.get(employee=self.a, start_date=date(2026, 9, 6))
        self.assertEqual((night.hours_requested, night.line_note, night.request_type),
                         (Decimal("6.00"), "night", R.OT_RESTDAY_HOLIDAY))

    def test_rest_day_and_holiday_set_the_flag(self):
        self.file(self.sup, post_data("HOLIDAY", [self.a], self.ot_lines()[:1]))
        self.assertTrue(R.objects.get().is_restday_or_holiday)

    def test_per_employee_schedule_when_not_applying_same(self):
        lines = [{"date": "2026-09-05", "time_from": "17:00", "time_to": "19:00", "employee": self.a},
                 {"date": "2026-09-07", "time_from": "17:00", "time_to": "20:00", "employee": self.b}]
        self.file(self.sup, post_data("OT", [self.a, self.b], lines, apply_same=False))
        self.assertEqual(sorted((r.employee_id, r.start_date.day) for r in R.objects.all()),
                         sorted([(self.a.pk, 5), (self.b.pk, 7)]))

    def test_supervisor_only_for_own_section(self):
        r = self.file(self.sup, post_data("TRAVEL", [self.a, self.outsider], [{"date": "2026-10-20"}]))
        self.assertFalse(B.objects.exists())
        self.assertEqual(r.status_code, 200)

    def test_oic_supervisor_may_file_for_the_covered_section(self):
        oic = emp("oic", self.er)
        RoleAssignment.objects.create(employee=oic, role="SUPERVISOR", section=self.lab, is_oic=True,
                                      delegated_by=self.sup)
        self.file(oic, post_data("TRAVEL", [self.a], [{"date": "2026-10-20"}]))
        self.assertEqual(B.objects.get().filer_role, "SUPERVISOR")

    def test_hr_processor_files_ot_travel_and_ob(self):
        # D5 (2026-11-02): the HR Processor may now file OT as well (was Travel and OB only).
        self.file(self.proc, post_data("OT", [self.a], self.ot_lines()[:1]))
        self.assertEqual(B.objects.get().filer_role, "HR_PROCESSOR")
        self.file(self.proc, post_data("TRAVEL", [self.a, self.outsider], [{"date": "2026-10-20"}]))
        self.assertEqual(B.objects.latest("pk").filer_role, "HR_PROCESSOR")

    def test_filer_cannot_include_themselves(self):
        r = self.file(self.sup, post_data("OT", [self.a, self.sup], self.ot_lines()[:1]))
        self.assertContains(r, "include yourself")
        self.assertFalse(B.objects.exists())

    def test_plain_employee_cannot_file_a_batch(self):
        self.client.force_login(self.a.user)
        self.assertEqual(self.client.get(reverse("official_requests:batch_file")).status_code, 403)

    def test_ot_needs_times_and_travel_needs_destination(self):
        self.file(self.sup, post_data("OT", [self.a], [{"date": "2026-09-05"}]))
        self.file(self.sup, post_data("TRAVEL", [self.a], [{"date": "2026-10-20"}], destination=""))
        self.assertFalse(B.objects.exists())

    def test_conflicts_are_flagged_and_can_be_removed(self):
        LeaveApplication.objects.create(employee=self.a, leave_type=LeaveType.objects.get(code="VL"),
                                        start_date=date(2026, 10, 20), end_date=date(2026, 10, 20),
                                        number_of_days=1, status="RECORDED")
        R.objects.create(employee=self.b, request_type=R.OFFICIAL_BUSINESS, purpose="x",
                         start_date=date(2026, 10, 21), end_date=date(2026, 10, 21))
        data = post_data("TRAVEL", [self.a, self.b], [{"date": "2026-10-20"}, {"date": "2026-10-21"}])
        r = self.file(self.sup, data)
        self.assertContains(r, "Conflicts found (2)")
        self.assertContains(r, "On approved leave")
        self.assertContains(r, "Already has a pending Official Business request")
        self.assertFalse(B.objects.exists())
        data["skip"] = [f"{self.a.pk}|2026-10-20", f"{self.b.pk}|2026-10-21"]
        self.file(self.sup, data)
        batch = B.objects.get()
        self.assertEqual(sorted((l.employee_id, l.start_date.day) for l in batch.active_lines()),
                         sorted([(self.a.pk, 21), (self.b.pk, 20)]))

    def test_same_employee_date_twice_in_the_form_is_an_error(self):
        lines = [{"date": "2026-09-05", "time_from": "17:00", "time_to": "19:00", "employee": self.a},
                 {"date": "2026-09-05", "time_from": "20:00", "time_to": "21:00", "employee": self.a}]
        r = self.file(self.sup, post_data("OT", [self.a], lines, apply_same=False))
        self.assertContains(r, "Listed more than once")

    # -- routing ---------------------------------------------------------------

    def run_route(self, filer, kind, employees, expected_steps, day="2026-10-20"):
        before = B.objects.count()
        self.file(filer, post_data(kind, employees, [{"date": day, "time_from": "17:00", "time_to": "20:00"}]))
        self.assertEqual(B.objects.count(), before + 1)
        batch = B.objects.latest("pk")
        actors = {"endorse": self.sup, "process": self.proc, "recommend": self.ao, "approve": self.coh}
        done = []
        while rules.next_status(batch):
            code = rules.STEP_ACTIONS[rules.next_status(batch)][0]
            self.assertEqual(self.act(actors[code], batch, code).status_code, 302, code)
            batch.refresh_from_db()
            done.append(code)
        self.assertEqual(done, expected_steps)
        self.assertEqual(batch.status, R.APPROVED)
        self.assertFalse(batch.lines.exclude(status=R.APPROVED).exists())
        return batch

    def test_routes_follow_the_owner_table(self):
        self.run_route(self.sup, "OT", [self.a], ["process", "recommend", "approve"], "2026-10-01")
        self.run_route(self.hradmin, "REST_DAY", [self.a], ["recommend", "approve"], "2026-10-02")
        self.run_route(self.sup, "TRAVEL", [self.a], ["recommend", "approve"], "2026-10-03")
        self.run_route(self.proc, "TRAVEL", [self.a], ["recommend", "approve"], "2026-10-04")
        self.run_route(self.sup, "OFFICIAL_BUSINESS", [self.a], ["process", "recommend", "approve"], "2026-10-05")
        self.run_route(self.proc, "OFFICIAL_BUSINESS", [self.a, self.b], ["endorse", "recommend", "approve"], "2026-10-06")
        self.run_route(self.proc, "OT", [self.a], ["recommend", "approve"], "2026-10-07")  # D5
        self.run_route(self.ao, "HOLIDAY", [self.a], ["approve"], "2026-10-08")  # D6: AO-filed -> COH

    def test_ob_by_hr_needs_a_shared_supervisor(self):
        r = self.file(self.proc, post_data("OFFICIAL_BUSINESS", [self.a, self.outsider], [{"date": "2026-10-20"}]))
        self.assertContains(r, "don&#x27;t share one Supervisor")
        self.assertFalse(B.objects.exists())

    def test_wrong_person_or_wrong_step_is_refused(self):
        self.file(self.sup, post_data("OT", [self.a], self.ot_lines()[:1]))
        batch = B.objects.get()
        self.assertEqual(self.act(self.ao, batch, "recommend").status_code, 403)  # HR step first
        self.assertEqual(self.act(self.coh, batch, "approve").status_code, 403)

    def test_filer_cannot_act_on_own_batch_even_with_other_roles(self):
        # 8b: the AO cannot file OB, so OB goes under the next role, HR Processor:
        # route Supervisor -> AO -> COH, and the filer may act at neither step.
        boss = emp("boss", self.lab, "SUPERVISOR", "HR_PROCESSOR")
        RoleAssignment.objects.create(employee=boss, role="ADMINISTRATIVE_OFFICER")
        self.file(boss, post_data("OFFICIAL_BUSINESS", [self.a], [{"date": "2026-10-20"}]))
        batch = B.objects.get()
        self.assertEqual(batch.filer_role, "HR_PROCESSOR")
        self.assertEqual(self.act(boss, batch, "endorse").status_code, 403)
        self.act(self.sup, batch, "endorse")
        batch.refresh_from_db()
        self.assertEqual(self.act(boss, batch, "recommend").status_code, 403)
        self.client.force_login(boss.user)
        self.assertNotContains(self.client.get(reverse("official_requests:request_queue")), f"#{batch.pk}<")

    def test_batch_lines_are_not_in_the_individual_queue(self):
        self.file(self.sup, post_data("OT", [self.a], self.ot_lines()[:1]))
        line = R.objects.get()
        self.client.force_login(self.proc.user)
        self.assertEqual(self.client.post(reverse("official_requests:request_action", args=[line.pk]),
                                          {"action": "process"}).status_code, 403)

    # -- return / resubmit --------------------------------------------------------

    def test_return_needs_remark_then_filer_edits_and_resubmits_same_batch(self):
        self.file(self.sup, post_data("OT", [self.a, self.b], self.ot_lines()))
        batch = B.objects.get()
        self.act(self.proc, batch, "return", notes="")
        batch.refresh_from_db()
        self.assertEqual(batch.status, R.SUBMITTED)  # no remark, no return
        self.act(self.proc, batch, "process")
        self.act(self.ao, batch, "return", notes="Remove Staffb on Sep 6")
        batch.refresh_from_db()
        self.assertEqual(batch.status, R.RETURNED)
        self.assertIn("Remove Staffb on Sep 6", Notification.objects.filter(recipient=self.sup).latest("pk").message)

        self.client.force_login(self.sup.user)
        page = self.client.get(reverse("official_requests:batch_edit", args=[batch.pk]))
        self.assertContains(page, "Remove Staffb on Sep 6")
        lines = [{"date": "2026-09-05", "time_from": "17:00", "time_to": "21:00", "employee": self.a},
                 {"date": "2026-09-06", "time_from": "20:00", "time_to": "02:00", "employee": self.a},
                 {"date": "2026-09-05", "time_from": "17:00", "time_to": "21:00", "employee": self.b}]
        self.file(self.sup, post_data("OT", [self.a, self.b], lines, apply_same=False),
                  url=reverse("official_requests:batch_edit", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(B.objects.count(), 1)
        self.assertEqual(batch.status, R.SUBMITTED)  # starts again at the first step (HR)
        self.assertEqual(batch.active_lines().count(), 3)
        removed = R.objects.get(employee=self.b, start_date=date(2026, 9, 6))
        self.assertEqual(removed.status, R.CANCELLED)
        self.assertEqual(self.act(self.ao, batch, "recommend").status_code, 403)
        self.assertEqual(list(batch.actions.values_list("action", flat=True)),
                         ["submit", "process", "return", "resubmit"])

    def test_filer_whose_role_changed_cannot_edit_a_returned_batch(self):
        # Security review 2026-11-02: the saved filer_role decides the route, so a returned batch is
        # edited only while the filer would still file it under that role (else: file a new one).
        self.file(self.hradmin, post_data("OT", [self.a], self.ot_lines()[:1]))  # route AO -> COH
        batch = B.objects.get()
        self.act(self.ao, batch, "return", notes="Fix it")
        edit_url = reverse("official_requests:batch_edit", args=[batch.pk])
        RoleAssignment.objects.filter(employee=self.hradmin).delete()
        RoleAssignment.objects.create(employee=self.hradmin, role="SUPERVISOR", section=self.lab)  # now a Supervisor
        self.client.force_login(self.hradmin.user)
        self.assertEqual(self.client.get(edit_url).status_code, 403)
        self.assertEqual(self.client.post(edit_url, post_data("OT", [self.a], self.ot_lines()[:1])).status_code, 403)
        RoleAssignment.objects.filter(employee=self.hradmin).delete()  # no filing role at all
        self.assertEqual(self.client.get(edit_url).status_code, 403)
        batch.refresh_from_db()
        self.assertEqual(batch.status, R.RETURNED)
        RoleAssignment.objects.create(employee=self.hradmin, role="HR_ADMINISTRATOR")  # same role again: allowed
        self.assertEqual(self.client.get(edit_url).status_code, 200)

    def test_only_the_filer_edits_and_only_when_returned(self):
        self.file(self.sup, post_data("TRAVEL", [self.a], [{"date": "2026-10-20"}]))
        batch = B.objects.get()
        self.client.force_login(self.proc.user)
        self.assertEqual(self.client.get(reverse("official_requests:batch_edit", args=[batch.pk])).status_code, 403)
        self.client.force_login(self.sup.user)
        self.assertEqual(self.client.get(reverse("official_requests:batch_edit", args=[batch.pk])).status_code, 302)

    # -- audit, notifications, CTO ------------------------------------------------

    def test_audit_trail_records_on_behalf_and_skipped_step_on_each_line(self):
        self.file(self.hradmin, post_data("OT", [self.a, self.b], self.ot_lines()[:1]))
        note = OfficialRequestAction.objects.filter(request__employee=self.a, action="submit").get().notes
        self.assertIn("by HR Administrator", note)
        self.assertIn("Skipped step: Supervisor endorsement and HR processing (filed on behalf by HR Administrator)", note)

    def test_employees_are_notified_when_filed_and_on_final_decision(self):
        self.file(self.sup, post_data("TRAVEL", [self.a, self.b], [{"date": "2026-10-20"}]))
        self.assertTrue(Notification.objects.filter(recipient=self.a, message__contains="filed a Travel").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.ao, message__contains="awaiting your action").exists())
        batch = B.objects.get()
        self.act(self.ao, batch, "recommend")
        self.act(self.coh, batch, "approve")
        for e in (self.a, self.b):
            self.assertTrue(Notification.objects.filter(recipient=e, message__contains="was approved").exists())

    def test_approved_ot_batch_creates_no_cto_and_lines_are_claimable(self):
        from cto.claims import claimable_ot_lines
        from cto.models import CTOClaimLine

        self.run_route(self.sup, "OT", [self.a], ["process", "recommend", "approve"])
        self.assertFalse(CTOClaimLine.objects.exists())  # approval alone credits nothing
        self.assertEqual([r.pk for r in claimable_ot_lines(self.a)], [R.objects.get(employee=self.a).pk])

    def test_employee_sees_own_lines_and_can_open_the_batch(self):
        self.file(self.sup, post_data("TRAVEL", [self.a], [{"date": "2026-10-20"}]))
        batch = B.objects.get()
        self.client.force_login(self.a.user)
        self.assertContains(self.client.get(reverse("official_requests:my_requests")), f"Group request #{batch.pk}")
        self.assertEqual(self.client.get(reverse("official_requests:batch_detail", args=[batch.pk])).status_code, 200)
        self.client.force_login(self.outsider.user)
        self.assertEqual(self.client.get(reverse("official_requests:batch_detail", args=[batch.pk])).status_code, 403)

    def test_file_for_a_group_link_sits_under_my_requests(self):
        self.client.force_login(self.sup.user)
        html = self.client.get(reverse("official_requests:my_requests")).content.decode()
        mine, link, approvals = html.index("My Requests"), html.index("File for a Group"), html.index("Approvals")
        self.assertTrue(mine < link < approvals)
        self.client.force_login(self.a.user)  # plain employee: no link
        self.assertNotIn("File for a Group", self.client.get(reverse("official_requests:my_requests")).content.decode())


class OtFilingTests(TestCase):
    """OT on main's batch module, CLAUDE.md §7.1 (decisions D4-D6, 8a, 8b, 8e, 2026-11-02)."""

    def setUp(self):
        from orgstructure.models import Unit

        self.lab = Section.objects.get(name="Laboratory Section")
        self.pharm = Section.objects.get(name="Pharmacy Section")
        self.nursing = Section.objects.get(name="Nursing Service Section")
        self.er = Unit.objects.get(section=self.nursing, name="ER")
        self.sup = emp("sup", self.lab, "SUPERVISOR")
        self.a = emp("staffa", self.lab)
        self.outsider = emp("outsider", self.pharm)
        self.proc = emp("proc", None, "HR_PROCESSOR")
        self.proc2 = emp("proc2", None, "HR_PROCESSOR")
        self.hradmin = emp("hradmin", None, "HR_ADMINISTRATOR")
        self.ao = emp("ao", None, "ADMINISTRATIVE_OFFICER")
        self.coh = emp("coh", None, "CHIEF_OF_HOSPITAL")
        self.chief_nurse = emp("chiefnurse", self.nursing, "SUPERVISOR")
        self.er_head = emp("erhead", self.nursing)
        self.er_head.units.add(self.er)
        RoleAssignment.objects.create(employee=self.er_head, role="SUPERVISOR", unit=self.er)
        self.nurse = emp("nurse", self.nursing)
        self.nurse.units.add(self.er)

    def file(self, who, data):
        self.client.force_login(who.user)
        return self.client.post(reverse("official_requests:batch_file"), data)

    def act(self, who, batch, action, notes=""):
        self.client.force_login(who.user)
        return self.client.post(reverse("official_requests:batch_action", args=[batch.pk]),
                                {"action": action, "notes": notes})

    def ot(self, who, employees, day="2026-10-20", kind="OT", **extra):
        return self.file(who, post_data(kind, employees, [{"date": day, "time_from": "17:00", "time_to": "20:00"}],
                                        **extra))

    def test_filer_role_priority_ao_hr_admin_hr_processor_supervisor(self):
        # 8b: the highest role that allows the type.
        everyone = emp("everyone", self.lab, "SUPERVISOR", "HR_PROCESSOR", "HR_ADMINISTRATOR",
                       "ADMINISTRATIVE_OFFICER")
        self.assertEqual(rules.filer_roles(everyone), ["AO", "HR_ADMINISTRATOR", "HR_PROCESSOR", "SUPERVISOR"])
        self.assertEqual(rules.filing_role_for(everyone, "OT"), "AO")
        self.assertEqual(rules.filing_role_for(everyone, "TRAVEL"), "HR_ADMINISTRATOR")  # AO files OT only
        RoleAssignment.objects.filter(employee=everyone, role="HR_ADMINISTRATOR").delete()
        self.assertEqual(rules.filing_role_for(everyone, "TRAVEL"), "HR_PROCESSOR")
        RoleAssignment.objects.filter(employee=everyone, role="ADMINISTRATIVE_OFFICER").delete()
        self.assertEqual(rules.filing_role_for(everyone, "OT"), "HR_PROCESSOR")
        RoleAssignment.objects.filter(employee=everyone, role="HR_PROCESSOR").delete()
        self.assertEqual(rules.filing_role_for(everyone, "OT"), "SUPERVISOR")

    def test_ot_filers_hr_processor_ao_and_supervisor_for_any_employee(self):
        # §7.1: a filer may add anyone except the COH and themselves; for Travel the Supervisor keeps own staff.
        for who, day in ((self.proc, "2026-10-20"), (self.ao, "2026-10-21"), (self.sup, "2026-10-22")):
            self.ot(who, [self.a, self.outsider], day)
        self.assertEqual(list(B.objects.order_by("pk").values_list("filer_role", flat=True)),
                         ["HR_PROCESSOR", "AO", "SUPERVISOR"])
        self.assertTrue(all(b.active_lines().count() == 2 for b in B.objects.all()))
        self.file(self.sup, post_data("TRAVEL", [self.outsider], [{"date": "2026-10-23"}]))
        self.assertEqual(B.objects.count(), 3)

    def test_employee_cannot_file_ot(self):
        # D5: no group filing for a plain employee; 8a: no OT on the single form either...
        self.client.force_login(self.a.user)
        self.assertEqual(self.client.get(reverse("official_requests:batch_file")).status_code, 403)
        self.client.post(reverse("official_requests:request_apply"), {
            "request_type": R.OT_RESTDAY_HOLIDAY, "start_date": "2026-10-20", "end_date": "2026-10-20",
            "purpose": "Coverage", "hours_requested": "3"})
        self.assertFalse(R.objects.exists())
        self.assertNotContains(self.client.get(reverse("official_requests:request_apply")), R.OT_RESTDAY_HOLIDAY)

    def test_coh_files_own_ot_on_the_single_form_and_hr_records_it(self):
        # ...except the COH's own OT, which HR records (approval is outside BDH).
        self.client.force_login(self.coh.user)
        self.client.post(reverse("official_requests:request_apply"), {
            "request_type": R.OT_RESTDAY_HOLIDAY, "start_date": "2026-10-20", "end_date": "2026-10-20",
            "purpose": "Coverage", "hours_requested": "3"})
        self.assertTrue(R.objects.get(employee=self.coh).recorded_only)

    def test_coh_cannot_be_added_to_an_ot_batch(self):
        r = self.ot(self.hradmin, [self.a, self.coh])
        self.assertContains(r, "Leave out the Chief of Hospital")
        self.assertFalse(B.objects.exists())

    def test_past_dated_ot_needs_a_typed_reason(self):
        from django.utils import timezone

        today = timezone.localdate()
        past = (today - timedelta(days=1)).isoformat()
        r = self.ot(self.sup, [self.a], past, past_date_reason="")  # override the helper's default
        self.assertContains(r, "type why this is filed late")
        self.assertFalse(B.objects.exists())
        self.ot(self.sup, [self.a], today.isoformat(), past_date_reason="")  # today: no reason needed
        self.assertEqual(B.objects.get().past_date_reason, "")
        self.ot(self.sup, [self.a], past, past_date_reason="Emergency, filed next morning")
        self.assertEqual(B.objects.latest("pk").past_date_reason, "Emergency, filed next morning")

    def test_ot_after_last_day_of_service_is_blocked(self):
        self.a.separation_status = Employee.SEPARATION_RESIGNED
        self.a.last_day_of_service = date(2026, 10, 20)
        self.a.save()
        r = self.ot(self.hradmin, [self.a], "2026-10-21")
        self.assertContains(r, "After the last day of service")
        self.assertFalse(B.objects.exists())
        self.ot(self.hradmin, [self.a], "2026-10-20")  # the last day itself is fine
        self.assertTrue(B.objects.exists())

    def test_ot_batch_with_twelve_employees_is_accepted(self):
        people = [emp(f"crew{i}", self.lab) for i in range(12)]
        self.ot(self.hradmin, people)
        self.assertEqual(B.objects.get().active_lines().count(), 12)

    def test_employees_supervisor_gets_info_notice_when_someone_else_files(self):
        # D5: covering Supervisors are told, information only; the filer is not.
        self.ot(self.hradmin, [self.a, self.nurse])
        info = Notification.objects.filter(message__startswith="For your information")
        self.assertEqual({n.recipient for n in info}, {self.sup, self.er_head, self.chief_nurse})
        Notification.objects.all().delete()
        self.ot(self.sup, [self.a], "2026-10-21")  # filed by the employee's own Supervisor
        self.assertFalse(Notification.objects.filter(message__startswith="For your information").exists())

    def test_unit_head_ot_goes_chief_nurse_hr_ao_coh(self):
        # D6, with an OIC covering the Chief Nurse: the absent Chief Nurse is set aside.
        oic = emp("oicnurse", self.nursing)
        RoleAssignment.objects.create(employee=oic, role="SUPERVISOR", section=self.nursing, is_oic=True,
                                      delegated_by=self.chief_nurse, start_date=date(2026, 1, 1),
                                      end_date=date(2099, 12, 31))
        self.ot(self.er_head, [self.nurse])
        batch = B.objects.get()
        self.assertEqual(batch.filer_role, "UNIT_HEAD")
        self.assertEqual(self.act(self.chief_nurse, batch, "endorse").status_code, 403)
        for who, code in ((oic, "endorse"), (self.proc, "process"), (self.ao, "recommend"), (self.coh, "approve")):
            self.assertEqual(self.act(who, batch, code).status_code, 302, code)
        batch.refresh_from_db()
        self.assertEqual(batch.status, R.APPROVED)

    def test_unit_head_ot_including_the_chief_nurse_goes_to_the_ao(self):
        # 8i: the Chief Nurse is on the request, so the Supervisor step goes to the next level up, the AO.
        self.ot(self.er_head, [self.nurse, self.chief_nurse])
        batch = B.objects.get()
        self.assertEqual(batch.filer_role, "UNIT_HEAD")
        self.assertEqual(self.act(self.chief_nurse, batch, "endorse").status_code, 403)
        self.assertTrue(Notification.objects.filter(recipient=self.ao, message__contains="awaiting your action").exists())
        self.assertEqual(self.act(self.ao, batch, "endorse").status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.status, R.ENDORSED_BY_SUPERVISOR)

    def test_ot_batch_with_no_coh_waits(self):
        RoleAssignment.objects.filter(role="CHIEF_OF_HOSPITAL").delete()
        self.ot(self.hradmin, [self.a])
        batch = B.objects.get()
        self.act(self.ao, batch, "recommend")
        batch.refresh_from_db()
        self.assertEqual(batch.status, R.RECOMMENDED_BY_AO)  # never skipped: waits for a COH
        self.assertEqual(rules.batches_awaiting(self.ao, self.ao.user), [])

    def test_no_ao_or_ao_on_a_line_coh_takes_the_ao_step(self):
        self.ot(self.hradmin, [self.a, self.ao])  # the AO is on a line
        batch = B.objects.get()
        self.assertEqual(self.act(self.ao, batch, "recommend").status_code, 403)
        self.assertEqual(self.act(self.coh, batch, "recommend").status_code, 302)
        RoleAssignment.objects.filter(role="ADMINISTRATIVE_OFFICER").delete()  # no AO at all
        self.ot(self.hradmin, [self.a], "2026-10-21")
        self.assertEqual(self.act(self.coh, B.objects.latest("pk"), "recommend").status_code, 302)

    def test_person_on_a_line_never_acts_another_hr_does(self):
        self.ot(self.sup, [self.a, self.proc])
        batch = B.objects.get()
        self.assertEqual(self.act(self.proc, batch, "process").status_code, 403)
        self.assertEqual(self.act(self.proc2, batch, "process").status_code, 302)

    def test_ot_duplicate_guard_blocks_pending_approved_and_returned(self):
        # 8e: a returned batch still blocks the same employee-date in another batch.
        self.ot(self.hradmin, [self.a], "2026-10-20")
        pending = B.objects.get()
        r = self.ot(self.proc, [self.a], "2026-10-20")
        self.assertContains(r, "Already has a pending")
        self.act(self.ao, pending, "return", notes="Fix the hours")
        r = self.ot(self.proc, [self.a], "2026-10-20")
        self.assertContains(r, "Already has a pending")  # returned still blocks
        self.ot(self.hradmin, [self.a], "2026-10-21")
        approved = B.objects.latest("pk")
        self.act(self.ao, approved, "recommend")
        self.act(self.coh, approved, "approve")
        r = self.ot(self.proc, [self.a], "2026-10-21")
        self.assertContains(r, "Already has a approved")
        self.assertEqual(B.objects.count(), 2)
        # the returned batch's own lines are no conflict when it is resubmitted
        self.client.force_login(self.hradmin.user)
        self.client.post(reverse("official_requests:batch_edit", args=[pending.pk]),
                         post_data("OT", [self.a], [{"date": "2026-10-20", "time_from": "17:00", "time_to": "21:00"}]))
        pending.refresh_from_db()
        self.assertEqual(pending.status, R.SUBMITTED)
