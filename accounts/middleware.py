"""
Sends anyone whose account has a temporary password (Employee.
must_change_password) to the Change Password page until they set their
own. Only the pages needed to do that, or to log out, stay reachable.
"""

from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            employee = getattr(user, "employee", None)
            if employee is not None and employee.must_change_password:
                allowed = (reverse("password_change"), reverse("logout"))
                if not request.path.startswith(allowed) and not request.path.startswith("/static/"):
                    return redirect("password_change")
        return self.get_response(request)
