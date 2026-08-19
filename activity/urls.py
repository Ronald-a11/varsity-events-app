from django.urls import path

from . import views

app_name = "activity"

urlpatterns = [
    path("", views.live_board, name="live"),
    path("feed.json", views.feed_json, name="feed"),
]
