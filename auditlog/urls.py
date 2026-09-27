from django.urls import path

from . import views

app_name = "auditlog"

urlpatterns = [
    path("", views.audit_log_view, name="audit_log"),
    path("retention/", views.retention_review_list, name="retention_review_list"),
    path("retention/<int:employee_pk>/decide/", views.retention_review_decide, name="retention_review_decide"),
]
