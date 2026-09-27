from django.urls import path

from . import views

app_name = "cto"

urlpatterns = [
    path("credit/new/", views.credit_entry_create, name="credit_entry_create"),
    path("mine/", views.my_cto, name="my_cto"),
    path("apply/", views.cto_apply, name="cto_apply"),
    path("queue/", views.cto_queue, name="cto_queue"),
    path("<int:pk>/action/", views.cto_action, name="cto_action"),
]
