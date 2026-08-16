from django.contrib import admin
from django.utils import timezone

from .models import (
    Bookmark,
    Category,
    Event,
    EventUpdate,
    Registration,
    Review,
    TicketOutlet,
    TicketStatus,
    Venue,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("icon", "name", "color", "description")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("name", "university", "capacity")
    list_filter = ("university",)
    search_fields = ("name", "address")


class RegistrationInline(admin.TabularInline):
    model = Registration
    extra = 0
    readonly_fields = ("ticket_code", "created_at")
    autocomplete_fields = ("user",)


class EventUpdateInline(admin.StackedInline):
    model = EventUpdate
    extra = 0


class TicketOutletInline(admin.TabularInline):
    model = TicketOutlet
    extra = 1
    fields = ("kind", "name", "detail", "url", "phone", "price_note", "is_available", "sort_order")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "organization",
        "starts_at",
        "status",
        "availability_label",
        "is_featured",
        "attendee_count",
        "capacity",
    )
    list_filter = (
        "status",
        "ticket_status",
        "visibility",
        "is_featured",
        "is_free",
        "category",
        "organization__university",
        "organization",
    )
    search_fields = ("title", "summary", "description", "tags")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "starts_at"
    autocomplete_fields = ("organization", "venue", "created_by")
    inlines = [TicketOutletInline, RegistrationInline, EventUpdateInline]
    readonly_fields = ("views_count", "created_at", "updated_at")
    actions = ["publish_events", "feature_events", "unfeature_events", "cancel_events", "mark_sold_out"]

    @admin.display(description="Tickets")
    def availability_label(self, obj):
        return obj.availability["label"]

    @admin.action(description="Publish selected events")
    def publish_events(self, request, queryset):
        updated = queryset.update(status=Event.Status.PUBLISHED)
        self.message_user(request, f"{updated} event(s) published.")

    @admin.action(description="Feature selected events on the homepage")
    def feature_events(self, request, queryset):
        updated = queryset.update(is_featured=True)
        self.message_user(request, f"{updated} event(s) featured.")

    @admin.action(description="Remove selected events from the picks")
    def unfeature_events(self, request, queryset):
        updated = queryset.update(is_featured=False)
        self.message_user(request, f"{updated} event(s) removed from the picks.")

    @admin.action(description="Cancel selected events")
    def cancel_events(self, request, queryset):
        updated = queryset.update(status=Event.Status.CANCELLED)
        self.message_user(request, f"{updated} event(s) cancelled.")

    @admin.action(description="Mark selected events as sold out")
    def mark_sold_out(self, request, queryset):
        updated = queryset.update(ticket_status=TicketStatus.SOLD_OUT)
        self.message_user(request, f"{updated} event(s) marked sold out.")


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("ticket_code", "user", "event", "status", "checked_in_at", "created_at")
    list_filter = ("status", "event__organization")
    search_fields = ("ticket_code", "user__username", "user__email", "event__title")
    autocomplete_fields = ("user", "event")
    readonly_fields = ("ticket_code", "created_at")
    actions = ["mark_checked_in"]

    @admin.action(description="Check in selected attendees")
    def mark_checked_in(self, request, queryset):
        updated = queryset.filter(checked_in_at__isnull=True).update(
            checked_in_at=timezone.now(), checked_in_by=request.user
        )
        self.message_user(request, f"{updated} attendee(s) checked in.")


@admin.register(EventUpdate)
class EventUpdateAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "author", "created_at")
    search_fields = ("title", "body")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("event", "user", "rating", "created_at")
    list_filter = ("rating",)


@admin.register(Bookmark)
class BookmarkAdmin(admin.ModelAdmin):
    list_display = ("user", "event", "created_at")
    autocomplete_fields = ("user", "event")


@admin.register(TicketOutlet)
class TicketOutletAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "kind", "price_note", "is_available")
    list_filter = ("kind", "is_available")
    search_fields = ("name", "detail", "event__title")
    autocomplete_fields = ("event",)
    actions = ["mark_unavailable", "mark_available"]

    @admin.action(description="Mark selected outlets as sold out")
    def mark_unavailable(self, request, queryset):
        updated = queryset.update(is_available=False)
        self.message_user(request, f"{updated} outlet(s) marked sold out.")

    @admin.action(description="Mark selected outlets as back in stock")
    def mark_available(self, request, queryset):
        updated = queryset.update(is_available=True)
        self.message_user(request, f"{updated} outlet(s) marked available.")
