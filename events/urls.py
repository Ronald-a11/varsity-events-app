from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.event_list, name="list"),
    # Same listing machinery, categories fixed to the entertainment end.
    path("gigs/", views.event_list, {"gig_guide": True}, name="gigs"),
    # Organizer dashboard — kept above the slug route so "manage" is never
    # mistaken for an event slug.
    path("manage/", views.dashboard, name="dashboard"),
    path("manage/new/", views.event_create, name="create"),
    path("manage/<slug:slug>/edit/", views.event_edit, name="edit"),
    path("manage/<slug:slug>/delete/", views.event_delete, name="delete"),
    path("manage/<slug:slug>/cancel/", views.event_cancel, name="cancel"),
    path("manage/<slug:slug>/attendees/", views.manage_attendees, name="manage_attendees"),
    path("manage/<slug:slug>/attendees/export/", views.export_attendees, name="export_attendees"),
    path("manage/<slug:slug>/check-in/", views.check_in, name="check_in"),
    path(
        "manage/<slug:slug>/registrations/<int:pk>/<str:action>/",
        views.registration_action,
        name="registration_action",
    ),
    # Tickets
    path("tickets/<str:code>/", views.ticket_detail, name="ticket"),
    path("tickets/<str:code>/qr.png", views.ticket_qr, name="ticket_qr"),
    # Public event pages
    path("<slug:slug>/", views.event_detail, name="detail"),
    path("<slug:slug>/register/", views.register_for_event, name="register"),
    path("<slug:slug>/cancel-registration/", views.cancel_registration, name="cancel_registration"),
    path("<slug:slug>/save/", views.toggle_bookmark, name="toggle_bookmark"),
    path("<slug:slug>/review/", views.submit_review, name="review"),
    path("<slug:slug>/calendar.ics", views.event_ics, name="ics"),
]
