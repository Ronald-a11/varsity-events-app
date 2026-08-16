from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from urllib.parse import urlencode
from django.views.decorators.http import require_POST

from accounts.models import University, User
from activity.models import Activity, record
from events.models import Event

from .forms import MembershipForm, OrganizationForm
from .models import Membership, Organization


def _society_tabs(request, selected_kind, selected_sort):
    """Directory / league-table / type, as tabs. Search and university persist."""
    keep = {}
    for key in ("q", "university"):
        value = request.GET.get(key)
        if value:
            keep[key] = value

    def url(**extra):
        params = {**keep, **{k: v for k, v in extra.items() if v}}
        query = urlencode(params)
        return f"{reverse('organizations:list')}{'?' + query if query else ''}"

    ranked = selected_sort in {"active", "popular"}
    return [
        {"label": "Directory", "url": url(), "active": not ranked and not selected_kind},
        {"label": "Most active", "url": url(sort="active"), "active": selected_sort == "active"},
        {"label": "Most followed", "url": url(sort="popular"), "active": selected_sort == "popular"},
        {"label": "Societies", "url": url(kind="society"), "active": selected_kind == "society"},
        {"label": "Clubs", "url": url(kind="club"), "active": selected_kind == "club"},
        {"label": "Sports", "url": url(kind="sports"), "active": selected_kind == "sports"},
    ]


def _number(societies, offset=0):
    """Stamp a running row number on each society.

    Nested template loops can't carry a running index across groups, so the
    numbering is decided here where it's simply enumerate().
    """
    for index, society in enumerate(societies, start=offset + 1):
        society.row_number = index
    return societies


def _group_by_university(societies):
    """Bucket a page of societies under their university, keeping the given order."""
    groups, current = [], None

    for society in societies:
        key = society.university_id
        if current is None or current["key"] != key:
            current = {
                "key": key,
                "university": society.university,
                "label": society.university.name if society.university else "Not tied to a campus",
                "short": str(society.university) if society.university else "Nationwide",
                "societies": [],
            }
            groups.append(current)
        current["societies"].append(society)

    return groups


def organization_list(request):
    qs = Organization.objects.filter(is_active=True).annotate(
        event_count=Count("events", filter=Q(events__status=Event.Status.PUBLISHED), distinct=True),
        follower_total=Count("followers", distinct=True),
    )

    query = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    university = request.GET.get("university", "")
    sort = request.GET.get("sort", "name")

    if query:
        qs = qs.filter(
            Q(name__icontains=query) | Q(tagline__icontains=query) | Q(description__icontains=query)
        )
    if kind:
        qs = qs.filter(kind=kind)
    if university:
        qs = qs.filter(university__slug=university)

    # Directory order: verified societies lead each group, then alphabetical.
    # The other sorts are explicit rankings, so they don't get the verified lift.
    ordering = {
        "name": ("university__name", "-is_verified", "name"),
        "active": ("-event_count", "name"),
        "popular": ("-follower_total", "name"),
    }.get(sort, ("university__name", "-is_verified", "name"))
    qs = qs.order_by(*ordering)

    paginator = Paginator(qs, 18)
    page = paginator.get_page(request.GET.get("page"))

    # Evaluate once: the grouped and flat views must share the same instances,
    # or the row numbers stamped on one won't be on the other.
    societies = _number(list(page.object_list), page.start_index() - 1)

    return render(
        request,
        "organizations/organization_list.html",
        {
            "page_obj": page,
            "organizations": societies,
            "grouped": _group_by_university(societies) if sort == "name" else None,
            "total_count": paginator.count,
            "range_start": page.start_index(),
            "range_end": page.end_index(),
            "verified_count": qs.filter(is_verified=True).count(),
            "tabs": _society_tabs(request, kind, sort),
            "number_offset": page.start_index() - 1,
            "page_folio": (
                f"Nos. {page.start_index()}–{page.end_index()} of {paginator.count}"
                f" · Page {page.number} of {paginator.num_pages}"
            ),
            "crumbs": [{"label": "Societies"}],
            "page_subtitle": (
                f"{paginator.count} group{'' if paginator.count == 1 else 's'} putting on "
                f"events at universities across Zimbabwe."
            ),
            "actions": [
                {
                    "label": "Register a society",
                    "url": reverse("organizations:create"),
                    "style": "primary",
                }
            ],
            "kinds": Organization.Kind.choices,
            "universities": University.objects.all(),
            "selected": {"q": query, "kind": kind, "university": university, "sort": sort},
            "followed_ids": set(
                request.user.followed_organizations.values_list("id", flat=True)
                if request.user.is_authenticated
                else []
            ),
        },
    )


