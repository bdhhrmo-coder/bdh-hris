from django.utils import timezone


def greeting(request):
    """Header greeting and today's date on every logged-in screen (Batch 4)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    now = timezone.localtime()
    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    return {"header_greeting": f"Good {part}", "header_today": now}
