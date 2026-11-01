from django.contrib import admin

from documents.admin import UploadedDocumentInline

from .models import (
    CTOClaim,
    CTOClaimAction,
    CTOClaimLine,
    CTOMultiplierRate,
    CTOUsageApplication,
    CTOUsageApplicationAction,
    OvertimeLine,
    OvertimeRequest,
    OvertimeRequestAction,
)


class NoDeleteAdmin(admin.ModelAdmin):
    """OT requests and claims are audit records: never deleted from admin
    (cancel through the app instead). Lines are read-only inlines."""

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyInline(admin.TabularInline):
    extra = 0

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CTOMultiplierRate)
class CTOMultiplierRateAdmin(admin.ModelAdmin):
    list_display = ("effective_date", "weekday_multiplier", "restday_holiday_multiplier", "notes")


class OvertimeLineInline(ReadOnlyInline):
    model = OvertimeLine


class OvertimeRequestActionInline(ReadOnlyInline):
    model = OvertimeRequestAction


@admin.register(OvertimeRequest)
class OvertimeRequestAdmin(NoDeleteAdmin):
    # Status/route are read-only: approval only happens through cto/routing.py.
    list_display = ("pk", "filed_by", "filed_as", "status", "current_step", "created_at")
    list_filter = ("status", "filed_as")
    readonly_fields = ("status", "route", "current_step", "approved_on")
    inlines = [OvertimeLineInline, OvertimeRequestActionInline]


class CTOClaimLineInline(ReadOnlyInline):
    model = CTOClaimLine


class CTOClaimActionInline(ReadOnlyInline):
    model = CTOClaimAction


@admin.register(CTOClaim)
class CTOClaimAdmin(NoDeleteAdmin):
    list_display = ("pk", "employee", "duty_type", "status", "current_step", "first_filed_on", "approved_on")
    list_filter = ("status", "duty_type")
    search_fields = ("employee__surname", "employee__employee_id")
    readonly_fields = ("status", "route", "current_step", "approved_on", "first_filed_on")
    inlines = [CTOClaimLineInline, CTOClaimActionInline, UploadedDocumentInline]


class CTOUsageApplicationActionInline(ReadOnlyInline):
    model = CTOUsageApplicationAction


@admin.register(CTOUsageApplication)
class CTOUsageApplicationAdmin(admin.ModelAdmin):
    list_display = ("employee", "start_date", "end_date", "number_of_days", "status")
    list_filter = ("status",)
    search_fields = ("employee__surname", "employee__employee_id")
    inlines = [CTOUsageApplicationActionInline, UploadedDocumentInline]
