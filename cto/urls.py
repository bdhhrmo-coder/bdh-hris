from django.urls import path

from . import views

app_name = "cto"

urlpatterns = [
    path("credit/new/", views.credit_entry_create, name="credit_entry_create"),
    path("claims/", views.claims_home, name="claims_home"),
    path("claims/from-ot/<int:ot_pk>/", views.claim_create, name="claim_create"),
    path("claims/<int:pk>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:pk>/submit/", views.claim_submit, name="claim_submit"),
    path("claims/<int:pk>/discard/", views.claim_discard, name="claim_discard"),
    path("mine/", views.my_cto, name="my_cto"),
    path("apply/", views.cto_apply, name="cto_apply"),
    path("queue/", views.cto_queue, name="cto_queue"),
    path("<int:pk>/action/", views.cto_action, name="cto_action"),
]
