from django.urls import path

from . import views

app_name = "cto"

urlpatterns = [
    path("claims/new/", views.claim_new, name="claim_new"),
    path("claims/new-exception/", views.exception_claim_new, name="exception_claim_new"),
    path("claims/<int:pk>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:pk>/action/", views.claim_action, name="claim_action"),
    path("opening-balances/", views.opening_balances, name="opening_balances"),
    path("carry-over/", views.carry_over, name="carry_over"),
    path("separation/", views.separation, name="separation"),
    path("mine/", views.my_cto, name="my_cto"),
    path("apply/", views.cto_apply, name="cto_apply"),
    path("queue/", views.cto_queue, name="cto_queue"),
    path("<int:pk>/action/", views.cto_action, name="cto_action"),
    path("<int:pk>/print/", views.print_cto_form, name="print_cto_form"),
]
