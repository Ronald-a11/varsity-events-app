import re

from django.contrib import messages
from django.contrib.auth import login
from django.core.cache import cache
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django_ratelimit.decorators import ratelimit

from events.models import Bookmark, Event, Registration

from .forms import LoginForm, ProfileForm, SignUpForm
from .models import User


# Simple in-memory throttle. Enough to blunt credential stuffing on a single
# box; swap the cache backend for Redis and it works across processes too.
LOGIN_MAX_ATTEMPTS = 8
LOGIN_LOCKOUT_SECONDS = 300


def _throttle_key(request):
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    return f"login-attempts:{ip or request.META.get('REMOTE_ADDR', 'unknown')}"


class VarsityLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        attempts = cache.get(_throttle_key(request), 0)
        if attempts >= LOGIN_MAX_ATTEMPTS:
            messages.error(
                request,
                "Too many failed sign-in attempts. Try again in a few minutes.",
            )
            return self.render_to_response(self.get_context_data(form=self.get_form()))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        cache.delete(_throttle_key(self.request))

        # "Keep me signed in" or a session that dies with the browser.
        if form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(60 * 60 * 24 * 30)
        else:
            self.request.session.set_expiry(0)

        messages.success(self.request, f"Welcome back, {form.get_user().display_name}.")
        return super().form_valid(form)

    def form_invalid(self, form):
        key = _throttle_key(self.request)
        cache.set(key, cache.get(key, 0) + 1, LOGIN_LOCKOUT_SECONDS)
        return super().form_invalid(form)


# Answers "does this account exist?" to anybody who asks, which is exactly
# what you'd automate to harvest a list of real students. It has to stay open —
# the sign-up form calls it before anyone has an account — so bound it instead.
@ratelimit(key="ip", rate="60/m", block=True)
def check_availability(request):
    """Live username / email availability for the sign-up form."""
    field = request.GET.get("field", "")
    value = (request.GET.get("value") or "").strip()

    if field not in {"username", "email"} or len(value) < 3:
        return JsonResponse({"checked": False})

    if field == "username":
        if not re.fullmatch(r"[\w.@+-]+", value):
            return JsonResponse(
                {"checked": True, "available": False, "message": "Letters, numbers and . @ + - only."}
            )
        taken = User.objects.filter(username__iexact=value).exists()
        message = "That username is taken." if taken else "Available."
    else:
        if "@" not in value or "." not in value.split("@")[-1]:
            return JsonResponse({"checked": False})
        taken = User.objects.filter(email__iexact=value).exists()
        message = "An account already uses this email." if taken else "Looks good."

    return JsonResponse({"checked": True, "available": not taken, "message": message})


class VarsityLogoutView(LogoutView):
    next_page = reverse_lazy("core:home")


class VarsityPasswordChangeView(PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:profile")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        from core.forms import BASE_INPUT

        for field in form.fields.values():
            field.widget.attrs["class"] = BASE_INPUT
        return form

    def form_valid(self, response):
        messages.success(self.request, "Your password has been updated.")
        return super().form_valid(response)


class TailwindFieldsMixin:
    """Django's auth views build their own forms; style them like ours."""

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        from core.forms import BASE_INPUT

        for field in form.fields.values():
            field.widget.attrs["class"] = BASE_INPUT
        return form


class VarsityPasswordResetView(TailwindFieldsMixin, PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "emails/password_reset.txt"
    html_email_template_name = "emails/password_reset.html"
    subject_template_name = "emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    extra_email_context = {"SITE_NAME": settings.SITE_NAME}


class VarsityPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class VarsityPasswordResetConfirmView(TailwindFieldsMixin, PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class VarsityPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@ratelimit(key="ip", rate="10/h", method="POST", block=True)
def register(request):
    if request.user.is_authenticated:
        return redirect("core:home")

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(
            request,
            f"Welcome to Varsity Events, {user.first_name}! "
            "Tell us what you're into and we'll put the right events in front of you.",
        )
        return redirect(f"{reverse('accounts:profile_edit')}?welcome=1")

    return render(request, "accounts/register.html", {"form": form})


@login_required
def profile(request):
    registrations = (
        Registration.objects.filter(user=request.user)
        .exclude(status=Registration.Status.CANCELLED)
        .select_related("event", "event__organization")
    )
    context = {
        "profile_user": request.user,
        "upcoming_tickets": [r for r in registrations if not r.event.has_ended][:4],
        "ticket_count": registrations.count(),
        "bookmark_count": Bookmark.objects.filter(user=request.user).count(),
        "organizations": request.user.organizations.filter(memberships__is_active=True).distinct(),
        "hosted_count": Event.objects.filter(created_by=request.user).count(),
    }
    return render(request, "accounts/profile.html", context)


@login_required
def profile_edit(request):
    """Capture the student's details. New sign-ups go straight on to the feed."""
    is_welcome = request.GET.get("welcome") == "1"
    form = ProfileForm(
        request.POST or None, request.FILES or None, instance=request.user
    )

    if request.method == "POST" and form.is_valid():
        form.save()
        if is_welcome or request.POST.get("welcome") == "1":
            messages.success(request, "You're all set — here's what's on across Zimbabwe.")
            return redirect("core:discover")
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")

    return render(
        request, "accounts/profile_edit.html", {"form": form, "is_welcome": is_welcome}
    )


def public_profile(request, username):
    person = get_object_or_404(User, username=username, is_active=True)
    context = {
        "profile_user": person,
        "organizations": person.organizations.filter(memberships__is_active=True).distinct(),
        "hosted_events": Event.objects.published()
        .filter(created_by=person)
        .upcoming()
        .select_related("organization")[:6],
    }
    return render(request, "accounts/public_profile.html", context)


@login_required
def my_tickets(request):
    registrations = (
        Registration.objects.filter(user=request.user)
        .select_related("event", "event__organization", "event__venue")
        .order_by("event__starts_at")
    )
    tab = request.GET.get("tab", "upcoming")

    # Split once so each tab can carry its own count.
    upcoming = [
        r
        for r in registrations
        if not r.event.has_ended and r.status != Registration.Status.CANCELLED
    ]
    past = [r for r in registrations if r.event.has_ended]
    cancelled = [r for r in registrations if r.status == Registration.Status.CANCELLED]

    shown = {"past": past, "cancelled": cancelled}.get(tab, upcoming)

    tabs = [
        {"label": "Upcoming", "url": "?tab=upcoming", "active": tab == "upcoming", "count": len(upcoming)},
        {"label": "Past", "url": "?tab=past", "active": tab == "past", "count": len(past)},
        {"label": "Cancelled", "url": "?tab=cancelled", "active": tab == "cancelled", "count": len(cancelled)},
    ]

    return render(
        request,
        "accounts/tickets.html",
        {
            "registrations": shown,
            "tab": tab,
            "tabs": tabs,
            "crumbs": [{"label": "My tickets"}],
        },
    )


@login_required
def saved_events(request):
    bookmarks = (
        Bookmark.objects.filter(user=request.user)
        .select_related("event", "event__organization", "event__category", "event__venue")
    )
    return render(
        request,
        "accounts/saved.html",
        {"bookmarks": bookmarks, "crumbs": [{"label": "Saved events"}]},
    )
