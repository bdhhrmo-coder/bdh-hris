from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.dashboard_home, name="dashboard_home"),
    path("reports/leave-balances/", views.leave_balance_report, name="leave_balance_report"),
    path("reports/application-summary/", views.application_summary_report, name="application_summary_report"),
]
