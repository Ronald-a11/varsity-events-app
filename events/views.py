import csv
import datetime as dt
from io import BytesIO
from urllib.parse import urlencode

import qrcode
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import dateformat, timezone
from django.views.decorators.http import require_POST

from accounts.models import University
from activity.models import Activity, record
from organizations.models import Organization
from payments.models import Payment, total_collected

from .forms import (
    CheckInForm,
    EventForm,
    EventUpdateForm,
    RegistrationForm,
    ReviewForm,
    TicketOutletFormSet,
)
from .models import Bookmark, Category, Event, Registration, Review, TicketStatus

SORT_OPTIONS = {
    "soonest": ("starts_at", "Starting soonest"),
    "popular": ("-confirmed_count", "Most popular"),
    "newest": ("-created_at", "Recently added"),
}

PAGE_SIZES = (12, 24, 48)

# Varsity Gigs is the entertainment end of the calendar: live music, open mics,
# parties, film nights. It's the same listing machinery with the categories fixed.
GIG_CATEGORY_SLUGS = ("music-arts", "social")

SITE_EDITION = settings.SITE_NAME


def _event_tabs(request, selected_when, selected_show, selected_price, route="events:list"):
    """The five ways people actually slice the calendar, as tabs.

    Each tab keeps the university and category the visitor already chose, so
    switching tab narrows rather than resets.
    """
    keep = {}
    for key in ("university", "category", "org", "q"):
        value = request.GET.get(key)
        if value:
            keep[key] = value

    def url(**extra):
        params = {**keep, **{k: v for k, v in extra.items() if v}}
        query = urlencode(params)
        return f"{reverse(route)}{'?' + query if query else ''}"

    is_past = selected_show == "past"
    return [
        {
            "label": "Upcoming",
            "url": url(),
            "active": not is_past and not selected_when and selected_price != "free",
        },
        {"label": "Today", "url": url(when="today"), "active": selected_when == "today"},
        {"label": "This week", "url": url(when="week"), "active": selected_when == "week"},
        {"label": "Free entry", "url": url(price="free"), "active": selected_price == "free"},
        {"label": "Archive", "url": url(show="past"), "active": is_past},
    ]


def _selling_fast(queryset):
    """Events closest to selling out — the sidebar's 'act now' rail.

    Capacity-limited only; an unlimited event can never be running out.
    """
    candidates = [
        event
        for event in queryset.filter(capacity__isnull=False)[:40]
        if not event.is_full and event.fill_percentage >= 40
    ]
    candidates.sort(key=lambda e: e.fill_percentage, reverse=True)
    return candidates[:4]


def group_by_day(events):
    """Bucket a page of events under readable date headings.

    Only meaningful when the page is in date order; for other sorts the caller
    just ignores it and renders a flat grid.
    """
    today = timezone.localdate()
    groups, current = [], None

    for event in events:
        day = timezone.localtime(event.starts_at).date()
        delta = (day - today).days

        if delta == 0:
            label, note = "Today", "Happening today"
        elif delta == 1:
            label, note = "Tomorrow", ""
        elif 1 < delta < 7:
            label, note = dateformat.format(event.starts_at, "l"), dateformat.format(event.starts_at, "j F")
        else:
            label, note = dateformat.format(event.starts_at, "l j F"), ""

        if current is None or current["day"] != day:
            current = {"day": day, "label": label, "note": note, "events": []}
            groups.append(current)
        current["events"].append(event)

    return groups


def _manage_or_404(request, slug):
    event = get_object_or_404(Event.objects.select_related("organization"), slug=slug)
    if not event.can_manage(request.user):
        raise Http404("No event matches the given query.")
    return event


# --------------------------------------------------------------------------
# Browsing
# --------------------------------------------------------------------------


