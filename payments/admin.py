from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "user",
        "event_title",
        "amount_display",
        "method",
        "status",
        "is_simulated",
        "created_at",
    )
    list_filter = ("status", "method", "is_simulated", "currency")
    search_fields = ("reference", "paynow_reference", "user__username", "user__email")
    autocomplete_fields = ("registration", "user")
    readonly_fields = (
        "reference",
        "paynow_reference",
        "poll_url",
        "browser_url",
        "paid_at",
        "last_polled_at",
        "created_at",
        "updated_at",
    )
    actions = ["resync_with_paynow", "mark_refunded"]

    @admin.display(description="Event")
    def event_title(self, obj):
        return obj.registration.event.title

    @admin.action(description="Re-check selected payments with Paynow")
    def resync_with_paynow(self, request, queryset):
        from .views import _sync

        for payment in queryset.select_related("registration"):
            _sync(payment)
        self.message_user(request, f"{queryset.count()} payment(s) re-checked.")

    @admin.action(description="Mark selected payments as refunded")
    def mark_refunded(self, request, queryset):
        for payment in queryset:
            payment.mark_refunded()
        self.message_user(request, f"{queryset.count()} payment(s) marked refunded.")
