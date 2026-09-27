from django.urls import path

from . import views

app_name = "employees"

urlpatterns = [
    path("<int:pk>/", views.employee_detail, name="employee_detail"),
    path("<int:pk>/edit/", views.employee_edit, name="employee_edit"),
]
