from django.contrib import admin

from .models import DateChangeAction, DateChangeRequest


class DateChangeActionInline(admin.TabularInline):
    model = DateChangeAction
    extra = 0
    can_delete = False
    readonly_fields = ["action", "resulting_status", "notes", "acted_by", "acted_at"]


@admin.register(DateChangeRequest)
class DateChangeRequestAdmin(admin.ModelAdmin):
    """Read-only record for the System Administrator; changes go through the HRIS screens."""

    list_display = ["pk", "employee", "kind", "old_start_date", "new_start_date", "status", "submitted_at"]
    list_filter = ["kind", "status"]
    search_fields = ["employee__surname", "employee__first_name"]
    inlines = [DateChangeActionInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
