from django.contrib import admin

from documents.admin import UploadedDocumentInline

from .models import OfficialRequest, OfficialRequestAction


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
