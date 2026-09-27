from django.contrib import admin

from .models import EducationHistory, Employee, EmployeeEditHistory


class EducationHistoryInline(admin.TabularInline):
    model = EducationHistory
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "surname", "first_name", "position", "employment_status", "is_active")
    search_fields = ("employee_id", "surname", "first_name")
    list_filter = ("employment_status", "is_active", "sections")
    inlines = [EducationHistoryInline]
    filter_horizontal = ("sections", "units")


@admin.register(EmployeeEditHistory)
class EmployeeEditHistoryAdmin(admin.ModelAdmin):
    list_display = ("employee", "field_label", "old_value", "new_value", "changed_by", "edited_at")
    list_filter = ("field_name",)
    search_fields = ("employee__surname", "employee__employee_id", "reason")
    readonly_fields = [f.name for f in EmployeeEditHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
