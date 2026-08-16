"""Confirm the Pesepay credentials in .env actually work.

Run this the moment you paste your keys in, rather than finding out from a
student stuck on a payment page. It only reads — no transaction is created and
no money moves.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from payments.pesepay import PesepayCrypto, PesepayClient


class Command(BaseCommand):
    help = "Check the configured Pesepay credentials against the live API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--currency",
            default="USD",
            help="Currency to list payment methods for (default USD).",
        )

    def handle(self, *args, **options):
        client = PesepayClient()

        self.stdout.write(self.style.MIGRATE_HEADING("Credentials"))
        self._report_keys(client)

        if client.is_simulated:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "Checkout is running the local simulator. No real payment can be "
                    "taken until both keys above are set and the encryption key is 32 "
                    "characters."
                )
            )
            return

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Live API"))

        # Do this first: the method list answers to any key, so a green there
        # proves nothing about whether the account is actually yours.
        verdict = client.verify_credentials()
        self._line(
            "Integration key accepted",
            verdict.ok,
            verdict.error if not verdict.ok else "",
        )
        if not verdict.ok:
            self.stdout.write("")
            self.stdout.write(
                self.style.ERROR(
                    "These credentials are not valid for Pesepay. Checkout would accept "
                    "payments and every one of them would fail. Check that the keys came "
                    "from the Pesepay dashboard — not another gateway's."
                )
            )
            return

        self._report_methods(client, options["currency"])

    # -- pieces ---------------------------------------------------------

    def _report_keys(self, client):
        integration = settings.PESEPAY_INTEGRATION_KEY
        encryption = settings.PESEPAY_ENCRYPTION_KEY

        # Never print the keys themselves — a terminal transcript is not a vault.
        self._line("Integration key", bool(integration), f"{len(integration)} characters")

        if not encryption:
            self._line("Encryption key", False, "not set")
        elif client.key_is_usable:
            self._line("Encryption key", True, f"{len(encryption)} characters")
        else:
            self._line(
                "Encryption key",
                False,
                f"{len(encryption)} characters — Pesepay keys are "
                f"{PesepayCrypto.VALID_KEY_LENGTHS[-1]}",
            )

        if client.key_is_usable:
            # Proves the cipher is happy with this key before we ever depend on it.
            try:
                crypto = PesepayCrypto(settings.PESEPAY_ENCRYPTION_KEY)
                probe = {"check": "varsity-events"}
                ok = crypto.decrypt(crypto.encrypt(probe)) == probe
            except Exception as exc:  # noqa: BLE001 — any failure means the key is unusable
                ok = False
                self.stderr.write(f"  cipher error: {exc}")
            self._line("Encrypt / decrypt round trip", ok, "" if ok else "cipher rejected the key")

    def _report_methods(self, client, currency):
        methods = client.payment_methods(currency)

        if not methods:
            self._line(
                "Reach Pesepay",
                False,
                "no method list came back — check the key, or your connection",
            )
            self.stdout.write("")
            self.stdout.write(
                self.style.ERROR(
                    "Could not confirm these credentials. Checkout will still accept "
                    "payments, but every attempt would fail the same way."
                )
            )
            return

        self._line("Reach Pesepay", True, f"{len(methods)} method(s) for {currency}")
        self.stdout.write("")

        live_codes = {}
        for method in methods:
            code = method.get("code") or method.get("paymentMethodCode") or ""
            if code:
                live_codes[code] = method.get("name") or method.get("displayName") or ""

        for code, name in sorted(live_codes.items()):
            self.stdout.write(f"  {code:<10} {name}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Your configured wallet codes"))

        wrong = []
        for wallet, code in settings.PESEPAY_METHOD_CODES.items():
            if not code:
                # Blank is a deliberate "this account can't take it", not a mistake.
                self.stdout.write(f"  {self.style.WARNING('off ')}{wallet:<9} not offered to students")
                continue
            matched = code in live_codes
            if not matched:
                wrong.append((wallet, code))
            self._line(
                f"{wallet:<9} {code}",
                matched,
                live_codes.get(code, "no such code on this account"),
            )

        self.stdout.write("")
        if wrong:
            names = ", ".join(f"PESEPAY_CODE_{w.upper()}" for w, _ in wrong)
            self.stdout.write(
                self.style.ERROR(
                    f"{names} point at codes this account doesn't have. Set them to a "
                    "code from the list above, or blank to stop offering that wallet — "
                    "otherwise students picking it will be declined."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("Pesepay is configured and reachable."))

    def _line(self, label, ok, detail=""):
        mark = self.style.SUCCESS("OK  ") if ok else self.style.ERROR("--  ")
        suffix = f"  ({detail})" if detail else ""
        self.stdout.write(f"  {mark}{label}{suffix}")
