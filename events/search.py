"""Searching for events.

Postgres does this properly: a stored `tsvector` behind a GIN index, weighted so
a word in the title beats the same word buried in a description, stemmed so
"parties" finds "party", and ranked so the best match is first.

SQLite can do none of that, and development runs on SQLite. So both paths live
here behind one function, and the fallback is the `icontains` chain this app
used everywhere before — no worse than it was, and only ever seen on a database
that isn't production's.

The vector is stored rather than computed per query. Building it on the fly
means reading every row and re-tokenising it on every keystroke, which is
precisely what an index exists to avoid — and it couldn't use one anyway.
"""

from django.db import connections, router

# The weights, in the order Postgres names them:
#
#   A  title
#   B  summary, tags, the society's name
#   C  the venue
#   D  description
#
# Postgres' default weighting is {D, C, B, A} = {0.1, 0.2, 0.4, 1.0}, so a title
# hit outranks a description hit ten to one. That is about right here: students
# search for the name of a thing far more often than for words inside it.
FALLBACK_FIELDS = (
    "title",
    "summary",
    "tags",
    "organization__name",
    "venue__name",
    "description",
)


def supports_full_text(using=None):
    """Whether the database behind this queryset can do any of the above."""
    return connections[using or "default"].vendor == "postgresql"


def search_events(queryset, query):
    """Filter and order `queryset` by relevance to `query`.

    An empty query returns the queryset untouched, so callers can hand this
    whatever came off the querystring without checking it first.
    """
    query = (query or "").strip()
    if not query:
        return queryset

    if supports_full_text(queryset.db):
        return _ranked(queryset, query)
    return _like(queryset, query)


def _ranked(queryset, query):
    from django.contrib.postgres.search import SearchQuery, SearchRank

    # websearch is the syntax people already know from every search box they
    # have used: quoted phrases, OR, and a leading minus to exclude. It is also
    # the only search_type that cannot raise on malformed input, and this string
    # arrives straight off a querystring.
    search = SearchQuery(query, config="english", search_type="websearch")

    return (
        queryset.filter(search_vector=search)
        .annotate(relevance=SearchRank("search_vector", search))
        .order_by("-relevance", "starts_at")
    )


def _like(queryset, query):
    from django.db.models import Q

    matches = Q()
    for field in FALLBACK_FIELDS:
        matches |= Q(**{f"{field}__icontains": query})

    # distinct(), because matching through the organization and venue joins can
    # return the same event more than once.
    return queryset.filter(matches).distinct()


# --------------------------------------------------------------------------
# Keeping the vector current
# --------------------------------------------------------------------------


def vector_for(event):
    """The weighted vector for one event, built from values rather than columns.

    Values, because half the text isn't on the row: the society's name and the
    venue's are each a join away, and `UPDATE ... FROM` is not something
    `QuerySet.update()` will build. Passing the strings in costs one statement
    per event, which is the right trade for something that runs when an event is
    saved. `rebuild_search` wears the same cost across the table, rarely.
    """
    from django.contrib.postgres.search import SearchVector
    from django.db.models import TextField, Value

    by_weight = (
        ("A", [event.title]),
        ("B", [event.summary, event.tags, _name(event, "organization")]),
        ("C", [_name(event, "venue")]),
        ("D", [event.description]),
    )

    vector = None
    for weight, chunks in by_weight:
        text = " ".join(chunk for chunk in chunks if chunk).strip()
        if not text:
            continue
        # An explicit text output_field, or the parameter reaches Postgres
        # untyped and to_tsvector has to guess at it.
        part = SearchVector(
            Value(text, output_field=TextField()), weight=weight, config="english"
        )
        vector = part if vector is None else vector + part

    # An event with no text at all still needs a vector rather than a null, or
    # it would never match anything and never be visibly wrong either.
    if vector is None:
        return SearchVector(Value("", output_field=TextField()), config="english")
    return vector


def _name(event, relation):
    related = getattr(event, relation, None)
    return related.name if related else ""


def refresh_search_vectors(queryset=None):
    """Rewrite the stored vector for some or all events. Returns how many.

    Called after an event is saved, and by `rebuild_search` for everything —
    which is what you want after a society is renamed, since its name is baked
    into the vector of every event it runs.

    A no-op on anything but Postgres, where there is no vector to keep.
    """
    from .models import Event

    if not supports_full_text(router.db_for_write(Event)):
        return 0

    queryset = Event.objects.all() if queryset is None else queryset
    updated = 0

    for event in queryset.select_related("organization", "venue").iterator(chunk_size=500):
        Event.objects.filter(pk=event.pk).update(search_vector=vector_for(event))
        updated += 1

    return updated
