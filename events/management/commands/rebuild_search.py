"""Rebuild the stored search vector for every event.

Each event refreshes its own vector when it is saved, so this is for the times
that isn't enough:

- after deploying the search field for the first time, when every row is null
- after a society or venue is renamed, since that name is baked into the vector
  of every event they host and renaming one doesn't re-save any of them
- after changing the weights or the text configuration in events/search.py

Does nothing on SQLite, which has no vector to rebuild.
"""

from django.core.management.base import BaseCommand

from events.models import Event
from events.search import refresh_search_vectors, supports_full_text


class Command(BaseCommand):
    help = "Rebuild the full-text search vector for every event."

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization",
            help="Only events run by this society, by slug. Use after a rename.",
        )

    def handle(self, *args, **options):
        if not supports_full_text():
            self.stdout.write(
                "This database has no full-text search — nothing to rebuild. "
                "Search falls back to LIKE."
            )
            return

        events = Event.objects.all()
        if options["organization"]:
            events = events.filter(organization__slug=options["organization"])
            if not events.exists():
                self.stderr.write(f"No society with the slug {options['organization']!r}.")
                return

        updated = refresh_search_vectors(events)

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt the search vector for {updated} event{'' if updated == 1 else 's'}."
            )
        )
