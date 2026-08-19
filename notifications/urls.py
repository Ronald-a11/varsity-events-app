from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("key.json", views.public_key, name="public_key"),
    path("subscribe/", views.subscribe, name="subscribe"),
    path("unsubscribe/", views.unsubscribe, name="unsubscribe"),
]
