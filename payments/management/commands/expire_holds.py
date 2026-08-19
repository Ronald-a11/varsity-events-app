"""Release seats held by checkouts nobody finished.

The task cluster runs this every minute (see core.apps). This command is the
same job on demand — for a deploy that runs a platform cron instead of a
worker process, or for checking by hand what a sweep would do.
"""

from django.core.management.base import BaseCommand

from payments.tasks import release_abandoned_holds


class Command(BaseCommand):
    help = "Cancel timed-out checkouts and promote the waitlist into the freed seats."

    def handle(self, *args, **options):
        released = release_abandoned_holds()

        if not released:
            self.stdout.write("Nothing to release — no checkout has timed out.")
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Released {released} abandoned checkout{'' if released == 1 else 's'}."
            )
        )
