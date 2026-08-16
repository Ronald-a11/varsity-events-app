"""A small, dependency-light client for the Paynow Zimbabwe API.

Paynow speaks URL-encoded form posts and returns URL-encoded bodies. Every message
is signed with a SHA-512 hash of the concatenated field values plus the integration
key, so both directions have to agree on field *order* as well as content.

Two flows are supported:

* **Redirect** (`initiate`) — we get a `browserurl` and send the student to Paynow,
  where they pick EcoCash, OneMoney, InnBucks, Zimswitch or a card.
* **Express checkout** (`initiate_express`) — we pass the mobile number and wallet
  directly, Paynow pushes a PIN prompt to the student's phone and we poll for the result.

When no integration credentials are configured the client runs in **simulation mode**:
it mints a local URL that behaves like Paynow so the whole flow can be exercised in
development. Simulation never talks to the network and is obvious in the UI.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode

import requests
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)

INITIATE_URL = "https://www.paynow.co.zw/interface/initiatetransaction"
EXPRESS_URL = "https://www.paynow.co.zw/interface/remotetransaction"

TIMEOUT = 20  # seconds


class PaynowError(Exception):
    """Paynow rejected the request, or we couldn't reach it."""


@dataclass
class PaynowResponse:
    """Normalised view of whatever Paynow just told us."""

    ok: bool
    status: str = ""
    browser_url: str = ""
    poll_url: str = ""
    instructions: str = ""
    paynow_reference: str = ""
    amount: Decimal | None = None
    error: str = ""
    raw: dict = field(default_factory=dict)


def generate_hash(values: dict, integration_key: str) -> str:
    """SHA-512 of every value in order, with the integration key appended.

    The `hash` field itself is never part of its own input.
    """
    concatenated = "".join(
        str(value) for key, value in values.items() if key.lower() != "hash"
    )
    concatenated += integration_key
    return hashlib.sha512(concatenated.encode("utf-8")).hexdigest().upper()


def verify_hash(values: dict, integration_key: str) -> bool:
    """Check a message really came from Paynow and wasn't tampered with."""
    received = values.get("hash") or values.get("Hash")
    if not received:
        return False
    expected = generate_hash(values, integration_key)
    # Constant-time compare: this guards a signature, so leaking timing is careless.
    return hmac.compare_digest(received.upper(), expected)


def _parse(body: str) -> dict:
    """Paynow answers with a URL-encoded body; order matters for the hash."""
    return dict(parse_qsl(body, keep_blank_values=True))


