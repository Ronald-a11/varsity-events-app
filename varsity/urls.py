"""Root URL configuration for the Varsity Events platform."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("accounts/", include("accounts.urls")),
    path("events/", include("events.urls")),
    path("societies/", include("organizations.urls")),
    path("pay/", include("payments.urls")),
]

if settings.DEBUG or settings.SERVE_MEDIA:
    # Django serving media is fine at this scale — posters and society logos,
    # read far more than written. Put a CDN or object storage in front of it
    # before traffic justifies the cost of doing so.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "Varsity Events administration"
admin.site.site_title = "Varsity Events"
admin.site.index_title = "Platform management"

handler404 = "core.views.handler404"
handler500 = "core.views.handler500"
