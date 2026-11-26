from django.urls import path

from . import settings_views, views

app_name = "exchange"

urlpatterns = [
    path("apply/", views.exchange_apply, name="exchange_apply"),
    path("mine/", views.my_exchanges, name="my_exchanges"),
    path("<int:pk>/consent/", views.exchange_consent, name="exchange_consent"),
    path("queue/", views.exchange_queue, name="exchange_queue"),
    path("approved/", views.approved_exchanges, name="approved_exchanges"),
    path("<int:pk>/action/", views.exchange_action, name="exchange_action"),
    path("positions/", settings_views.positions, name="positions"),
    path("rules/", settings_views.rules_settings, name="rules"),
    path("<int:pk>/print/", views.print_exchange_form, name="print_exchange_form"),
]
