from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.VarsityLoginView.as_view(), name="login"),
    path("logout/", views.VarsityLogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("available.json", views.check_availability, name="check_availability"),
    path("password/", views.VarsityPasswordChangeView.as_view(), name="password_change"),
    path("profile/", views.profile, name="profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("tickets/", views.my_tickets, name="tickets"),
    path("saved/", views.saved_events, name="saved"),
    path("u/<str:username>/", views.public_profile, name="public_profile"),
]
