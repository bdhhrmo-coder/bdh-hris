"""
URL configuration for bdh_hris project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from django.views.generic import RedirectView

from accounts import oic
from accounts.views import BDHLoginView, BDHPasswordChangeView, home

def favicon(request):
    """Batch 11: browsers also ask for /favicon.ico directly. The icon's address is
    looked up when asked (not when the server starts), so commands such as
    "migrate" work even before "collectstatic" has listed the new icon."""
    from django.shortcuts import redirect
    from django.templatetags.static import static

    return redirect(static("images/favicon.ico"))


urlpatterns = [
    # Batch 2, Item 1: one permanent login link, /hris/login/. "/" , "/hris/"
    # and the old "/login/" all lead there (or, if already signed in, to
    # the person's landing page). The old /login/ keeps any ?next= so
    # existing bookmarks still work.
    path("", home, name="home"),
    path("hris/", home),
    path("hris/login/", BDHLoginView.as_view(), name="login"),
    path("hris/home/", include("homepage.urls")),
    path("login/", RedirectView.as_view(pattern_name="login", permanent=False, query_string=True)),
    path("admin/", admin.site.urls),
    # Batch 11: browsers also ask for /favicon.ico directly; send them to the BDH logo icon.
    path("favicon.ico", favicon),
    # Batch 7: Officer-in-Charge screen (accounts/oic.py)
    path("oic/", oic.oic_home, name="oic_home"),
    path("oic/<int:pk>/end/", oic.oic_end, name="oic_end"),
    path("employees/", include("employees.urls")),
    path("leave/", include("leave.urls")),
    path("cto/", include("cto.urls")),
    path("exchange/", include("exchange.urls")),
    path("attendance/", include("attendance.urls")),
    path("official-requests/", include("official_requests.urls")),
    path("documents/", include("documents.urls")),
    path("notifications/", include("notifications.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("audit-log/", include("auditlog.urls")),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", login_required(BDHPasswordChangeView.as_view()), name="password_change"),
    path("data-import/", include("dataimport.urls")),
    path("announcements/", include("announcements.urls")),
    path("schedules/", include("schedules.urls")),
]
