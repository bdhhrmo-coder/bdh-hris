from django.urls import path

from . import views

app_name = "leave"

urlpatterns = [
    path("apply/", views.leave_apply, name="leave_apply"),
    path("mine/", views.my_applications, name="my_applications"),
    path("queue/", views.leave_queue, name="leave_queue"),
    path("<int:pk>/action/", views.leave_action, name="leave_action"),
    path("<int:pk>/print/csc-form-6/", views.print_csc_form6, name="print_csc_form6"),
    path("<int:pk>/print/cosp-leave-form/", views.print_cosp_leave_form, name="print_cosp_leave_form"),
]
