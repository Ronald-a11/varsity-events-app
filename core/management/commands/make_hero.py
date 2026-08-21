"""Draw the homepage hero.

    python manage.py make_hero

Same bargain as `make_icons` and the `seed_demo` artwork: generated rather than
sourced, so the repo carries no image nobody can regenerate, there is no
licence to honour, and re-running it produces the same bytes and no spurious
diff. Commit what it writes — the production image installs no Pillow at build
time, exactly as with `static/css/app.css`.

The picture is the product's sentence in one frame: eighteen universities, the
eight kinds of thing they put on, all of it arriving in one place. It replaced
a stock photograph of a concert crowd, which said "music night" about a
platform that is also careers fairs, moot courts and blood drives — and which
was somebody else's campus standing in for a Zimbabwean one.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.imagegen import make_hero

OUTPUT = "hero-universities.jpg"


class Command(BaseCommand):
    help = "Draw the homepage hero into static/img/."

    def add_arguments(self, parser):
        parser.add_argument(
            "--width", type=int, default=2400, help="Pixels across (default 2400)."
        )
        parser.add_argument(
            "--height", type=int, default=1000, help="Pixels down (default 1000)."
        )

    def handle(self, *args, **options):
        target = Path(settings.BASE_DIR) / "static" / "img" / OUTPUT
        target.parent.mkdir(parents=True, exist_ok=True)

        buffer = make_hero(size=(options["width"], options["height"]))
        payload = buffer.getvalue()
        target.write_bytes(payload)

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote {target.relative_to(settings.BASE_DIR)} "
                f"({len(payload) // 1024} KB, {options['width']}x{options['height']})"
            )
        )
        self.stdout.write("Commit it — nothing regenerates this at deploy time.")
