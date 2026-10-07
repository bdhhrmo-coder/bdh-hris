from django.urls import path

from . import batch_views, views

app_name = "official_requests"

urlpatterns = [
    path("apply/", views.request_apply, name="request_apply"),
    path("mine/", views.my_requests, name="my_requests"),
    path("queue/", views.request_queue, name="request_queue"),
    path("<int:pk>/action/", views.request_action, name="request_action"),
    path("batch/new/", batch_views.batch_file, name="batch_file"),
    path("batch/<int:pk>/", batch_views.batch_detail, name="batch_detail"),
    path("batch/<int:pk>/edit/", batch_views.batch_edit, name="batch_edit"),
    path("batch/<int:pk>/action/", batch_views.batch_action, name="batch_action"),
]
