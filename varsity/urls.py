"""Root URL configuration for the Varsity Events platform."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("events/", include("events.urls")),
    path("societies/", include("organizations.urls")),
    path("pay/", include("payments.urls")),
]

if settings.DEBUG or settings.SERVE_MEDIA:
    # Wired by hand rather than with django.conf.urls.static.static(): that
    # helper silently returns an empty list whenever DEBUG is False, so in
    # production it registers nothing and every poster 404s.
    #
    # Django serving media is fine at this scale — posters and society logos,
    # read far more than written. Put a CDN or object storage in front of it
    # before traffic justifies the cost of doing so.
    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            serve,
            {"document_root": settings.MEDIA_ROOT},
        )
    ]

admin.site.site_header = "Varsity Events administration"
admin.site.site_title = "Varsity Events"
admin.site.index_title = "Platform management"

handler404 = "core.views.handler404"
handler500 = "core.views.handler500"
