from django.contrib import admin

from documents.admin import UploadedDocumentInline

from .models import OfficialRequest, OfficialRequestAction, TravelOrderSettings


class OfficialRequestActionInline(admin.TabularInline):
    model = OfficialRequestAction
    extra = 0
    readonly_fields = [f.name for f in OfficialRequestAction._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OfficialRequest)
class OfficialRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "request_type", "start_date", "end_date", "status", "submitted_at")
    list_filter = ("request_type", "status")
    search_fields = ("employee__surname", "employee__employee_id")
    inlines = [OfficialRequestActionInline, UploadedDocumentInline]


@admin.register(TravelOrderSettings)
class TravelOrderSettingsAdmin(admin.ModelAdmin):
    """One row: the names printed on every Travel Order (2026-11-03)."""

    list_display = ("__str__", "coh_name", "budget_officer_name", "governor_name", "updated_at")

    def has_add_permission(self, request):
        return not TravelOrderSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
