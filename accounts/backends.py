"""
Lets staff log in with their Employee ID (e.g. EMP-0123) as well as their
username - the login page says "Username or Employee ID".

Django's normal ModelBackend is tried first (username), then this one. It
only finds the user; the password check and the "inactive accounts can't
log in" rule are Django's own (check_password, user_can_authenticate), so a
wrong ID or password gives the same generic error as a wrong username.
"""

from django.contrib.auth.backends import ModelBackend

from employees.models import Employee


class BDHModelBackend(ModelBackend):
    """Django's normal username login, plus one rule (Batch 3 Item 5): an
    ARCHIVED employee's account can't log in, and an open session ends on
    the next page (Django re-checks this for every request). The account
    itself is not changed, so Undo/Restore gives access back exactly."""

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False
        employee = getattr(user, "employee", None)
        return employee is None or employee.archived_at is None


class EmployeeIDBackend(BDHModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        # Case-insensitive, so "emp-0123" works too. If two IDs differ only
        # by case (shouldn't happen), refuse rather than guess.
        matches = list(
            Employee.objects.select_related("user").filter(employee_id__iexact=username.strip(), user__isnull=False)[:2]
        )
        if len(matches) != 1:
            return None
        user = matches[0].user
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
