from django.urls import path

from . import views

app_name = "homepage"

urlpatterns = [
    path("", views.home, name="home"),
    path("absent-today/", views.absent_list, name="absent_list"),
    path("pending-leave-cto/", views.pending_leave_list, name="pending_leave_list"),
    path("pending-applications/", views.pending_applications_list, name="pending_applications_list"),
]
