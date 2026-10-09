from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import BDHAuthenticationForm

SESSION_EXPIRED_MESSAGE = "Session expired. Please log in again."

# ?next= targets that must never be used after login: going "next" to the
# login page itself is a redirect loop, /logout/ only accepts POST (a GET
# shows an error page), and /admin/ is Django's staff-only panel.
_BAD_NEXT_PREFIXES = ("/hris/login", "/login", "/logout", "/admin")


def landing_url(user):
    """Where a signed-in person starts: the homepage, for every role
    (Batch 4, owner decision 2026-10-09). The Dashboard stays as the
    analytics page."""
    return reverse("homepage:home")


def home(request):
    """'/' (and /hris/): signed in -> your landing page, otherwise the login page."""
    if request.user.is_authenticated:
        return redirect(landing_url(request.user))
    return redirect("login")


def csrf_failure(request, reason=""):
    """
    Replaces Django's "403 Forbidden - CSRF verification failed" page
    (CSRF_FAILURE_VIEW). That page appeared when someone submitted a login
    form loaded long ago (from browser history, the Back button, or a tab
    left open) whose security token had expired. Now they simply get a
    fresh login page with a short message, or, if they are still signed
    in, are sent back to the page they came from.
    """
    if request.user.is_authenticated:
        messages.warning(request, "This page had expired. Please try again.")
        back = request.META.get("HTTP_REFERER", "")
        if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
            return redirect(back)
        return redirect(landing_url(request.user))
    messages.warning(request, SESSION_EXPIRED_MESSAGE)
    return redirect("login")


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

    def get_redirect_url(self):
        """The ?next= page, unless it would loop or break (see _BAD_NEXT_PREFIXES)."""
        url = super().get_redirect_url()
        if url and url.lower().startswith(_BAD_NEXT_PREFIXES):
            return ""
        return url

    def get_default_redirect_url(self):
        return landing_url(self.request.user)

    def get(self, request, *args, **kwargs):
        # Say why the login page is showing. A leftover session cookie with
        # no signed-in user means the session timed out (a normal logout
        # deletes the cookie); a ?next= alone means an inner-page bookmark.
        if not request.user.is_authenticated:
            if request.COOKIES.get(settings.SESSION_COOKIE_NAME):
                messages.warning(request, SESSION_EXPIRED_MESSAGE)
            elif request.GET.get("next"):
                messages.info(request, "Please log in to continue.")
        return super().get(request, *args, **kwargs)

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
