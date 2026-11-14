from django.urls import path

from . import views

app_name = "outonpass"

urlpatterns = [
    path("", views.my_passes, name="mine"),
    path("new/", views.file_pass, name="new"),
    path("queue/", views.queue, name="queue"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/guard/", views.guard_card, name="guard_card"),
    path("<int:pk>/edit/", views.edit_pass, name="edit"),
    path("<int:pk>/action/", views.act, name="action"),
    path("<int:pk>/tap/", views.tap, name="tap"),
    path("<int:pk>/missed/<str:which>/", views.missed_new, name="missed_new"),
    path("missed/<int:pk>/action/", views.missed_act, name="missed_act"),
    path("gate/", views.gate_page, name="gate"),
    path("<int:pk>/hr-close/", views.hr_close, name="hr_close"),
]
