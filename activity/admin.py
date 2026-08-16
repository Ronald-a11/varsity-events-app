from django.contrib import admin

from .models import Activity


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("created_at", "verb", "actor", "target_name", "detail", "is_simulated")
    list_filter = ("verb", "is_simulated", "is_public")
    search_fields = ("actor__username", "event__title", "organization__name")
    autocomplete_fields = ("actor", "event", "organization")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at",)
    actions = ["purge_simulated"]

    @admin.action(description="Delete the simulated rows in this selection")
    def purge_simulated(self, request, queryset):
        deleted, _ = queryset.filter(is_simulated=True).delete()
        self.message_user(request, f"{deleted} simulated activity row(s) removed.")
