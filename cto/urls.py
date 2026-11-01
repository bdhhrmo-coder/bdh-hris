from django.urls import path

from . import views

app_name = "cto"

urlpatterns = [
    path("ot/new/", views.ot_new, name="ot_new"),
    path("ot/<int:pk>/", views.ot_detail, name="ot_detail"),
    path("ot/<int:pk>/edit/", views.ot_edit, name="ot_edit"),
    path("ot/<int:pk>/action/", views.ot_action, name="ot_action"),
    path("claims/new/", views.claim_new, name="claim_new"),
    path("claims/new-exception/", views.exception_claim_new, name="exception_claim_new"),
    path("claims/<int:pk>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:pk>/action/", views.claim_action, name="claim_action"),
    path("mine/", views.my_cto, name="my_cto"),
    path("apply/", views.cto_apply, name="cto_apply"),
    path("queue/", views.cto_queue, name="cto_queue"),
    path("<int:pk>/action/", views.cto_action, name="cto_action"),
]