def event_list(request, gig_guide=False):
    """The listings page. `gig_guide=True` serves the same page as Varsity Gigs."""
    qs = (
        Event.objects.published()
        .select_related("organization", "organization__university", "category", "venue")
        .with_counts()
    )

    if gig_guide:
        qs = qs.filter(category__slug__in=GIG_CATEGORY_SLUGS)

    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    when = request.GET.get("when", "")
    price = request.GET.get("price", "")
    university = request.GET.get("university", "")
    org = request.GET.get("org", "")
    tickets = request.GET.get("tickets", "")
    show = request.GET.get("show", "upcoming")
    sort = request.GET.get("sort", "soonest")

    if query:
        qs = qs.filter(
            Q(title__icontains=query)
            | Q(summary__icontains=query)
            | Q(description__icontains=query)
            | Q(tags__icontains=query)
            | Q(organization__name__icontains=query)
            | Q(venue__name__icontains=query)
        )

    if category:
        qs = qs.filter(category__slug=category)

    if university:
        qs = qs.filter(
            Q(venue__university__slug=university) | Q(organization__university__slug=university)
        )

    if org:
        qs = qs.filter(organization__slug=org)

    if price == "free":
        qs = qs.filter(is_free=True)
    elif price == "paid":
        qs = qs.filter(is_free=False)

    # Ticket availability. `confirmed_count` comes from with_counts() above, so this
    # stays a single query rather than evaluating the availability property per row.
    if tickets == "available":
        qs = qs.exclude(
            ticket_status__in=[TicketStatus.SOLD_OUT, TicketStatus.UNAVAILABLE]
        ).filter(Q(capacity__isnull=True) | Q(reserved_total__lt=F("capacity")))
    elif tickets == "soldout":
        qs = qs.filter(
            Q(ticket_status=TicketStatus.SOLD_OUT)
            | Q(capacity__isnull=False, reserved_total__gte=F("capacity"))
        )

    now = timezone.now()
    if show == "past":
        qs = qs.past()
    else:
        qs = qs.upcoming()
        if when == "today":
            qs = qs.filter(starts_at__date=now.date())
        elif when == "week":
            qs = qs.filter(starts_at__lte=now + timezone.timedelta(days=7))
        elif when == "month":
            qs = qs.filter(starts_at__lte=now + timezone.timedelta(days=30))

    if show != "past":
        order_field = SORT_OPTIONS.get(sort, SORT_OPTIONS["soonest"])[0]
        qs = qs.order_by(order_field)

    try:
        per_page = int(request.GET.get("per_page", 12))
    except (TypeError, ValueError):
        per_page = 12
    per_page = per_page if per_page in PAGE_SIZES else 12

    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

    active_filters = {
        k: v
        for k, v in {
            "q": query,
            "category": category,
            "when": when,
            "price": price,
            "university": university,
            "org": org,
            "tickets": tickets,
        }.items()
        if v
    }

    querystring = request.GET.copy()
    querystring.pop("page", None)

    # The featured strip only makes sense on an unfiltered first page — once you've
    # asked a specific question, everything below is the answer.
    shown = list(page.object_list)
    is_front = page.number == 1 and show != "past" and not active_filters

    featured = []
    if is_front:
        picked = list(
            Event.objects.published()
            .upcoming()
            .filter(is_featured=True)
            .select_related("organization", "organization__university", "category", "venue")[:3]
        )
        # Top up from the soonest events if there aren't three picks yet.
        if len(picked) < 3:
            picked += [e for e in shown if e not in picked][: 3 - len(picked)]
        featured = picked[:3]

    context = {
        "page_obj": page,
        "events": shown,
        "featured_events": featured,
        "is_front": is_front,
        "event_groups": group_by_day(shown),
        "tabs": _event_tabs(
            request,
            selected_when=when,
            selected_show=show,
            selected_price=price,
            route="events:gigs" if gig_guide else "events:list",
        ),
        # Numbering runs across pages, so page 2 starts at 13 rather than 1.
        "number_offset": page.start_index() - 1,
        "page_folio": (
            f"Nos. {page.start_index()}–{page.end_index()} of {paginator.count}"
            f" · Page {page.number} of {paginator.num_pages}"
        ),
        "total_count": paginator.count,
        "selling_fast": _selling_fast(qs),
        "free_soon": qs.filter(is_free=True)[:5],
        "gig_guide": gig_guide,
        "route": "events:gigs" if gig_guide else "events:list",
        "crumbs": [{"label": "Varsity Gigs" if gig_guide else "Events"}],
        "page_title": (
            f"Results for “{query}”"
            if query
            else ("Varsity Gigs" if gig_guide else "Explore Upcoming Events")
        ),
        "page_subtitle": (
            f"{paginator.count} "
            + ("gig" if gig_guide else "event")
            + ("" if paginator.count == 1 else "s")
            + f" {'in the archive' if show == 'past' else 'coming up'}"
            + (
                " — live music, open mics, parties and film nights across the country."
                if gig_guide
                else " across Zimbabwe's universities."
            )
        ),
        "masthead_edition": "Gig Guide" if gig_guide else SITE_EDITION,
        "page_sizes": PAGE_SIZES,
        "per_page": per_page,
        "range_start": page.start_index(),
        "range_end": page.end_index(),
        "categories": Category.objects.annotate(
            event_count=Count("events", filter=Q(events__status=Event.Status.PUBLISHED))
        ),
        "universities": University.objects.all(),
        "organizations": Organization.objects.filter(is_active=True).order_by("name"),
        "sort_options": SORT_OPTIONS,
        "active_filters": active_filters,
        "querystring": querystring.urlencode(),
        "selected": {
            "q": query,
            "category": category,
            "when": when,
            "price": price,
            "university": university,
            "org": org,
            "tickets": tickets,
            "show": show,
            "sort": sort,
        },
        "bookmarked_ids": set(
            request.user.bookmarks.values_list("event_id", flat=True)
            if request.user.is_authenticated
            else []
        ),
    }
    return render(request, "events/event_list.html", context)


