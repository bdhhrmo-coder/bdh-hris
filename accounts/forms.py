from django import forms
from django.contrib.auth.forms import AuthenticationForm


class BDHAuthenticationForm(AuthenticationForm):
    """
    Login form for the redesigned login page (2026-09-28).

    Two deliberate choices beyond Django's default AuthenticationForm:

    - error_messages never distinguishes "no such user" from "wrong
      password" — both collapse to one generic message, so a failed
      attempt can't be used to confirm whether a username/employee ID
      exists in the system.
    - adds `remember_me`, read by accounts.views.BDHLoginView to decide
      the session length. Not a security feature — CLAUDE.md §13 keeps
      2FA and similar login hardening out of scope for now.
    """

    username = forms.CharField(
        label="Username or Employee ID",
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "placeholder": "Enter your username",
                "class": "input-control",
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "placeholder": "Enter your password",
                "class": "input-control",
            }
        ),
    )
    remember_me = forms.BooleanField(required=False, label="Remember me")

    error_messages = {
        "invalid_login": "Invalid username or password.",
        "inactive": "This account has been deactivated. Contact your System Administrator.",
    }
