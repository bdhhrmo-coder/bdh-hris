from django.contrib import admin

from .models import Section, Unit


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    search_fields = ("name", "code")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("name", "section", "is_active")
    list_filter = ("section",)
