from django.urls import path

from . import views

app_name = "exchange"

urlpatterns = [
    path("apply/", views.exchange_apply, name="exchange_apply"),
    path("mine/", views.my_exchanges, name="my_exchanges"),
    path("<int:pk>/consent/", views.exchange_consent, name="exchange_consent"),
    path("queue/", views.exchange_queue, name="exchange_queue"),
    path("<int:pk>/action/", views.exchange_action, name="exchange_action"),
]
