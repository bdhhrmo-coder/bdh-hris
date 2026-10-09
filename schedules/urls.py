from django.urls import path

from . import views

app_name = "schedules"

urlpatterns = [
    path("", views.schedule_list, name="list"),
    path("mine/", views.my_schedule, name="mine"),
    path("new/", views.schedule_new, name="new"),
    path("shift-codes/", views.shift_codes, name="shift_codes"),
    path("shift-codes/new/", views.shift_code_edit, name="shift_code_new"),
    path("shift-codes/<int:pk>/", views.shift_code_edit, name="shift_code_edit"),
    path("<int:pk>/", views.schedule_detail, name="detail"),
    path("<int:pk>/edit/", views.schedule_edit, name="edit"),
    path("<int:pk>/action/", views.schedule_action, name="action"),
    path("<int:pk>/correct/", views.schedule_correct, name="correct"),
    path("<int:pk>/excel/", views.excel_template, name="excel_template"),
    path("<int:pk>/excel/import/", views.excel_import, name="excel_import"),
    path("<int:pk>/print/", views.schedule_print, name="print"),
]
