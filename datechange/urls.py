from django.urls import path

from . import views

app_name = "datechange"

urlpatterns = [
    path("new/<str:kind>/<int:pk>/", views.change_new, name="new"),
    path("<int:pk>/", views.change_detail, name="detail"),
    path("<int:pk>/action/", views.change_action, name="action"),
]
