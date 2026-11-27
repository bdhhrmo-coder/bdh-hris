from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # Part A (2026-11-27): note on each new action row whether its actor
        # acted as OIC (display only; see accounts/processors.py).
        from . import processors

        processors.connect()