def event_detail(request, slug):
    event = get_object_or_404(
        Event.objects.select_related("organization", "category", "venue", "created_by"),
        slug=slug,
    )

    if not event.can_be_seen_by(request.user):
        raise Http404("No event matches the given query.")

    Event.objects.filter(pk=event.pk).update(views_count=event.views_count + 1)

    registration = event.registration_for(request.user)
    is_bookmarked = (
        request.user.is_authenticated
        and Bookmark.objects.filter(user=request.user, event=event).exists()
    )

    can_review = (
        request.user.is_authenticated
        and event.has_ended
        and Registration.objects.filter(
            event=event, user=request.user, status=Registration.Status.CONFIRMED
        ).exists()
        and not Review.objects.filter(event=event, user=request.user).exists()
    )

    similar = (
        Event.objects.published()
        .upcoming()
        .exclude(pk=event.pk)
        .filter(Q(category=event.category) | Q(organization=event.organization))
        .select_related("organization", "category")
        .distinct()[:3]
    )

    reviews = event.reviews.select_related("user")
    rating_values = [r.rating for r in reviews]

    context = {
        "event": event,
        "registration": registration,
        "is_bookmarked": is_bookmarked,
        "registration_form": RegistrationForm(),
        "review_form": ReviewForm() if can_review else None,
        "can_review": can_review,
        "can_manage": event.can_manage(request.user),
        "updates": event.updates.select_related("author")[:5],
        "similar_events": similar,
        "reviews": reviews[:5],
        "average_rating": round(sum(rating_values) / len(rating_values), 1)
        if rating_values
        else None,
        "review_count": len(rating_values),
        "attendee_preview": event.confirmed_registrations.select_related("user")[:8],
        "event_activity": list(event.activities.public().for_feed()[:6]),
        "signups_today": event.activities.filter(
            verb__in=[Activity.Verb.REGISTERED, Activity.Verb.PAID],
            created_at__gte=timezone.now() - timezone.timedelta(hours=24),
        ).count(),
    }
    return render(request, "events/event_detail.html", context)


# --------------------------------------------------------------------------
# Attendee actions
# --------------------------------------------------------------------------


@login_required
@require_POST
def register_for_event(request, slug):
    event = get_object_or_404(Event, slug=slug)

    if not event.can_be_seen_by(request.user):
        raise Http404("No event matches the given query.")

    form = RegistrationForm(request.POST)
    notes = form.cleaned_data["notes"] if form.is_valid() else ""

    try:
        registration = event.register(request.user)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect(event.get_absolute_url())

    if notes and not registration.notes:
        registration.notes = notes
        registration.save(update_fields=["notes"])

    if registration.status == Registration.Status.AWAITING_PAYMENT:
        messages.info(
            request,
            f"Your place is held for 30 minutes. Pay {event.price_display} to confirm it.",
        )
        return redirect("payments:checkout", slug=event.slug)

    if registration.status == Registration.Status.CONFIRMED:
        messages.success(
            request, f"You're in. Ticket {registration.ticket_code} is saved to your account."
        )
    elif registration.status == Registration.Status.WAITLISTED:
        messages.warning(
            request, "This event is full — you're on the waitlist and we'll bump you up if a place opens."
        )
    else:
        messages.info(request, "Request sent. The organizer will confirm your place shortly.")

    return redirect("events:ticket", code=registration.ticket_code)


