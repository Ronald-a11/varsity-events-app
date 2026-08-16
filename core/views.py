from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import connections
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import dateformat, timezone
from django.utils.timezone import localtime
from django.views.decorators.http import require_POST

from accounts.models import University, User
from events.models import Category, Event, Registration, TicketStatus
from organizations.models import Organization


def _bookmarked_ids(request):
    if not request.user.is_authenticated:
        return set()
    return set(request.user.bookmarks.values_list("event_id", flat=True))


def healthz(request):
    """Liveness probe for the platform's load balancer.

    Touches the database, because a process that's up but can't reach Postgres
    is not healthy — it would just serve 500s to every student.
    """
    try:
        connections["default"].cursor().execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 — any failure means "don't route here"
        return JsonResponse({"ok": False, "error": str(exc)}, status=503)

    return JsonResponse({"ok": True})


def home(request):
    """One entry point, two jobs.

    Signed out it explains what the platform is; signed in it *is* the events
    feed, rendered at the same URL rather than bouncing through a redirect.
    """
    if request.user.is_authenticated:
        return discover(request)

    # The landing page explains the product; it no longer lists events or
    # societies, so it only needs the counts and the university roll-call.
    published = Event.objects.published()

    context = {
        "categories": Category.objects.annotate(
            event_count=Count("events", filter=Q(events__status=Event.Status.PUBLISHED))
        ),
        "universities": University.objects.annotate(
            event_count=Count(
                "organizations__events",
                filter=Q(organizations__events__status=Event.Status.PUBLISHED),
                distinct=True,
            )
        ).order_by("-event_count", "name")[:12],
        "stats": {
            "events": published.count(),
            "societies": Organization.objects.filter(is_active=True).count(),
            "universities": University.objects.count(),
            "tickets": Registration.objects.filter(status=Registration.Status.CONFIRMED).count(),
        },
    }
    return render(request, "core/home.html", context)


@login_required
def discover(request):
    """Where signed-in students land: what's on right now, nationwide.

    Their own university floats to the top, but every Zimbabwean university is
    on the page — the point is to see everything, not just your own campus.
    """
    user = request.user
    now = timezone.now()

    base = (
        Event.objects.published()
        .upcoming()
        .select_related("organization", "organization__university", "category", "venue")
        .with_counts()
    )

    university = user.university
    mine = base.filter(
        Q(organization__university=university) | Q(venue__university=university)
    ).distinct() if university else base.none()

    interests = user.interests.all()
    for_you = (
        base.filter(category__in=interests).exclude(pk__in=mine.values("pk"))[:6]
        if interests
        else base.none()
    )

    followed = user.followed_organizations.all()
    from_societies = (
        base.filter(organization__in=followed)[:6] if followed.exists() else base.none()
    )

    happening_now = Event.objects.published().filter(
        starts_at__lte=now, ends_at__gte=now
    ).select_related("organization", "organization__university", "category")

    context = {
        "happening_now": happening_now[:4],
        "my_university_events": mine[:6],
        "my_university_total": mine.count(),
        "for_you": for_you,
        "from_societies": from_societies,
        "nationwide": base.exclude(
            pk__in=[e.pk for e in mine[:6]]
        ).order_by("starts_at")[:9],
        "tickets_going_fast": base.filter(
            capacity__isnull=False, confirmed_count__gte=1
        ).exclude(
            ticket_status__in=[TicketStatus.SOLD_OUT, TicketStatus.UNAVAILABLE]
        ).order_by("-confirmed_count")[:3],
        "this_week_count": base.filter(
            starts_at__lte=now + timezone.timedelta(days=7)
        ).count(),
        "universities": University.objects.annotate(
            event_count=Count(
                "organizations__events",
                filter=Q(
                    organizations__events__status=Event.Status.PUBLISHED,
                    organizations__events__ends_at__gte=now,
                ),
                distinct=True,
            )
        ).filter(event_count__gt=0).order_by("-event_count", "name"),
        "categories": Category.objects.all(),
        "nationwide_folio": f"{base.count()} events listed",
        "needs_profile": not user.profile_is_complete,
        "upcoming_tickets": Registration.objects.filter(
            user=user, status=Registration.Status.CONFIRMED, event__ends_at__gte=now
        ).select_related("event", "event__organization").order_by("event__starts_at")[:3],
        "bookmarked_ids": _bookmarked_ids(request),
    }
    return render(request, "core/discover.html", context)


