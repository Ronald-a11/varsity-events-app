"""Search, on both databases it has to work on.

The behaviour every deployment shares is tested unconditionally. The parts only
Postgres can do — stemming, ranking, websearch syntax — are skipped on SQLite
and run in CI, which does the whole suite against both.
"""

from datetime import timedelta
from io import StringIO
from unittest import skipIf, skipUnless

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import University, User
from organizations.models import Organization

from .models import Category, Event, Venue
from .search import refresh_search_vectors, search_events, supports_full_text

POSTGRES = connection.vendor == "postgresql"
NEEDS_PG = skipUnless(POSTGRES, "full-text search needs Postgres")
SQLITE_ONLY = skipIf(POSTGRES, "describes the LIKE fallback")


class SearchTestCase(TestCase):
    """Three events whose text overlaps in awkward ways."""

    def setUp(self):
        self.uz = University.objects.create(name="University of Zimbabwe", short_name="UZ")
        self.organizer = User.objects.create_user(
            username="organizer", email="o@uz.test", password="pw"
        )
        self.jazz = Organization.objects.create(
            name="Jazz Appreciation Society", created_by=self.organizer, university=self.uz
        )
        self.debate = Organization.objects.create(
            name="Debating Union", created_by=self.organizer, university=self.uz
        )
        self.hall = Venue.objects.create(name="Beit Hall", university=self.uz)
        self.category = Category.objects.create(name="Social")

        self.gig = self.make(
            title="Midnight Jazz Party",
            summary="A late night of live music",
            description="Bring your friends and dance until dawn.",
            tags="music,jazz,social",
            organization=self.jazz,
            venue=self.hall,
        )
        self.debate_night = self.make(
            title="Inter-Varsity Debate",
            summary="NUST versus UZ",
            # 'jazz' appears only here, and only in the description — the
            # lowest weight there is. It must rank below the gig.
            description="Afterwards there will be jazz in the foyer.",
            tags="debate,competition",
            organization=self.debate,
        )
        self.quiz = self.make(
            title="Quiz Night",
            summary="General knowledge",
            description="Teams of four.",
            tags="quiz",
            organization=self.debate,
        )

    def make(self, **fields):
        now = timezone.now()
        fields.setdefault("category", self.category)
        return Event.objects.create(
            created_by=self.organizer,
            starts_at=now + timedelta(days=5),
            ends_at=now + timedelta(days=5, hours=3),
            status=Event.Status.PUBLISHED,
            **fields,
        )

    def find(self, query):
        return list(search_events(Event.objects.published(), query))


