from django.contrib import admin

from .models import Report

# Register your models here.


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """The queue at /staff/reports/ is the working view; this is for the rest."""

    list_display = ("__str__", "reason", "status", "reporter", "created_at", "reviewed_by")
    list_filter = ("status", "reason")
    search_fields = ("detail", "reporter__username", "event__title", "organization__name")
    readonly_fields = ("created_at", "reviewed_at")
