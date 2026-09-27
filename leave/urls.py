from django.urls import path

from . import views

app_name = "leave"

urlpatterns = [
    path("apply/", views.leave_apply, name="leave_apply"),
    path("mine/", views.my_applications, name="my_applications"),
    path("queue/", views.leave_queue, name="leave_queue"),
    path("<int:pk>/action/", views.leave_action, name="leave_action"),
]
