from django.urls import path

from . import views

app_name = "organizations"

urlpatterns = [
    path("", views.organization_list, name="list"),
    path("new/", views.organization_create, name="create"),
    path("<slug:slug>/", views.organization_detail, name="detail"),
    path("<slug:slug>/edit/", views.organization_edit, name="edit"),
    path("<slug:slug>/claim/", views.claim_organization, name="claim"),
    path("<slug:slug>/follow/", views.toggle_follow, name="toggle_follow"),
    path("<slug:slug>/join/", views.join_organization, name="join"),
    path("<slug:slug>/members/", views.manage_members, name="members"),
]
