from django.urls import path

from . import deduction_views, views

app_name = "performance"

urlpatterns = [
    path("setup/", views.setup, name="setup"),
    path("periods/", views.periods, name="periods"),
    path("periods/<int:pk>/", views.period_detail, name="period"),
    path("tasks/", views.tasks, name="tasks"),
    path("rate/<int:pk>/", views.rate, name="rate"),
    path("deduction-criteria/", deduction_views.criteria, name="deduction_criteria"),
]
