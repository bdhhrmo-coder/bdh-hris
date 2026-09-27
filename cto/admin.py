from django.contrib import admin

from .models import (
    CTOCreditEntry,
    CTOCreditTransaction,
    CTOMultiplierRate,
    CTOUsageApplication,
    CTOUsageApplicationAction,
)


@admin.register(CTOMultiplierRate)
class CTOMultiplierRateAdmin(admin.ModelAdmin):
    list_display = ("effective_date", "weekday_multiplier", "restday_holiday_multiplier", "notes")


@admin.register(CTOCreditEntry)
class CTOCreditEntryAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "work_date", "hours_worked", "is_restday_or_holiday",
        "multiplier_applied", "credited_hours", "credited_days", "recorded_by",
    )
    list_filter = ("is_restday_or_holiday",)
    search_fields = ("employee__surname", "employee__employee_id")
    readonly_fields = ("multiplier_applied", "credited_hours", "credited_days", "shift_hours_used", "recorded_by")


@admin.register(CTOCreditTransaction)
class CTOCreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("employee", "transaction_type", "days", "transaction_date", "created_by")
    list_filter = ("transaction_type",)
    search_fields = ("employee__surname", "employee__employee_id")
    readonly_fields = [f.name for f in CTOCreditTransaction._meta.fields]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class CTOUsageApplicationActionInline(admin.TabularInline):
    model = CTOUsageApplicationAction
    extra = 0
    readonly_fields = [f.name for f in CTOUsageApplicationAction._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CTOUsageApplication)
class CTOUsageApplicationAdmin(admin.ModelAdmin):
    list_display = ("employee", "start_date", "end_date", "number_of_days", "status")
    list_filter = ("status",)
    search_fields = ("employee__surname", "employee__employee_id")
    inlines = [CTOUsageApplicationActionInline]
