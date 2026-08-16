from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("discover/", views.discover, name="discover"),
    path("about/", views.about, name="about"),
    path("search.json", views.quick_search, name="quick_search"),
    # Staff curation
    path("staff/", views.staff_dashboard, name="staff_dashboard"),
    path("staff/societies/", views.staff_societies, name="staff_societies"),
    path("staff/events/<slug:slug>/<str:action>/", views.staff_event_action, name="staff_event_action"),
    path(
        "staff/societies/<slug:slug>/<str:action>/",
        views.staff_society_action,
        name="staff_society_action",
    ),
]
