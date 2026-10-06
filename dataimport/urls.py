from django.urls import path

from . import views

app_name = "dataimport"

urlpatterns = [
    path("", views.home, name="home"),
    path("template/", views.download_template, name="download_template"),
    path("<str:kind>/upload/", views.upload, name="upload"),
    path("<str:kind>/confirm/", views.confirm, name="confirm"),
    path("slips/<str:fmt>/", views.password_slips, name="password_slips"),
    path("slips-clear/", views.clear_slips, name="clear_slips"),
]
