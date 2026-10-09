from django.contrib import admin

from .models import Announcement, AnnouncementAction


class ActionInline(admin.TabularInline):
    model = AnnouncementAction
    extra = 0
    readonly_fields = [f.name for f in AnnouncementAction._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "announcement_type", "status", "date_issued", "expiry_date")
    list_filter = ("announcement_type", "status")
    inlines = [ActionInline]
