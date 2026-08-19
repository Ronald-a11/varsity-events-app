from django.contrib import admin

from .models import PushSubscription


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "user_agent", "created_at", "last_used_at", "failures")
    list_filter = ("created_at", "last_used_at")
    search_fields = ("user__username", "user__email", "endpoint")
    # The endpoint and keys are credentials for pushing to somebody's device.
    # There is no reason for a person to edit them, and every reason not to.
    readonly_fields = ("user", "endpoint", "p256dh", "auth", "user_agent", "created_at")
