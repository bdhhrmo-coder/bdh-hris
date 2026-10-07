from django.contrib import admin

from documents.admin import UploadedDocumentInline

from .models import (
    AttendanceCorrectionLine,
    AttendanceCorrectionRequest,
    AttendanceCorrectionRequestAction,
    AttendanceRecord,
    BiometricColumnMapping,
    BiometricImportBatch,
)


@admin.register(BiometricColumnMapping)
class BiometricColumnMappingAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "employee_id_column", "date_column", "time_in_column", "time_out_column")


@admin.register(BiometricImportBatch)
class BiometricImportBatchAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "uploaded_by", "uploaded_at", "row_count", "imported_count", "skipped_count", "error_count")
    readonly_fields = [f.name for f in BiometricImportBatch._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "date", "time_in", "time_out", "is_absent", "source", "undertime_minutes")
    list_filter = ("source", "is_absent")
    search_fields = ("employee__surname", "employee__employee_id")
    readonly_fields = ("undertime_minutes",)


class AttendanceCorrectionLineInline(admin.TabularInline):
    model = AttendanceCorrectionLine
    extra = 0


class AttendanceCorrectionRequestActionInline(admin.TabularInline):
    model = AttendanceCorrectionRequestAction
    extra = 0
    readonly_fields = [f.name for f in AttendanceCorrectionRequestAction._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AttendanceCorrectionRequest)
class AttendanceCorrectionRequestAdmin(admin.ModelAdmin):
    list_display = ("employee", "dates_summary", "correction_type", "status", "submitted_at")
    list_filter = ("correction_type", "status")
    search_fields = ("employee__surname", "employee__employee_id")
    inlines = [AttendanceCorrectionLineInline, AttendanceCorrectionRequestActionInline, UploadedDocumentInline]
