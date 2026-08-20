"""Turning what a poster said into something EventForm can show.

Kept apart from events/poster.py on purpose: that module talks to a model and
knows nothing about this app; this one knows the app and nothing about models.
Either can be tested without the other, and swapping how posters get read
doesn't touch any of the matching below.

Nothing here decides anything final. Every value becomes a form *initial*, which
the organizer sees, edits and confirms. Matching a society or a venue by name is
a guess made to save typing — where it's wrong, the field is a dropdown and they
change it.
"""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

logger = logging.getLogger(__name__)


def _parse_local(value):
    """An ISO string off a poster, as an aware datetime in the site's timezone."""
    if not value or not isinstance(value, str):
        return None
    try:
        naive = datetime.fromisoformat(value.replace("Z", "").strip())
    except ValueError:
        logger.info("Poster gave an unparseable datetime: %r", value)
        return None

    if timezone.is_aware(naive):
        return naive
    return timezone.make_aware(naive, timezone.get_current_timezone())


def match_organization(name, among):
    """Best-effort match of a society name against the ones this user runs.

    Exact first, then case-insensitive, then a containment check either way —
    posters print "UZ Jazz Society" for what the platform calls "Jazz Society".
    Ambiguity loses: two candidates means we pick neither and let them choose.
    """
    if not name:
        return None

    cleaned = name.strip().casefold()
    if not cleaned:
        return None

    for org in among:
        if org.name.casefold() == cleaned:
            return org

    partial = [
        org for org in among
        if cleaned in org.name.casefold() or org.name.casefold() in cleaned
    ]
    return partial[0] if len(partial) == 1 else None


def match_venue(name, university=None):
    """Match a printed venue against the ones already on file."""
    from .models import Venue

    if not name or not name.strip():
        return None

    venues = Venue.objects.all()
    if university is not None:
        venues = venues.filter(university=university)

    cleaned = name.strip().casefold()
    for venue in venues:
        if venue.name.casefold() == cleaned:
            return venue

    partial = [v for v in venues if cleaned in v.name.casefold() or v.name.casefold() in cleaned]
    return partial[0] if len(partial) == 1 else None


def _price(raw):
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def to_form_initial(data, organizations):
    """Map extracted poster fields onto EventForm initial values.

    Returns (initial, warnings). Warnings are shown above the form — things the
    poster didn't say, which the organizer therefore has to supply themselves.
    """
    initial, warnings = {}, []

    for field in ("title", "summary", "description"):
        value = (data.get(field) or "").strip()
        if value:
            initial[field] = value

    tags = data.get("tags") or []
    if isinstance(tags, list) and tags:
        initial["tags"] = ", ".join(str(t).strip() for t in tags if str(t).strip())[:200]

    starts = _parse_local(data.get("starts_at"))
    ends = _parse_local(data.get("ends_at"))

    if starts:
        initial["starts_at"] = starts
        if ends and ends > starts:
            initial["ends_at"] = ends
        else:
            # The model is told to leave this null unless the poster states it,
            # which most don't. Three hours is a guess, and it is labelled as one.
            initial["ends_at"] = starts + timezone.timedelta(hours=3)
            warnings.append("The poster didn't give an end time — we've assumed three hours.")

        if starts < timezone.now():
            warnings.append(
                "The date reads as being in the past. Check the year before you publish."
            )
    else:
        warnings.append("We couldn't read a date off the poster. You'll need to set it.")

    org = match_organization(data.get("organizer"), organizations)
    if org is not None:
        initial["organization"] = org.pk
    elif data.get("organizer"):
        warnings.append(
            f"The poster credits “{data['organizer']}”, which doesn't match a society "
            "you run. Pick the right one below."
        )

    venue = match_venue(data.get("venue_name"), org.university if org else None)
    if venue is not None:
        initial["venue"] = venue.pk
    elif data.get("venue_name"):
        # Somewhere the platform has never heard of. Keep the words rather than
        # discard them — location_note is free text and shows on the event page.
        initial["location_note"] = data["venue_name"].strip()[:200]

    is_free = data.get("is_free")
    price = _price(data.get("price"))

    if is_free is True:
        initial["is_free"] = True
    elif price is not None:
        initial["is_free"] = False
        initial["price"] = price
        currency = (data.get("currency") or "").strip().upper()
        if currency in {"USD", "ZWG"}:
            initial["currency"] = currency
    else:
        warnings.append("The poster didn't mention price. It'll default to free — change it if not.")

    if data.get("confidence") == "low":
        warnings.insert(
            0,
            "This poster was hard to read, so check every field below rather than skimming.",
        )
    if data.get("notes"):
        warnings.append(str(data["notes"]))

    return initial, warnings
