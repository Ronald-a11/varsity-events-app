"""Client for the Pesepay payments engine (https://pesepay.com).

Pesepay wraps every request and response body in a single `payload` field that
is AES-encrypted with the merchant's encryption key, and authenticates with a
separate integration key sent in the `authorization` header.

The cipher matches Pesepay's own SDKs: **AES-CBC with PKCS7 padding**, the key
being the raw UTF-8 bytes of the encryption key and the IV its first 16
characters, base64-encoded. A 32-character key therefore means AES-256.

As with the previous gateway, when no credentials are configured the client
runs in **simulation mode**: it mints a local URL that behaves like Pesepay so
the whole flow is exercisable offline. Simulation never touches the network.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)

BASE_URL = "https://api.pesepay.com/api/payments-engine"
TIMEOUT = 25  # seconds

# Pesepay's transaction statuses, mapped onto ours. Anything absent is ignored.
SUCCESS_STATUSES = {"SUCCESS", "PARTIALLY_PAID"}
PENDING_STATUSES = {"INITIATED", "PENDING", "PROCESSING"}
FAILED_STATUSES = {
    "AUTHORIZATION_FAILED",
    "CANCELLED",
    "CLOSED",
    "CLOSED_PERIOD_ELAPSED",
    "DECLINED",
    "ERROR",
    "FAILED",
    "INSUFFICIENT_FUNDS",
    "REVERSED",
    "SERVICE_UNAVAILABLE",
    "TERMINATED",
}


class PesepayError(Exception):
    """Pesepay rejected the request, or we couldn't reach it."""


@dataclass
class PesepayResponse:
    """Normalised view of a Pesepay transaction."""

    ok: bool
    status: str = ""
    reference: str = ""            # Pesepay's referenceNumber
    redirect_url: str = ""
    poll_url: str = ""
    instructions: str = ""
    amount: Decimal | None = None
    error: str = ""
    raw: dict = field(default_factory=dict)


class PesepayCrypto:
    """AES-CBC/PKCS7 over the `payload` envelope."""

    # AES only accepts these key sizes, and the IV needs 16 characters to slice.
    VALID_KEY_LENGTHS = (16, 24, 32)

    def __init__(self, encryption_key: str):
        self.key = encryption_key.encode("utf-8")
        # Pesepay derives the IV from the first 16 characters of the same key.
        self.iv = encryption_key[:16].encode("utf-8")

    def encrypt(self, data: dict) -> str:
        # Compact separators, to match what JSON.stringify produces in Pesepay's
        # own SDKs. Whitespace wouldn't break decoding, but keeping the bytes
        # identical means a captured payload can be compared against theirs.
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")

        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(raw) + padder.finalize()

        encryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()

    def decrypt(self, payload: str) -> dict:
        decryptor = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        padded = decryptor.update(base64.b64decode(payload)) + decryptor.finalize()

        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        return json.loads(unpadder.update(padded) + unpadder.finalize())


