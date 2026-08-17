"""Prove the mail settings work, before a student's ticket depends on them.

Because sending is deliberately best-effort everywhere else, a broken mail
server is quiet — tickets still confirm, nothing 500s, and no email arrives.
This is the thing that makes that failure loud.
"""

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand, CommandError

from core.mail import send_mail


class Command(BaseCommand):
    help = "Check the email configuration and optionally send a test message."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            help="Send a real test email to this address.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Configuration"))

        backend = settings.EMAIL_BACKEND.rsplit(".", 2)[-2]
        is_console = "console" in settings.EMAIL_BACKEND

        self._line("Backend", not is_console, backend)
        self._line("EMAIL_HOST", bool(settings.EMAIL_HOST), settings.EMAIL_HOST or "not set")
        self._line("EMAIL_HOST_USER", bool(settings.EMAIL_HOST_USER), settings.EMAIL_HOST_USER or "not set")
        # Never print the password — only whether there is one.
        self._line("EMAIL_HOST_PASSWORD", bool(settings.EMAIL_HOST_PASSWORD), "set" if settings.EMAIL_HOST_PASSWORD else "not set")
        self._line("Port / TLS", True, f"{settings.EMAIL_PORT}, TLS={settings.EMAIL_USE_TLS}, SSL={settings.EMAIL_USE_SSL}")
        self._line("From", True, settings.DEFAULT_FROM_EMAIL)
        self._line("Links point at", True, settings.SITE_BASE_URL)

        if is_console:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "No EMAIL_HOST is set, so mail is printed to the console and nothing "
                    "reaches a real inbox. Set EMAIL_HOST, EMAIL_HOST_USER and "
                    "EMAIL_HOST_PASSWORD to send for real."
                )
            )
            return

        # Django's SMTP backend only calls login() when it has BOTH a username
        # and a password. With one missing it opens the socket, authenticates
        # nothing, and looks perfectly healthy — then every real send is
        # rejected. Catch that here rather than in production.
        if settings.EMAIL_HOST_USER and not settings.EMAIL_HOST_PASSWORD:
            self.stdout.write("")
            raise CommandError(
                "EMAIL_HOST_USER is set but EMAIL_HOST_PASSWORD is empty. Opening a "
                "connection would succeed without ever logging in, so this is a "
                "failure, not a pass. Set the password and run this again."
            )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Connection"))

        connection = get_connection()
        try:
            connection.open()
            connection.close()
        except Exception as exc:  # noqa: BLE001 — surface whatever the server said
            self._line("Authenticate with the mail server", False, str(exc))
            raise CommandError(
                "Could not log in. Check the host, port and credentials — for Gmail "
                "this must be a 16-character App Password, not your account password."
            )

        self._line(
            "Authenticate with the mail server",
            True,
            f"logged in to {settings.EMAIL_HOST} as {settings.EMAIL_HOST_USER}",
        )

        recipient = options.get("to")
        if not recipient:
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS(
                    "Settings are good. Add --to you@example.com to send a real test email."
                )
            )
            return

        self.stdout.write("")
        sent = send_mail(
            to=recipient,
            subject=f"{settings.SITE_NAME} email test",
            template="test",
            context={"base_url": settings.SITE_BASE_URL},
        )

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Test email sent to {recipient}."))
        else:
            raise CommandError(
                "The send failed. The traceback is in the log — core.mail swallows "
                "the exception so a payment can't be broken by a mail server."
            )

    def _line(self, label, ok, detail=""):
        mark = self.style.SUCCESS("OK  ") if ok else self.style.ERROR("--  ")
        suffix = f"  ({detail})" if detail else ""
        self.stdout.write(f"  {mark}{label}{suffix}")