@login_required
@require_POST
def cancel_registration(request, slug):
    event = get_object_or_404(Event, slug=slug)
    registration = get_object_or_404(Registration, event=event, user=request.user)
    registration.cancel()
    messages.info(request, f"Your place at “{event.title}” has been released.")
    return redirect(event.get_absolute_url())


@login_required
@require_POST
def toggle_bookmark(request, slug):
    event = get_object_or_404(Event, slug=slug)
    bookmark, created = Bookmark.objects.get_or_create(user=request.user, event=event)
    if not created:
        bookmark.delete()

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"bookmarked": created})

    messages.success(request, "Saved to your list." if created else "Removed from your list.")
    return redirect(request.META.get("HTTP_REFERER", event.get_absolute_url()))


@login_required
@require_POST
def submit_review(request, slug):
    event = get_object_or_404(Event, slug=slug)

    attended = Registration.objects.filter(
        event=event, user=request.user, status=Registration.Status.CONFIRMED
    ).exists()
    if not (attended and event.has_ended):
        messages.error(request, "Only attendees can review an event once it has finished.")
        return redirect(event.get_absolute_url())

    form = ReviewForm(request.POST)
    if form.is_valid():
        Review.objects.update_or_create(
            event=event,
            user=request.user,
            defaults={
                "rating": form.cleaned_data["rating"],
                "comment": form.cleaned_data["comment"],
            },
        )
        record(
            Activity.Verb.REVIEWED,
            actor=request.user,
            event=event,
            rating=int(form.cleaned_data["rating"]),
        )
        messages.success(request, "Thanks for the feedback.")
    else:
        messages.error(request, "Please pick a rating before submitting.")

    return redirect(event.get_absolute_url())


@login_required
def ticket_detail(request, code):
    registration = get_object_or_404(
        Registration.objects.select_related("event", "event__organization", "event__venue", "user"),
        ticket_code=code,
    )

    if registration.user != request.user and not registration.event.can_manage(request.user):
        raise Http404("No ticket matches the given query.")

    return render(request, "events/ticket.html", {"registration": registration})


@login_required
def ticket_qr(request, code):
    """Renders the ticket code as a QR PNG for door scanning."""
    registration = get_object_or_404(Registration, ticket_code=code)

    if registration.user != request.user and not registration.event.can_manage(request.user):
        raise Http404("No ticket matches the given query.")

    img = qrcode.make(
        request.build_absolute_uri(registration.get_absolute_url()), box_size=8, border=2
    )
    buffer = BytesIO()
    img.save(buffer, format="PNG")

    response = HttpResponse(buffer.getvalue(), content_type="image/png")
    response["Cache-Control"] = "private, max-age=86400"
    return response


