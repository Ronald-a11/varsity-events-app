from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("checkout/<slug:slug>/", views.checkout, name="checkout"),
    # Organizer-facing queue sits above the reference routes so "verify" is
    # never mistaken for a payment reference.
    path("verify/", views.verification_queue, name="verify"),
    # Named routes before the <reference> catch-all, or "earnings" is read as a
    # payment reference and 404s.
    path("earnings/", views.earnings, name="earnings"),
    path("payouts/", views.payout_desk, name="payout_desk"),
    path("payouts/prepare/<slug:slug>/", views.payout_prepare, name="payout_prepare"),
    path("payouts/<str:reference>/sent/", views.payout_mark_paid, name="payout_mark_paid"),
    path("payouts/<str:reference>/cancel/", views.payout_cancel, name="payout_cancel"),
    path("payouts/<str:reference>/", views.payout_detail, name="payout_detail"),
    path("<str:reference>/", views.payment_status, name="status"),
    path("<str:reference>/state.json", views.payment_status_json, name="status_json"),
    path("<str:reference>/transfer/", views.transfer, name="transfer"),
    path("<str:reference>/return/", views.payment_return, name="return"),
    path("<str:reference>/result/", views.payment_result, name="result"),
    path("<str:reference>/resend/", views.resend_prompt, name="resend"),
    path("<str:reference>/simulate/", views.simulator, name="simulator"),
]
