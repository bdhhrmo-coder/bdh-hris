from django.contrib import admin

from .models import LeaveApplication, LeaveApplicationAction, LeaveCreditTransaction, LeaveType


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = (
        "code", "name", "applicable_to", "requires_full_routing",
        "balance_tracking", "monthly_accrual_days", "annual_fixed_days", "is_cumulative", "is_active",
    )
    list_filter = ("applicable_to", "balance_tracking", "requires_full_routing", "is_active")


class LeaveApplicationActionInline(admin.TabularInline):
    model = LeaveApplicationAction
    extra = 0
    readonly_fields = [f.name for f in LeaveApplicationAction._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LeaveApplication)
class LeaveApplicationAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "start_date", "end_date", "number_of_days", "status")
    list_filter = ("status", "leave_type")
    search_fields = ("employee__surname", "employee__employee_id")
    inlines = [LeaveApplicationActionInline]


@admin.register(LeaveCreditTransaction)
class LeaveCreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("employee", "leave_type", "transaction_type", "days", "transaction_date", "created_by")
    list_filter = ("transaction_type", "leave_type")
    search_fields = ("employee__surname", "employee__employee_id")
    readonly_fields = [f.name for f in LeaveCreditTransaction._meta.fields]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
