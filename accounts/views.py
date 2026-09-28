from django.contrib.auth.views import LoginView

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