def organization_detail(request, slug):
    organization = get_object_or_404(
        Organization.objects.select_related("university"), slug=slug, is_active=True
    )

    upcoming = (
        organization.events.published()
        .upcoming()
        .select_related("category", "venue")
        .with_counts()
    )
    past = organization.events.published().past().select_related("category")[:6]

    membership = None
    if request.user.is_authenticated:
        membership = organization.memberships.filter(user=request.user).first()

    context = {
        "organization": organization,
        "upcoming_events": upcoming,
        "past_events": past,
        "leaders": organization.memberships.filter(
            is_active=True, role__in=[Membership.Role.OWNER, Membership.Role.ADMIN]
        ).select_related("user"),
        "membership": membership,
        "is_following": request.user.is_authenticated
        and organization.followers.filter(pk=request.user.pk).exists(),
        "can_manage": organization.can_manage(request.user),
        "total_events": organization.events.published().count(),
    }
    return render(request, "organizations/organization_detail.html", context)


@login_required
def organization_create(request):
    form = OrganizationForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        organization = form.save(commit=False)
        organization.created_by = request.user
        if not organization.university_id:
            organization.university = request.user.university
        organization.save()

        Membership.objects.create(
            organization=organization, user=request.user, role=Membership.Role.OWNER
        )
        organization.followers.add(request.user)

        if request.user.role == User.Role.STUDENT:
            request.user.role = User.Role.ORGANIZER
            request.user.save(update_fields=["role"])

        messages.success(
            request,
            f"“{organization.name}” is registered. You can publish its first event now.",
        )
        return redirect(organization.get_absolute_url())

    return render(request, "organizations/organization_form.html", {"form": form, "is_edit": False})


@login_required
def organization_edit(request, slug):
    organization = get_object_or_404(Organization, slug=slug)
    if not organization.can_manage(request.user):
        raise Http404("No society matches the given query.")

    form = OrganizationForm(
        request.POST or None, request.FILES or None, instance=organization
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Society profile updated.")
        return redirect(organization.get_absolute_url())

    return render(
        request,
        "organizations/organization_form.html",
        {"form": form, "organization": organization, "is_edit": True},
    )


@login_required
@require_POST
def toggle_follow(request, slug):
    organization = get_object_or_404(Organization, slug=slug, is_active=True)
    following = organization.followers.filter(pk=request.user.pk).exists()

    if following:
        organization.followers.remove(request.user)
    else:
        organization.followers.add(request.user)
        record(Activity.Verb.FOLLOWED, actor=request.user, organization=organization)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"following": not following})

    messages.success(
        request,
        f"Unfollowed {organization.name}." if following else f"You're following {organization.name}.",
    )
    return redirect(organization.get_absolute_url())


@login_required
@require_POST
def join_organization(request, slug):
    organization = get_object_or_404(Organization, slug=slug, is_active=True)
    membership, created = Membership.objects.get_or_create(
        organization=organization,
        user=request.user,
        defaults={"role": Membership.Role.MEMBER},
    )

    if created:
        organization.followers.add(request.user)
        record(Activity.Verb.JOINED, actor=request.user, organization=organization)
        messages.success(request, f"You're now a member of {organization.name}.")
    elif not membership.is_active:
        membership.is_active = True
        membership.save(update_fields=["is_active"])
        messages.success(request, f"Welcome back to {organization.name}.")
    else:
        membership.delete()
        messages.info(request, f"You've left {organization.name}.")

    return redirect(organization.get_absolute_url())


@login_required
def manage_members(request, slug):
    organization = get_object_or_404(Organization, slug=slug)
    if not organization.can_manage(request.user):
        raise Http404("No society matches the given query.")

    memberships = organization.memberships.select_related("user").order_by("role", "joined_at")

    if request.method == "POST":
        membership = get_object_or_404(
            Membership, pk=request.POST.get("membership_id"), organization=organization
        )
        action = request.POST.get("action")

        if action == "remove":
            if membership.role == Membership.Role.OWNER:
                messages.error(request, "Transfer ownership before removing the owner.")
            else:
                membership.delete()
                messages.info(request, "Member removed.")
        elif action in {Membership.Role.ADMIN, Membership.Role.MEMBER}:
            membership.role = action
            membership.save(update_fields=["role"])
            messages.success(
                request, f"{membership.user.display_name} is now {membership.get_role_display()}."
            )
        return redirect("organizations:members", slug=organization.slug)

    return render(
        request,
        "organizations/manage_members.html",
        {
            "organization": organization,
            "memberships": memberships,
            "member_form": MembershipForm(),
        },
    )
