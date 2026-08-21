from django.urls import path

from . import maintenance, pwa, views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    # Installability and offline tickets. sw.js has to answer from the root:
    # a service worker can only control pages at or below its own path, so one
    # served out of /static/ would control nothing that matters.
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("manifest.webmanifest", pwa.manifest, name="manifest"),
    path("offline/", pwa.offline, name="offline"),
    # Lets an outside scheduler stand in for the task cluster. Answers 404
    # unless TASK_TOKEN is set and presented. See core/maintenance.py.
    path("tasks/run/", maintenance.run_scheduled_jobs, name="run_scheduled_jobs"),
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
