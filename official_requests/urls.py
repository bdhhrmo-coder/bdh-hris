from django.urls import path

from . import views

app_name = "official_requests"

urlpatterns = [
    path("apply/", views.request_apply, name="request_apply"),
    path("mine/", views.my_requests, name="my_requests"),
    path("queue/", views.request_queue, name="request_queue"),
    path("<int:pk>/action/", views.request_action, name="request_action"),
]
