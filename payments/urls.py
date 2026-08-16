from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("checkout/<slug:slug>/", views.checkout, name="checkout"),
    # Organizer-facing queue sits above the reference routes so "verify" is
    # never mistaken for a payment reference.
    path("verify/", views.verification_queue, name="verify"),
    path("<str:reference>/", views.payment_status, name="status"),
    path("<str:reference>/state.json", views.payment_status_json, name="status_json"),
    path("<str:reference>/transfer/", views.transfer, name="transfer"),
    path("<str:reference>/return/", views.payment_return, name="return"),
    path("<str:reference>/result/", views.payment_result, name="result"),
    path("<str:reference>/resend/", views.resend_prompt, name="resend"),
    path("<str:reference>/simulate/", views.simulator, name="simulator"),
]
