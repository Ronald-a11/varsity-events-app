from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import University, User


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ("short_name", "name", "kind", "city", "province")
    list_filter = ("kind", "province")
    search_fields = ("name", "short_name", "city")
    prepopulated_fields = {"slug": ("short_name",)}


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        "username",
        "email",
        "display_name",
        "role",
        "university",
        "is_verified_organizer",
    )
    list_filter = ("role", "university", "is_verified_organizer", "is_staff", "is_active")
    search_fields = ("username", "email", "first_name", "last_name", "student_id")
    filter_horizontal = ("interests", "groups", "user_permissions")
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Student profile",
            {
                "fields": (
                    "role",
                    "university",
                    "student_id",
                    "course",
                    "year_of_study",
                    "phone",
                    "bio",
                    "avatar",
                    "interests",
                    "is_verified_organizer",
                )
            },
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Student profile", {"fields": ("email", "role", "university")}),
    )
