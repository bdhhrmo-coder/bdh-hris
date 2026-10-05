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
from django.urls import include, path
from django.views.generic import RedirectView

from accounts.views import BDHLoginView

urlpatterns = [
    # BDHLoginView sends already-signed-in users straight on to their home page.
    path("", RedirectView.as_view(pattern_name="login", permanent=False)),
    path("admin/", admin.site.urls),
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
    path("login/", BDHLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