def about(request):
    return render(request, "core/about.html")


def quick_search(request):
    """Feeds the ⌘K palette: events, societies and universities in one list."""
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    results = []

    events = (
        Event.objects.published()
        .upcoming()
        .filter(
            Q(title__icontains=query)
            | Q(summary__icontains=query)
            | Q(tags__icontains=query)
        )
        .select_related("organization", "organization__university", "category")[:6]
    )
    for event in events:
        results.append(
            {
                "icon": event.category.icon if event.category else "🎓",
                "title": event.title,
                "subtitle": f"{event.organization.name} · "
                f"{dateformat.format(localtime(event.starts_at), 'j M, H:i')}",
                "badge": event.availability["label"],
                "url": event.get_absolute_url(),
            }
        )

    societies = Organization.objects.filter(is_active=True, name__icontains=query).select_related(
        "university"
    )[:4]
    for society in societies:
        results.append(
            {
                "icon": "🏛️",
                "title": society.name,
                "subtitle": f"{society.get_kind_display()}"
                + (f" · {society.university}" if society.university else ""),
                "badge": "Society",
                "url": society.get_absolute_url(),
            }
        )

    universities = University.objects.filter(
        Q(name__icontains=query) | Q(short_name__icontains=query)
    )[:4]
    for university in universities:
        results.append(
            {
                "icon": "🎓",
                "title": university.name,
                "subtitle": f"{university.city} · {university.get_kind_display()}",
                "badge": university.short_name or "University",
                "url": university.get_absolute_url(),
            }
        )

    return JsonResponse({"results": results})


# --------------------------------------------------------------------------
# Staff curation dashboard
# --------------------------------------------------------------------------


def staff_required(view):
    """Only platform staff may curate events across every university."""
    return login_required(
        user_passes_test(lambda u: u.is_platform_staff, login_url="core:home")(view)
    )


@staff_required
def staff_dashboard(request):
    now = timezone.now()
    events = Event.objects.select_related(
        "organization", "organization__university", "category", "created_by"
    ).with_counts()

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    university = request.GET.get("university", "")
    picked = request.GET.get("picked", "")
    when = request.GET.get("when", "upcoming")

    if query:
        events = events.filter(
            Q(title__icontains=query) | Q(organization__name__icontains=query)
        )
    if status:
        events = events.filter(status=status)
    if university:
        events = events.filter(organization__university__slug=university)
    if picked == "yes":
        events = events.filter(is_featured=True)
    elif picked == "no":
        events = events.filter(is_featured=False)

    if when == "past":
        events = events.filter(ends_at__lt=now).order_by("-starts_at")
    elif when == "all":
        events = events.order_by("-starts_at")
    else:
        events = events.filter(ends_at__gte=now).order_by("starts_at")

    paginator = Paginator(events, 20)
    page = paginator.get_page(request.GET.get("page"))

    querystring = request.GET.copy()
    querystring.pop("page", None)

    all_events = Event.objects.all()
    context = {
        "page_obj": page,
        "events": page.object_list,
        "total_count": paginator.count,
        "querystring": querystring.urlencode(),
        "universities": University.objects.all(),
        "status_choices": Event.Status.choices,
        "selected": {
            "q": query,
            "status": status,
            "university": university,
            "picked": picked,
            "when": when,
        },
        "stats": {
            "universities": University.objects.count(),
            "societies": Organization.objects.filter(is_active=True).count(),
            "unverified": Organization.objects.filter(is_verified=False, is_active=True).count(),
            "published": all_events.filter(status=Event.Status.PUBLISHED).count(),
            "drafts": all_events.filter(status=Event.Status.DRAFT).count(),
            "picked": all_events.filter(is_featured=True).count(),
            "upcoming": all_events.filter(
                status=Event.Status.PUBLISHED, ends_at__gte=now
            ).count(),
            "tickets": Registration.objects.filter(
                status=Registration.Status.CONFIRMED
            ).count(),
            "students": User.objects.filter(role=User.Role.STUDENT).count(),
        },
    }
    return render(request, "core/staff_dashboard.html", context)


