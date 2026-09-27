from django.contrib import admin

from .models import RetentionReviewRecord


@admin.register(RetentionReviewRecord)
class RetentionReviewRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "decision", "reviewed_by", "reviewed_at")
    list_filter = ("decision",)
    search_fields = ("employee__surname", "employee__first_name", "employee__employee_id")
    readonly_fields = ("reviewed_at",)
