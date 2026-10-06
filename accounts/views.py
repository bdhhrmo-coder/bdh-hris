from django.contrib import messages
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.urls import reverse_lazy

from .forms import BDHAuthenticationForm


class BDHLoginView(LoginView):
    """
    Drop-in replacement for the default auth LoginView (still used for
    /login/ in bdh_hris/urls.py) — adds the "Remember me" behavior the
    redesigned login page (2026-09-28) needs. Everything else (session
    auth, CSRF, redirect handling) is unchanged Django.
    """

    form_class = BDHAuthenticationForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    REMEMBER_ME_SECONDS = 60 * 60 * 24 * 14  # 2 weeks

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(self.REMEMBER_ME_SECONDS)
        else:
            self.request.session.set_expiry(0)  # expires when the browser closes
        return response


class BDHPasswordChangeView(PasswordChangeView):
    """Change password (any logged-in user). Also the page a person is sent
    to when their account has a temporary password (Employee.
    must_change_password, set by the employee import) - saving a new
    password here clears that flag."""

    template_name = "registration/password_change.html"
    success_url = reverse_lazy("notifications:notification_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        employee = getattr(self.request.user, "employee", None)
        if employee is not None and employee.must_change_password:
            employee.must_change_password = False
            employee.save(update_fields=["must_change_password", "updated_at"])
        messages.success(self.request, "Your password has been changed.")
        return response