@staff_required
@require_POST
def staff_event_action(request, slug, action):
    """Curate one event: pick it, publish it, pull it, or mark its tickets gone."""
    event = get_object_or_404(Event, slug=slug)
    label = f"“{event.title}”"

    if action == "pick":
        event.is_featured = True
        event.save(update_fields=["is_featured"])
        messages.success(request, f"{label} added to the picks.")
    elif action == "unpick":
        event.is_featured = False
        event.save(update_fields=["is_featured"])
        messages.info(request, f"{label} removed from the picks.")
    elif action == "publish":
        event.status = Event.Status.PUBLISHED
        event.save(update_fields=["status"])
        messages.success(request, f"{label} is now live.")
    elif action == "unpublish":
        event.status = Event.Status.DRAFT
        event.save(update_fields=["status"])
        messages.info(request, f"{label} moved back to draft.")
    elif action == "cancel":
        event.status = Event.Status.CANCELLED
        event.save(update_fields=["status"])
        messages.warning(request, f"{label} is marked cancelled.")
    elif action == "sold_out":
        event.ticket_status = TicketStatus.SOLD_OUT
        event.save(update_fields=["ticket_status"])
        messages.info(request, f"{label} is marked sold out.")
    elif action == "on_sale":
        event.ticket_status = TicketStatus.AUTO
        event.save(update_fields=["ticket_status"])
        messages.success(request, f"{label} is back to automatic ticket status.")
    elif action == "delete":
        event.delete()
        messages.success(request, f"{label} deleted.")
    else:
        messages.error(request, "Unknown action.")

    return redirect(request.META.get("HTTP_REFERER", "core:staff_dashboard"))


@staff_required
def staff_societies(request):
    societies = (
        Organization.objects.select_related("university", "created_by")
        .annotate(
            event_total=Count("events", distinct=True),
            member_total=Count("memberships", distinct=True),
        )
        .order_by("-is_active", "name")
    )

    verified = request.GET.get("verified", "")
    if verified == "no":
        societies = societies.filter(is_verified=False)
    elif verified == "yes":
        societies = societies.filter(is_verified=True)

    paginator = Paginator(societies, 25)
    page = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "core/staff_societies.html",
        {
            "page_obj": page,
            "societies": page.object_list,
            "total_count": paginator.count,
            "selected": {"verified": verified},
        },
    )


@staff_required
@require_POST
def staff_society_action(request, slug, action):
    organization = get_object_or_404(Organization, slug=slug)

    if action == "verify":
        organization.is_verified = True
        organization.save(update_fields=["is_verified"])
        messages.success(request, f"{organization.name} is verified.")
    elif action == "unverify":
        organization.is_verified = False
        organization.save(update_fields=["is_verified"])
        messages.info(request, f"{organization.name} is no longer verified.")
    elif action == "suspend":
        organization.is_active = False
        organization.save(update_fields=["is_active"])
        messages.warning(request, f"{organization.name} is suspended and hidden from students.")
    elif action == "restore":
        organization.is_active = True
        organization.save(update_fields=["is_active"])
        messages.success(request, f"{organization.name} is visible again.")

    return redirect("core:staff_societies")


def handler404(request, exception=None):
    return render(request, "core/404.html", status=404)


def handler500(request):
    return render(request, "core/500.html", status=500)
