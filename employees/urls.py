from django.urls import path

from . import views

app_name = "employees"

urlpatterns = [
    path("", views.employee_list, name="employee_list"),
    path("new/", views.employee_create, name="employee_create"),
    path("me/edit-request/", views.my_profile_edit_request, name="my_profile_edit_request"),
    path("profile-requests/", views.profile_edit_request_queue, name="profile_edit_request_queue"),
    path(
        "profile-requests/<int:pk>/review/",
        views.profile_edit_request_review,
        name="profile_edit_request_review",
    ),
    path("<int:pk>/", views.employee_detail, name="employee_detail"),
    path("<int:pk>/edit/", views.employee_edit, name="employee_edit"),
    path("<int:pk>/archive/", views.employee_archive, name="employee_archive"),
    path("<int:pk>/restore/", views.employee_restore, name="employee_restore"),
]
