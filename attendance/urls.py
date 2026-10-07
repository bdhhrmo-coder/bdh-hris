from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    path("import/", views.biometric_import, name="biometric_import"),
    path("mine/", views.my_attendance, name="my_attendance"),
    path("correction/formal/apply/", views.formal_correction_apply, name="formal_correction_apply"),
    path("correction/minor/new/", views.minor_correction_create, name="minor_correction_create"),
    path("correction/queue/", views.correction_queue, name="correction_queue"),
    path("correction/<int:pk>/", views.correction_detail, name="correction_detail"),
    path("correction/<int:pk>/edit/", views.correction_edit, name="correction_edit"),
    path("correction/<int:pk>/action/", views.correction_action, name="correction_action"),
    path("correction/<int:pk>/print/", views.print_correction_form, name="print_correction_form"),
]
