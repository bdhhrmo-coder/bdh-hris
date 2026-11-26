from django.urls import path

from . import deduction_views, monitoring_views, plan_views, result_views, views

app_name = "performance"

urlpatterns = [
    path("setup/", views.setup, name="setup"),
    path("periods/", views.periods, name="periods"),
    path("periods/<int:pk>/", views.period_detail, name="period"),
    path("tasks/", views.tasks, name="tasks"),
    path("rate/<int:pk>/", views.rate, name="rate"),
    path("deduction-criteria/", deduction_views.criteria, name="deduction_criteria"),
    path("results/", result_views.my_results, name="my_results"),
    path("results/<int:pk>/", result_views.result_detail, name="result"),
    path("cosp-form/<int:pk>/", result_views.cosp_form, name="cosp_form"),
    path("reviews/", result_views.reviews, name="reviews"),
    path("plans/", plan_views.plans, name="plans"),
    path("plans/<int:pk>/", plan_views.plan, name="plan"),
    path("incidents/", deduction_views.incidents, name="incidents"),
    path("incidents/register/", deduction_views.register, name="register"),
    path("incidents/<int:pk>/", deduction_views.incident, name="incident"),
    path("monitoring/", monitoring_views.monitoring, name="monitoring"),
    path("monitoring/rr-feed/", monitoring_views.rr_feed, name="rr_feed"),
    path("report/", monitoring_views.report, name="report"),
    path("report/renewal/", monitoring_views.renewal, name="renewal"),
]
