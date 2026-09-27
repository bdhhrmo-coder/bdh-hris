from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline

from .models import DocumentRequirement, UploadedDocument, UploadedDocumentEvent


class UploadedDocumentInline(GenericTabularInline):
    """Read-only, generic-FK inline other apps' admin.py files import to
    show a transaction's attached documents without duplicating this
    setup six times. Read-only because uploads must go through
    documents.views.document_upload, which runs CLAUDE.md §10's
    validation — not the raw admin file widget."""

    model = UploadedDocument
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 0
    fields = ("original_filename", "requirement", "is_confidential", "is_active", "uploaded_by", "uploaded_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentRequirement)
class DocumentRequirementAdmin(admin.ModelAdmin):
    list_display = ("content_type", "sub_type_value", "label", "alternative_group", "is_mandatory", "is_confidential")
    list_filter = ("content_type", "is_mandatory", "is_confidential")


class UploadedDocumentEventInline(admin.TabularInline):
    model = UploadedDocumentEvent
    fk_name = "document"
    extra = 0
    readonly_fields = [f.name for f in UploadedDocumentEvent._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UploadedDocument)
class UploadedDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "original_filename", "content_type", "object_id", "requirement", "is_confidential", "is_active",
        "uploaded_by", "uploaded_at",
    )
    list_filter = ("content_type", "is_confidential", "is_active", "file_type")
    search_fields = ("original_filename",)
    readonly_fields = ("file_size", "file_type", "uploaded_by", "uploaded_at")
    inlines = [UploadedDocumentEventInline]
