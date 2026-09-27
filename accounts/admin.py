from django.contrib import admin

from .models import RoleAssignment


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "role", "is_oic", "section", "unit", "is_active")
    list_filter = ("role", "is_oic", "is_active")
    search_fields = ("employee__surname", "employee__first_name", "employee__employee_id")