class PaynowClient:
    def __init__(self, integration_id: str | None = None, integration_key: str | None = None):
        # None means "read the setting"; "" means "explicitly no key".
        self.integration_id = (
            settings.PAYNOW_INTEGRATION_ID if integration_id is None else integration_id
        )
        self.integration_key = (
            settings.PAYNOW_INTEGRATION_KEY if integration_key is None else integration_key
        )

    @property
    def is_simulated(self) -> bool:
        """No credentials means we run the local stand-in instead of the real thing."""
        return not (self.integration_id and self.integration_key)

    # -- outgoing -------------------------------------------------------

    def _payload(self, *, reference, amount, additional_info, auth_email, return_url, result_url):
        # Field order is part of the signature — do not reorder.
        payload = {
            "id": self.integration_id,
            "reference": reference,
            "amount": f"{Decimal(amount):.2f}",
            "additionalinfo": additional_info,
            "returnurl": return_url,
            "resulturl": result_url,
            "authemail": auth_email,
            "status": "Message",
        }
        payload["hash"] = generate_hash(payload, self.integration_key)
        return payload

    def initiate(self, *, reference, amount, additional_info, auth_email, return_url, result_url):
        """Start a redirect payment. Returns a browser URL to send the student to."""
        if self.is_simulated:
            return self._simulate(reference, amount)

        payload = self._payload(
            reference=reference,
            amount=amount,
            additional_info=additional_info,
            auth_email=auth_email,
            return_url=return_url,
            result_url=result_url,
        )
        data = self._post(INITIATE_URL, payload)

        if data.get("status", "").lower() != "ok":
            return PaynowResponse(
                ok=False, status=data.get("status", ""), error=data.get("error", "Paynow refused the transaction."), raw=data
            )

        return PaynowResponse(
            ok=True,
            status=data.get("status", ""),
            browser_url=data.get("browserurl", ""),
            poll_url=data.get("pollurl", ""),
            raw=data,
        )

    def initiate_express(
        self, *, reference, amount, additional_info, auth_email, return_url, result_url, method, phone
    ):
        """Push a payment prompt straight to the student's mobile wallet."""
        if self.is_simulated:
            return self._simulate(reference, amount, method=method, phone=phone)

        payload = self._payload(
            reference=reference,
            amount=amount,
            additional_info=additional_info,
            auth_email=auth_email,
            return_url=return_url,
            result_url=result_url,
        )
        # Express fields sit after the standard ones, before the hash is recomputed.
        payload.pop("hash")
        payload["method"] = method
        payload["phone"] = phone
        payload["hash"] = generate_hash(payload, self.integration_key)

        data = self._post(EXPRESS_URL, payload)

        if data.get("status", "").lower() not in {"ok", "sent"}:
            return PaynowResponse(
                ok=False,
                status=data.get("status", ""),
                error=data.get("error", "Paynow could not reach that wallet."),
                raw=data,
            )

        return PaynowResponse(
            ok=True,
            status=data.get("status", ""),
            poll_url=data.get("pollurl", ""),
            instructions=data.get("instructions", "Check your phone and enter your PIN to approve."),
            raw=data,
        )

    def poll(self, poll_url: str) -> PaynowResponse:
        """Ask Paynow where a transaction has got to."""
        if not poll_url:
            return PaynowResponse(ok=False, error="No poll URL on this payment.")

        if poll_url.startswith("/") or self.is_simulated:
            # Simulated payments are polled straight from our own database.
            return PaynowResponse(ok=True, status="", raw={"simulated": "1"})

        try:
            response = requests.get(poll_url, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Paynow poll failed for %s: %s", poll_url, exc)
            return PaynowResponse(ok=False, error=str(exc))

        data = _parse(response.text)

        if not verify_hash(data, self.integration_key):
            logger.error("Paynow poll returned a bad hash for %s", poll_url)
            return PaynowResponse(ok=False, error="Paynow response failed signature checks.", raw=data)

        amount = data.get("amount")
        return PaynowResponse(
            ok=True,
            status=data.get("status", ""),
            paynow_reference=data.get("paynowreference", ""),
            poll_url=data.get("pollurl", poll_url),
            amount=Decimal(amount) if amount else None,
            raw=data,
        )

    def _post(self, url: str, payload: dict) -> dict:
        try:
            response = requests.post(
                url,
                data=urlencode(payload),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Paynow request to %s failed: %s", url, exc)
            raise PaynowError(f"Could not reach Paynow: {exc}") from exc

        return _parse(response.text)

    # -- simulation -----------------------------------------------------

    def _simulate(self, reference, amount, method="", phone="") -> PaynowResponse:
        """Stand in for Paynow so the flow works without live credentials."""
        browser_url = reverse("payments:simulator", kwargs={"reference": reference})
        if method:
            return PaynowResponse(
                ok=True,
                status="Sent",
                poll_url=browser_url,
                instructions=(
                    f"SIMULATION — no real {method.title()} prompt was sent to {phone}. "
                    f"Open the simulator to approve or decline this USD {Decimal(amount):.2f} payment."
                ),
                raw={"simulated": "1"},
            )
        return PaynowResponse(
            ok=True,
            status="Ok",
            browser_url=browser_url,
            poll_url=browser_url,
            raw={"simulated": "1"},
        )
