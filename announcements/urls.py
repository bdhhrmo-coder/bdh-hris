from django.urls import path

from . import views

app_name = "announcements"

urlpatterns = [
    path("", views.announcement_list, name="list"),
    path("manage/", views.manage, name="manage"),
    path("new/", views.create, name="create"),
    path("<int:pk>/", views.announcement_detail, name="detail"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/publish/", views.publish, name="publish"),
    path("<int:pk>/archive/", views.archive, name="archive"),
    path("<int:pk>/pdf/", views.announcement_pdf, name="pdf"),
]
