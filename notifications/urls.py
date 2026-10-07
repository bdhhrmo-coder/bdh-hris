from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="notification_list"),
    path("<int:pk>/open/", views.open_notification, name="open"),
    path("mark-all-read/", views.mark_all_read, name="mark_all_read"),
    path("unread-count/", views.unread_count, name="unread_count"),
]