class PesepayClient:
    def __init__(self, integration_key: str | None = None, encryption_key: str | None = None):
        # None means "read the setting"; "" means "explicitly no key", which a
        # falsy-or would have silently overridden with the configured one.
        self.integration_key = (
            settings.PESEPAY_INTEGRATION_KEY if integration_key is None else integration_key
        )
        self.encryption_key = (
            settings.PESEPAY_ENCRYPTION_KEY if encryption_key is None else encryption_key
        )

        # A key of the wrong length can't encrypt anything, so catch it here rather
        # than letting AES blow up mid-checkout with a student waiting on the page.
        self.key_is_usable = len(self.encryption_key) in PesepayCrypto.VALID_KEY_LENGTHS
        if self.encryption_key and not self.key_is_usable:
            logger.error(
                "PESEPAY_ENCRYPTION_KEY is %d characters; Pesepay keys are 32. "
                "Falling back to the checkout simulator.",
                len(self.encryption_key),
            )

        self._crypto = PesepayCrypto(self.encryption_key) if self.key_is_usable else None

    @property
    def is_simulated(self) -> bool:
        """No usable credentials means we run the local stand-in, not the real thing."""
        return not (self.integration_key and self.key_is_usable)

    # -- outgoing -------------------------------------------------------

    def initiate(self, *, amount, currency, reason, return_url, result_url, reference=""):
        """Start a redirect payment. Returns a URL to send the payer to."""
        if self.is_simulated:
            return self._simulate(reference, amount)

        body = {
            "amountDetails": {"amount": float(amount), "currencyCode": currency},
            "reasonForPayment": reason,
            "resultUrl": result_url,
            "returnUrl": return_url,
        }
        if reference:
            body["merchantReference"] = reference

        data = self._post("v1/payments/initiate", body)
        return self._as_response(data)

    def make_seamless_payment(
        self, *, amount, currency, reason, result_url, method_code, phone,
        email="", name="GUEST", reference="", required_fields=None,
    ):
        """Push a prompt straight to the payer's wallet, no redirect."""
        if self.is_simulated:
            return self._simulate(reference, amount, method=method_code, phone=phone)

        body = {
            "amountDetails": {"amount": float(amount), "currencyCode": currency},
            "merchantReference": reference,
            "reasonForPayment": reason,
            "resultUrl": result_url,
            "paymentMethodCode": method_code,
            "customer": {"phoneNumber": phone, "email": email, "name": name or "GUEST"},
            # Wallets want the number again under their own field name.
            "paymentMethodRequiredFields": required_fields or {"customerPhoneNumber": phone},
        }

        data = self._post("v2/payments/make-payment", body)
        return self._as_response(data)

    def check_payment(self, reference_number: str) -> PesepayResponse:
        """Ask Pesepay where a transaction has got to."""
        if not reference_number:
            return PesepayResponse(ok=False, error="No Pesepay reference on this payment.")
        if self.is_simulated:
            return PesepayResponse(ok=True, raw={"simulated": "1"})

        try:
            response = requests.get(
                f"{BASE_URL}/v1/payments/check-payment",
                params={"referenceNumber": reference_number},
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Pesepay status check failed for %s: %s", reference_number, exc)
            return PesepayResponse(ok=False, error=str(exc))

        return self._as_response(self._unwrap(response))

    def verify_credentials(self) -> PesepayResponse:
        """Prove the integration key is really ours, not just that Pesepay is up.

        The method list is public — it answers with any key at all, so it can't
        tell a valid account from a wrong one. Asking after a transaction that
        can't exist does: a good key gets 'not found', a bad one gets refused.
        """
        if self.is_simulated:
            return PesepayResponse(ok=False, error="No credentials configured.")

        probe = "VE-CREDENTIAL-CHECK-0000"
        try:
            response = requests.get(
                f"{BASE_URL}/v1/payments/check-payment",
                params={"referenceNumber": probe},
                headers=self._headers(),
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            return PesepayResponse(ok=False, error=f"Could not reach Pesepay: {exc}")

        if response.status_code in (401, 403):
            return PesepayResponse(
                ok=False,
                error="Pesepay rejected the integration key (HTTP %d)." % response.status_code,
                raw={"status_code": response.status_code},
            )

        try:
            body = response.json()
        except ValueError:
            return PesepayResponse(
                ok=False, error=f"Pesepay returned an unreadable response (HTTP {response.status_code})."
            )

        message = str(body.get("message") or body.get("error") or "")
        lowered = message.lower()

        # An unknown key comes back as HTTP 404 "Integration key record was not
        # found" — not a 401, which is why this has to match on the message.
        # Verified by pointing the check at a deliberately wrong key.
        rejected = (
            "integration key" in lowered
            or any(w in lowered for w in ("authoriz", "authentic", "credential", "forbidden"))
        )
        if rejected:
            return PesepayResponse(ok=False, error=message or "Key rejected.", raw=body)

        # Anything else — including "transaction not found" — means the key was
        # accepted and Pesepay got as far as looking the reference up.
        return PesepayResponse(ok=True, error=message, raw=body)

    def payment_methods(self, currency="USD"):
        """Live method list, so the checkout can offer what the account supports."""
        if self.is_simulated:
            return []
        try:
            response = requests.get(
                f"{BASE_URL}/v1/payment-methods/for-currency",
                params={"currencyCode": currency},
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Could not list Pesepay methods: %s", exc)
            return []

    # -- plumbing -------------------------------------------------------

    def _headers(self):
        return {"authorization": self.integration_key, "content-type": "application/json"}

    def _post(self, path, body) -> dict:
        try:
            response = requests.post(
                f"{BASE_URL}/{path}",
                json={"payload": self._crypto.encrypt(body)},
                headers=self._headers(),
                timeout=TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Pesepay request to %s failed: %s", path, exc)
            raise PesepayError(f"Could not reach Pesepay: {exc}") from exc

        return self._unwrap(response)

    def _unwrap(self, response) -> dict:
        """Pull the transaction out of the encrypted envelope.

        Errors come back as plain JSON with a `message`, not an encrypted
        payload, so handle both shapes.
        """
        try:
            body = response.json()
        except ValueError as exc:
            raise PesepayError("Pesepay returned a response we couldn't read.") from exc

        payload = body.get("payload")
        if not payload:
            return {"__error": body.get("message") or body.get("error") or "Pesepay declined the request."}

        try:
            return self._crypto.decrypt(payload)
        except Exception as exc:  # bad key, truncated body, changed cipher
            logger.error("Could not decrypt the Pesepay payload: %s", exc)
            raise PesepayError(
                "Could not decrypt Pesepay's response — check PESEPAY_ENCRYPTION_KEY."
            ) from exc

    def _as_response(self, data: dict) -> PesepayResponse:
        if "__error" in data:
            return PesepayResponse(ok=False, error=data["__error"], raw=data)

        amount = (data.get("amountDetails") or {}).get("amount")
        details = data.get("paymentMethodDetails") or {}

        return PesepayResponse(
            ok=True,
            status=data.get("transactionStatus", ""),
            reference=data.get("referenceNumber", ""),
            redirect_url=data.get("redirectUrl", "") or "",
            poll_url=data.get("pollUrl", "") or "",
            instructions=(
                details.get("paymentMethodMessage")
                or data.get("transactionStatusDescription", "")
                or ""
            ),
            amount=Decimal(str(amount)) if amount is not None else None,
            raw=data,
        )

    # -- simulation -----------------------------------------------------

    def _simulate(self, reference, amount, method="", phone="") -> PesepayResponse:
        """Stand in for Pesepay so the flow works without live credentials."""
        url = reverse("payments:simulator", kwargs={"reference": reference})

        if method:
            return PesepayResponse(
                ok=True,
                status="PENDING",
                reference=f"SIM-{reference}",
                poll_url=url,
                instructions=(
                    f"SIMULATION — no real {method} prompt was sent to {phone}. "
                    f"Open the simulator to approve or decline this "
                    f"{Decimal(amount):.2f} payment."
                ),
                raw={"simulated": "1"},
            )

        return PesepayResponse(
            ok=True,
            status="INITIATED",
            reference=f"SIM-{reference}",
            redirect_url=url,
            poll_url=url,
            raw={"simulated": "1"},
        )


def classify(status: str) -> str:
    """'paid' | 'pending' | 'failed' | '' for one of Pesepay's status strings."""
    value = (status or "").strip().upper()
    if value in SUCCESS_STATUSES:
        return "paid"
    if value in PENDING_STATUSES:
        return "pending"
    if value in FAILED_STATUSES:
        return "failed"
    return ""
