from django.contrib import admin

from .models import Membership, Organization, OrganizationClaim


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 1
    autocomplete_fields = ("user",)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "university", "is_verified", "is_active", "member_count")
    list_filter = ("kind", "university", "is_verified", "is_active")
    search_fields = ("name", "tagline", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [MembershipInline]
    actions = ["verify_organizations"]

    @admin.action(description="Mark selected societies as verified")
    def verify_organizations(self, request, queryset):
        updated = queryset.update(is_verified=True)
        self.message_user(request, f"{updated} society(ies) verified.")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "title", "is_active", "joined_at")
    list_filter = ("role", "is_active")
    search_fields = ("user__username", "organization__name")
    autocomplete_fields = ("user", "organization")


@admin.register(OrganizationClaim)
class OrganizationClaimAdmin(admin.ModelAdmin):
    """The queue lives at /staff/claims/; this is for the awkward cases."""

    list_display = ("organization", "user", "role_title", "status", "created_at", "reviewed_by")
    list_filter = ("status", "organization__university")
    search_fields = ("organization__name", "user__username", "user__email", "evidence")
    autocomplete_fields = ()
    readonly_fields = ("created_at", "reviewed_at")
