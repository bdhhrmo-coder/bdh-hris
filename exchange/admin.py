from django.contrib import admin

from .models import DutyExchangeRequest, DutyExchangeRequestAction


class DutyExchangeRequestActionInline(admin.TabularInline):
    model = DutyExchangeRequestAction
    extra = 0
    readonly_fields = [f.name for f in DutyExchangeRequestAction._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DutyExchangeRequest)
class DutyExchangeRequestAdmin(admin.ModelAdmin):
    list_display = (
        "employee_a", "date_a", "employee_b", "date_b", "status", "is_emergency", "filed_at",
    )
    list_filter = ("status", "is_emergency")
    search_fields = (
        "employee_a__surname", "employee_a__employee_id", "employee_b__surname", "employee_b__employee_id",
    )
    inlines = [DutyExchangeRequestActionInline]
