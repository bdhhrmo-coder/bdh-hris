from django.contrib import admin

from .models import DutySchedule, ScheduleAction, ScheduleChange, ShiftCode


@admin.register(ShiftCode)
class ShiftCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "description", "paid_hours", "is_duty", "is_active", "sort_order"]


class ActionInline(admin.TabularInline):
    model = ScheduleAction
    extra = 0
    readonly_fields = ["action", "resulting_status", "notes", "acted_by", "acted_at"]
    can_delete = False


class ChangeInline(admin.TabularInline):
    model = ScheduleChange
    extra = 0
    readonly_fields = ["employee", "date", "old_shift", "new_shift", "reason", "exchange", "changed_by", "changed_at"]
    can_delete = False


@admin.register(DutySchedule)
class DutyScheduleAdmin(admin.ModelAdmin):
    """Read-only here: schedules are routed and changed through the HRIS
    pages, so every step and change is logged."""
    list_display = ["__str__", "status", "preparer_role", "approved_at", "recorded_at"]
    list_filter = ["status", "year", "month"]
    inlines = [ActionInline, ChangeInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