class SharedBehaviourTests(SearchTestCase):
    """True on every database this runs on."""

    def test_an_empty_query_changes_nothing(self):
        """Callers hand this whatever came off the querystring."""
        everything = Event.objects.published()

        self.assertEqual(search_events(everything, "").count(), 3)
        self.assertEqual(search_events(everything, "   ").count(), 3)
        self.assertEqual(search_events(everything, None).count(), 3)

    def test_it_finds_an_event_by_its_title(self):
        self.assertIn(self.gig, self.find("Midnight"))

    def test_it_finds_an_event_by_its_summary(self):
        self.assertIn(self.quiz, self.find("knowledge"))

    def test_it_finds_an_event_by_its_tags(self):
        self.assertIn(self.quiz, self.find("quiz"))

    def test_it_finds_an_event_by_the_society_running_it(self):
        found = self.find("Debating Union")

        self.assertIn(self.debate_night, found)
        self.assertIn(self.quiz, found)
        self.assertNotIn(self.gig, found)

    def test_it_finds_an_event_by_its_venue(self):
        self.assertIn(self.gig, self.find("Beit"))

    def test_nonsense_finds_nothing_rather_than_everything(self):
        self.assertEqual(self.find("xylophonic quantum aardvark"), [])

    def test_each_event_is_returned_once(self):
        """Matching through both the society and the venue joins can duplicate a row."""
        found = self.find("Jazz")

        self.assertEqual(len(found), len(set(e.pk for e in found)))

    def test_the_listing_page_searches(self):
        response = self.client.get(reverse("events:list"), {"q": "Midnight"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Midnight Jazz Party")
        self.assertNotContains(response, "Quiz Night")

    def test_the_palette_searches(self):
        response = self.client.get(reverse("core:quick_search"), {"q": "Midnight"})

        titles = [row["title"] for row in response.json()["results"]]
        self.assertIn("Midnight Jazz Party", titles)

    def test_a_query_that_matches_nothing_still_renders(self):
        response = self.client.get(reverse("events:list"), {"q": "zzzznothing"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "zzzznothing")


@NEEDS_PG
class RankingTests(SearchTestCase):
    """What the stored vector buys over a LIKE."""

    def test_a_title_match_outranks_a_description_match(self):
        """Both events contain 'jazz'; only one is about it."""
        found = self.find("jazz")

        self.assertIn(self.gig, found)
        self.assertIn(self.debate_night, found)
        self.assertEqual(found[0], self.gig)

    def test_it_stems(self):
        """'parties' has to find 'Party', or search feels broken to anyone typing fast."""
        self.assertIn(self.gig, self.find("parties"))
        self.assertIn(self.gig, self.find("dancing"))

    def test_a_quoted_phrase_is_treated_as_one(self):
        self.assertIn(self.gig, self.find('"midnight jazz"'))
        self.assertEqual(self.find('"jazz midnight"'), [])

    def test_a_leading_minus_excludes(self):
        found = self.find("jazz -debate")

        self.assertIn(self.gig, found)
        self.assertNotIn(self.debate_night, found)

    def test_malformed_input_does_not_raise(self):
        """This string comes straight off a querystring; websearch has to absorb it."""
        for nonsense in ('"unclosed', "and or the", "!!!", "a & b | c", "-"):
            with self.subTest(query=nonsense):
                self.assertIsInstance(self.find(nonsense), list)

    def test_results_come_back_best_match_first_by_default(self):
        response = self.client.get(reverse("events:list"), {"q": "jazz"})

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertLess(
            body.index("Midnight Jazz Party"),
            body.index("Inter-Varsity Debate"),
            "the best match should be listed first",
        )

    def test_an_explicit_sort_still_wins(self):
        response = self.client.get(reverse("events:list"), {"q": "jazz", "sort": "newest"})

        self.assertEqual(response.status_code, 200)


@NEEDS_PG
class VectorMaintenanceTests(SearchTestCase):
    def test_saving_an_event_updates_what_search_knows(self):
        self.assertEqual(self.find("harmonica"), [])

        self.gig.description = "Featuring a harmonica solo."
        self.gig.save()

        self.assertIn(self.gig, self.find("harmonica"))

    def test_every_event_gets_a_vector_on_creation(self):
        self.assertFalse(Event.objects.filter(search_vector__isnull=True).exists())

    def test_renaming_a_society_needs_a_rebuild(self):
        """The name is baked into every one of its events, and renaming re-saves none of them."""
        self.jazz.name = "Harare Swing Collective"
        self.jazz.save()

        self.assertEqual(self.find("Swing"), [], "stale by design until rebuilt")

        call_command("rebuild_search", stdout=StringIO())

        self.assertIn(self.gig, self.find("Swing"))

    def test_the_rebuild_can_be_narrowed_to_one_society(self):
        out = StringIO()
        call_command("rebuild_search", organization=self.debate.slug, stdout=out)

        self.assertIn("2 events", out.getvalue())

    def test_a_status_only_save_does_not_rebuild_the_vector(self):
        """Publishing, cancelling and picking an event all save nothing but status."""
        with CaptureQueriesContext(connection) as captured:
            self.gig.status = Event.Status.CANCELLED
            self.gig.save(update_fields=["status"])

        rebuilt = [q for q in captured.captured_queries if "search_vector" in q["sql"]]
        self.assertEqual(rebuilt, [], "a status change should not cost a reindex")

    def test_a_save_that_touches_the_text_does_rebuild_it(self):
        with CaptureQueriesContext(connection) as captured:
            self.gig.title = "Renamed Entirely"
            self.gig.save(update_fields=["title"])

        rebuilt = [q for q in captured.captured_queries if "search_vector" in q["sql"]]
        self.assertTrue(rebuilt, "the title is indexed, so saving it must reindex")
        self.assertIn(self.gig, self.find("Renamed"))

    def test_the_gin_index_is_really_there(self):
        """Without it the vector is still correct and every search is a table scan."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = %s", ["events_event"]
            )
            names = {row[0] for row in cursor.fetchall()}

        self.assertIn("event_search_vector_gin", names)

    def test_an_event_with_no_text_still_gets_a_vector(self):
        """A null vector would never match and never look wrong either."""
        bare = self.make(title="", summary="", description="", tags="", organization=self.jazz)
        bare.refresh_from_db()

        self.assertIsNotNone(bare.search_vector)


@SQLITE_ONLY
class FallbackTests(SearchTestCase):
    """SQLite gets the LIKE chain this app used everywhere before."""

    def test_it_reports_that_it_cannot_rank(self):
        self.assertFalse(supports_full_text())

    def test_refreshing_vectors_is_a_no_op(self):
        self.assertEqual(refresh_search_vectors(), 0)

    def test_matching_is_case_insensitive(self):
        self.assertIn(self.gig, self.find("MIDNIGHT"))
        self.assertIn(self.gig, self.find("midnight"))

    def test_a_partial_word_still_matches(self):
        """LIKE has no stemming, but it does have substrings."""
        self.assertIn(self.gig, self.find("Midnig"))
