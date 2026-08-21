from django.urls import path

from . import views, views_2fa

app_name = "accounts"

urlpatterns = [
    path("login/", views.VarsityLoginView.as_view(), name="login"),
    path("logout/", views.VarsityLogoutView.as_view(), name="logout"),
    path("register/", views.register, name="register"),
    path("available.json", views.check_availability, name="check_availability"),
    # Confirming an email address. The link itself is open — it is read on
    # whichever device the inbox is on — and resending is a signed-in POST.
    path("verify/<str:token>/", views.verify_email, name="verify_email"),
    path("verify/resend/", views.resend_verification, name="resend_verification"),
    path("password/", views.VarsityPasswordChangeView.as_view(), name="password_change"),
    # Forgotten password. Names match Django's defaults so the built-in
    # PasswordResetForm can reverse them when it builds the email.
    path("password/reset/", views.VarsityPasswordResetView.as_view(), name="password_reset"),
    path(
        "password/reset/sent/",
        views.VarsityPasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        views.VarsityPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password/reset/done/",
        views.VarsityPasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # Second factor. Reachable by anyone — students may want one too — but
    # only demanded of accounts that can release money.
    path("security/", views_2fa.two_factor_manage, name="two_factor_manage"),
    path("security/setup/", views_2fa.two_factor_setup, name="two_factor_setup"),
    path("security/verify/", views_2fa.two_factor_verify, name="two_factor_verify"),
    path("security/codes/", views_2fa.two_factor_codes, name="two_factor_codes"),
    path(
        "security/codes/new/",
        views_2fa.two_factor_regenerate_codes,
        name="two_factor_regenerate_codes",
    ),
    path("security/disable/", views_2fa.two_factor_disable, name="two_factor_disable"),
    path("profile/", views.profile, name="profile"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("tickets/", views.my_tickets, name="tickets"),
    path("saved/", views.saved_events, name="saved"),
    path("u/<str:username>/", views.public_profile, name="public_profile"),
]
