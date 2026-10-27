from django.utils import timezone


def greeting(request):
    """Header greeting and today's date on every logged-in screen (Batch 4)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    now = timezone.localtime()
    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    return {"header_greeting": f"Good {part}", "header_today": now}


def test_system(request):
    """Red TEST SYSTEM bar on every page when settings.TEST_SYSTEM is on (test PC only)."""
    from django.conf import settings

    return {"test_system": getattr(settings, "TEST_SYSTEM", False)}


def page_watermark(request):
    """Faint logo and tagline behind logged-in pages (Batch 10). Display only;
    one setting turns it on or off for every page (the homepage swaps it for
    its own photo in homepage/home.html)."""
    from django.conf import settings

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"page_watermark": getattr(settings, "PAGE_WATERMARK", False),
            "page_watermark_fixed": getattr(settings, "PAGE_WATERMARK_FIXED", False)}
