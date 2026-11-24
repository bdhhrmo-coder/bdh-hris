from django.urls import path

from . import views
from .availability import in_use_required

app_name = "outonpass"

# Every page except Setup waits for the start date (availability.py, 2026-11-24).
urlpatterns = [
    path("", in_use_required(views.my_passes), name="mine"),
    path("new/", in_use_required(views.file_pass), name="new"),
    path("queue/", in_use_required(views.queue), name="queue"),
    path("<int:pk>/", in_use_required(views.detail), name="detail"),
    path("<int:pk>/guard/", in_use_required(views.guard_card), name="guard_card"),
    path("<int:pk>/edit/", in_use_required(views.edit_pass), name="edit"),
    path("<int:pk>/action/", in_use_required(views.act), name="action"),
    path("<int:pk>/tap/", in_use_required(views.tap), name="tap"),
    path("<int:pk>/missed/<str:which>/", in_use_required(views.missed_new), name="missed_new"),
    path("missed/<int:pk>/action/", in_use_required(views.missed_act), name="missed_act"),
    path("gate/", in_use_required(views.gate_page), name="gate"),
    path("<int:pk>/print/", in_use_required(views.print_slip), name="print_slip"),
    path("report/", in_use_required(views.monthly_report), name="report"),
    path("setup/", views.setup, name="setup"),
    path("<int:pk>/hr-close/", in_use_required(views.hr_close), name="hr_close"),
]