def event_ics(request, slug):
    """Download the event as a calendar invite."""
    event = get_object_or_404(Event, slug=slug)
    if not event.can_be_seen_by(request.user):
        raise Http404("No event matches the given query.")

    def stamp(value):
        return value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def escape(text):
        return (
            str(text).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Varsity Events//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:event-{event.pk}@varsityevents",
        f"DTSTAMP:{stamp(timezone.now())}",
        f"DTSTART:{stamp(event.starts_at)}",
        f"DTEND:{stamp(event.ends_at)}",
        f"SUMMARY:{escape(event.title)}",
        f"DESCRIPTION:{escape(event.summary or event.description[:300])}",
        f"LOCATION:{escape(event.online_url if event.is_online else event.location_display)}",
        f"URL:{request.build_absolute_uri(event.get_absolute_url())}",
        f"ORGANIZER;CN={escape(event.organization.name)}:MAILTO:{event.organization.email or 'no-reply@varsityevents.app'}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    response = HttpResponse("\r\n".join(lines), content_type="text/calendar")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}.ics"'
    return response


# --------------------------------------------------------------------------
# Organizer dashboard
# --------------------------------------------------------------------------


@login_required
def dashboard(request):
    if not (request.user.can_organize or request.user.managed_organizations().exists()):
        messages.info(
            request,
            "Organizer tools are for society leads. Create a society to start hosting events.",
        )
        return redirect("organizations:create")

    events = (
        Event.objects.filter(
            Q(created_by=request.user) | Q(organization__in=request.user.managed_organizations())
        )
        .select_related("organization", "category")
        .with_counts()
        .distinct()
    )

    now = timezone.now()
    upcoming = events.filter(ends_at__gte=now).order_by("starts_at")
    past = events.filter(ends_at__lt=now).order_by("-starts_at")
    drafts = events.filter(status=Event.Status.DRAFT).order_by("starts_at")

    total_registrations = Registration.objects.filter(
        event__in=events, status=Registration.Status.CONFIRMED
    ).count()
    checked_in = Registration.objects.filter(
        event__in=events, checked_in_at__isnull=False
    ).count()
    awaiting_payment = Registration.objects.filter(
        event__in=events, status=Registration.Status.AWAITING_PAYMENT
    ).count()
    to_verify = Payment.objects.filter(
        registration__event__in=events, status=Payment.Status.AWAITING_VERIFICATION
    ).count()

    actions = [
        {"label": "Create event", "url": reverse("events:create"), "style": "primary"},
        {"label": "New society", "url": reverse("organizations:create"), "style": "secondary"},
    ]
    if to_verify:
        actions.insert(
            0,
            {
                "label": "Verify EcoCash",
                "url": reverse("payments:verify"),
                "style": "secondary",
                "badge": to_verify,
            },
        )

    context = {
        "upcoming_events": upcoming,
        "past_events": past[:10],
        "drafts": drafts,
        "crumbs": [{"label": "Manage"}, {"label": "Dashboard"}],
        "actions": actions,
        "organizations": request.user.managed_organizations(),
        "stats": {
            "total_events": events.count(),
            "upcoming": upcoming.count(),
            "registrations": total_registrations,
            "checked_in": checked_in,
            "attendance_rate": round(checked_in / total_registrations * 100)
            if total_registrations
            else 0,
            "awaiting_payment": awaiting_payment,
            "to_verify": to_verify,
            "revenue": total_collected(events),
        },
    }
    return render(request, "events/dashboard.html", context)


@login_required
def event_create(request):
    organizations = request.user.managed_organizations()
    if not organizations.exists():
        messages.warning(
            request, "You need to run a society before you can publish events. Create one here."
        )
        return redirect("organizations:create")

    form = EventForm(request.POST or None, request.FILES or None, user=request.user)
    outlets = TicketOutletFormSet(request.POST or None, prefix="outlets")

    if request.method == "POST" and form.is_valid() and outlets.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        event.status = (
            Event.Status.PUBLISHED if request.POST.get("action") == "publish" else Event.Status.DRAFT
        )
        event.save()

        outlets.instance = event
        outlets.save()

        if event.status == Event.Status.PUBLISHED:
            record(
                Activity.Verb.PUBLISHED,
                actor=request.user,
                event=event,
                organization=event.organization,
            )

        messages.success(
            request,
            f"“{event.title}” {'is live' if event.status == Event.Status.PUBLISHED else 'saved as a draft'}.",
        )
        return redirect("events:manage_attendees", slug=event.slug)

    return render(
        request,
        "events/event_form.html",
        {"form": form, "outlets": outlets, "is_edit": False},
    )


@login_required
def event_edit(request, slug):
    event = _manage_or_404(request, slug)
    form = EventForm(
        request.POST or None, request.FILES or None, instance=event, user=request.user
    )
    outlets = TicketOutletFormSet(request.POST or None, instance=event, prefix="outlets")

    if request.method == "POST" and form.is_valid() and outlets.is_valid():
        event = form.save()
        outlets.save()

        action = request.POST.get("action")
        if action == "publish":
            was_draft = event.status != Event.Status.PUBLISHED
            event.status = Event.Status.PUBLISHED
            event.save(update_fields=["status"])
            if was_draft:
                record(
                    Activity.Verb.PUBLISHED,
                    actor=request.user,
                    event=event,
                    organization=event.organization,
                )
        elif action == "unpublish":
            event.status = Event.Status.DRAFT
            event.save(update_fields=["status"])

        messages.success(request, "Event updated.")
        return redirect(event.get_absolute_url())

    return render(
        request,
        "events/event_form.html",
        {"form": form, "outlets": outlets, "event": event, "is_edit": True},
    )


@login_required
@require_POST
def event_delete(request, slug):
    event = _manage_or_404(request, slug)
    title = event.title
    event.delete()
    messages.success(request, f"“{title}” has been deleted.")
    return redirect("events:dashboard")


@login_required
@require_POST
def event_cancel(request, slug):
    event = _manage_or_404(request, slug)
    event.status = Event.Status.CANCELLED
    event.save(update_fields=["status"])
    messages.warning(
        request, f"“{event.title}” is marked as cancelled. Attendees can see this on the page."
    )
    return redirect(event.get_absolute_url())


@login_required
def manage_attendees(request, slug):
    event = _manage_or_404(request, slug)

    registrations = event.registrations.select_related("user").order_by("-created_at")
    status = request.GET.get("status", "")
    if status:
        registrations = registrations.filter(status=status)

    update_form = EventUpdateForm(request.POST or None)
    if request.method == "POST" and update_form.is_valid():
        update = update_form.save(commit=False)
        update.event = event
        update.author = request.user
        update.save()
        messages.success(request, "Announcement posted to the event page.")
        return redirect("events:manage_attendees", slug=event.slug)

    context = {
        "event": event,
        "registrations": registrations,
        "status_filter": status,
        "status_choices": Registration.Status.choices,
        "update_form": update_form,
        "updates": event.updates.all(),
    }
    return render(request, "events/manage_attendees.html", context)


@login_required
def export_attendees(request, slug):
    event = _manage_or_404(request, slug)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-attendees.csv"'

    writer = csv.writer(response)
    writer.writerow(
        [
            "Ticket code",
            "Name",
            "Username",
            "Email",
            "University",
            "Course",
            "Status",
            "Paid",
            "Payment method",
            "Paynow reference",
            "Checked in",
            "Notes",
            "Registered",
        ]
    )
    for reg in event.registrations.select_related("user", "user__university").prefetch_related(
        "payments"
    ).order_by("created_at"):
        payment = reg.settled_payment
        writer.writerow(
            [
                reg.ticket_code,
                reg.user.display_name,
                reg.user.username,
                reg.user.email,
                reg.user.university.short_name if reg.user.university else "",
                reg.user.course,
                reg.get_status_display(),
                payment.amount_display if payment else "",
                payment.get_method_display() if payment else "",
                payment.paynow_reference if payment else "",
                reg.checked_in_at.strftime("%Y-%m-%d %H:%M") if reg.checked_in_at else "",
                reg.notes,
                reg.created_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )
    return response


@login_required
@require_POST
def registration_action(request, slug, pk, action):
    event = _manage_or_404(request, slug)
    registration = get_object_or_404(Registration, pk=pk, event=event)

    if action == "approve":
        registration.status = Registration.Status.CONFIRMED
        registration.save(update_fields=["status"])
        messages.success(request, f"{registration.user.display_name} is confirmed.")
    elif action == "decline":
        registration.cancel()
        messages.info(request, f"{registration.user.display_name}'s request was declined.")
    elif action == "checkin":
        if registration.check_in(by_user=request.user):
            messages.success(request, f"{registration.user.display_name} checked in.")
        else:
            messages.info(request, f"{registration.user.display_name} was already checked in.")
    elif action == "promote":
        registration.status = Registration.Status.CONFIRMED
        registration.save(update_fields=["status"])
        messages.success(request, f"{registration.user.display_name} moved off the waitlist.")

    return redirect("events:manage_attendees", slug=event.slug)


@login_required
def check_in(request, slug):
    """Door check-in: type or scan a ticket code."""
    event = _manage_or_404(request, slug)
    form = CheckInForm(request.POST or None)
    result = None

    if request.method == "POST" and form.is_valid():
        code = form.cleaned_data["ticket_code"]
        registration = Registration.objects.filter(
            ticket_code=code, event=event
        ).select_related("user").first()

        if registration is None:
            result = {"state": "invalid", "message": f"No ticket {code} for this event."}
        elif registration.status == Registration.Status.CANCELLED:
            result = {
                "state": "invalid",
                "message": f"{registration.user.display_name}'s ticket was cancelled.",
                "registration": registration,
            }
        elif registration.is_checked_in:
            result = {
                "state": "duplicate",
                "message": f"Already checked in at {registration.checked_in_at:%H:%M}.",
                "registration": registration,
            }
        else:
            registration.check_in(by_user=request.user)
            result = {
                "state": "ok",
                "message": f"Welcome, {registration.user.display_name}.",
                "registration": registration,
            }
        form = CheckInForm()

    context = {
        "event": event,
        "form": form,
        "result": result,
        "recent": event.registrations.filter(checked_in_at__isnull=False)
        .select_related("user")
        .order_by("-checked_in_at")[:10],
    }
    return render(request, "events/check_in.html", context)
