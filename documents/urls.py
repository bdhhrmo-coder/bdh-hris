from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("<str:app_label>/<str:model_name>/<int:object_id>/", views.document_list, name="document_list"),
    path("<str:app_label>/<str:model_name>/<int:object_id>/upload/", views.document_upload, name="document_upload"),
    path("<int:pk>/replace/", views.document_replace, name="document_replace"),
    path("<int:pk>/download/", views.document_download, name="document_download"),
]
