from django.urls import path

from . import views

app_name = "outonpass"

urlpatterns = [
    path("", views.my_passes, name="mine"),
    path("new/", views.file_pass, name="new"),
    path("queue/", views.queue, name="queue"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit_pass, name="edit"),
    path("<int:pk>/action/", views.act, name="action"),
]
