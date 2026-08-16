import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

# How long a seat is held while the student completes payment. Paynow mobile
# prompts time out well inside this, and it stops abandoned checkouts from
# locking up a sold-out event forever.
HOLD_MINUTES = 30


def generate_payment_reference():
    """Merchant reference sent to Paynow, e.g. VE-PAY-7K2M9QX4."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "VE-PAY-" + "".join(secrets.choice(alphabet) for _ in range(8))


class Payment(models.Model):
    """One attempt to pay for one registration through Paynow."""

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        SENT = "sent", "Sent to Paynow"
        AWAITING_TRANSFER = "awaiting_transfer", "Waiting for the EcoCash transfer"
        AWAITING_VERIFICATION = "awaiting_verification", "Code submitted — awaiting check"
        PAID = "paid", "Paid"
        AWAITING_DELIVERY = "awaiting_delivery", "Paid — awaiting delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        DISPUTED = "disputed", "Disputed"
        EXPIRED = "expired", "Expired"

    class Gateway(models.TextChoices):
        PESEPAY = "pesepay", "Pesepay"
        PAYNOW = "paynow", "Paynow"
        DIRECT = "direct", "Direct transfer"

    class Method(models.TextChoices):
        ECOCASH_DIRECT = "ecocash_direct", "EcoCash — send straight to us"
        WEB = "web", "Card, Zimswitch or any wallet"
        ECOCASH = "ecocash", "EcoCash"
        ONEMONEY = "onemoney", "OneMoney"
        INNBUCKS = "innbucks", "InnBucks"

    # Methods where a human, not a gateway callback, decides the money arrived.
    MANUAL_METHODS = {Method.ECOCASH_DIRECT}

    # Paynow's own vocabulary, mapped onto ours.
    PAYNOW_STATUS_MAP = {
        "created": Status.CREATED,
        "sent": Status.SENT,
        "paid": Status.PAID,
        "awaiting delivery": Status.AWAITING_DELIVERY,
        "delivered": Status.DELIVERED,
        "cancelled": Status.CANCELLED,
        "failed": Status.FAILED,
        "refunded": Status.REFUNDED,
        "disputed": Status.DISPUTED,
    }

    # Statuses where the money is genuinely with the merchant.
    SETTLED_STATUSES = {Status.PAID, Status.AWAITING_DELIVERY, Status.DELIVERED}

    registration = models.ForeignKey(
        "events.Registration", on_delete=models.CASCADE, related_name="payments"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments"
    )

    gateway = models.CharField(
        max_length=20,
        choices=Gateway.choices,
        default=Gateway.PESEPAY,
        help_text="Which processor handled this payment.",
    )
    reference = models.CharField(
        max_length=32, unique=True, default=generate_payment_reference, editable=False
    )
    # The processor's own reference. Named for Paynow historically; Pesepay's
    # referenceNumber lives here too, which is what status checks are keyed on.
    paynow_reference = models.CharField(max_length=64, blank=True)
    poll_url = models.URLField(max_length=500, blank=True)
    browser_url = models.URLField(max_length=500, blank=True)

    amount = models.DecimalField(max_digits=9, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.WEB)
    phone = models.CharField(max_length=30, blank=True)

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.CREATED)
    instructions = models.TextField(blank=True)
    error = models.CharField(max_length=255, blank=True)

    # Direct EcoCash transfers: the payer types back the code EcoCash sent them,
    # and an organizer checks it against the wallet statement before confirming.
    confirmation_code = models.CharField(
        max_length=40, blank=True, help_text="EcoCash transaction code supplied by the payer"
    )
    paid_from = models.CharField(
        max_length=30, blank=True, help_text="Mobile number the transfer came from"
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments_verified",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True)
    is_simulated = models.BooleanField(
        default=False, help_text="Created without live Paynow credentials."
    )

    expires_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.reference} · {self.currency} {self.amount} · {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            # A hand-typed transfer needs longer than a gateway push.
            minutes = (
                settings.ECOCASH_DIRECT_HOLD_MINUTES
                if self.method in self.MANUAL_METHODS
                else HOLD_MINUTES
            )
            self.expires_at = timezone.now() + timezone.timedelta(minutes=minutes)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("payments:status", kwargs={"reference": self.reference})

    # -- state ----------------------------------------------------------

    @property
    def is_settled(self):
        return self.status in self.SETTLED_STATUSES

    OPEN_STATUSES = {
        Status.CREATED,
        Status.SENT,
        Status.AWAITING_TRANSFER,
        Status.AWAITING_VERIFICATION,
    }

    @property
    def is_open(self):
        """Still in flight — not finished, not timed out."""
        return self.status in self.OPEN_STATUSES and not self.has_expired

    @property
    def is_manual(self):
        return self.method in self.MANUAL_METHODS

    @property
    def gateway_reference(self):
        """The processor's reference, whichever processor it was."""
        return self.paynow_reference

    def apply_pesepay_status(self, status: str, reference: str = "") -> bool:
        """Move this payment on from one of Pesepay's transaction statuses."""
        from .pesepay import classify

        verdict = classify(status)
        if not verdict:
            return False

        mapped = {
            "paid": self.Status.PAID,
            "pending": self.Status.SENT,
            "failed": self.Status.FAILED,
        }[verdict]

        # Backing out isn't the same as a failure, and the payer deserves to be
        # told which one happened.
        if status.strip().upper() == "CANCELLED":
            mapped = self.Status.CANCELLED

        changed = False
        if reference and reference != self.paynow_reference:
            self.paynow_reference = reference
            changed = True

        if mapped != self.status:
            self.status = mapped
            changed = True

        if mapped in self.SETTLED_STATUSES and not self.paid_at:
            self.paid_at = timezone.now()
            changed = True

        self.last_polled_at = timezone.now()
        self.save()
        return changed

    @property
    def needs_verification(self):
        return self.status == self.Status.AWAITING_VERIFICATION

    @property
    def has_expired(self):
        return bool(self.expires_at and timezone.now() > self.expires_at)

    @property
    def amount_display(self):
        return f"{self.currency} {self.amount:,.2f}"

    @property
    def is_wallet_push(self):
        """A prompt sent to the payer's handset, waiting on their PIN."""
        return self.gateway == self.Gateway.PESEPAY and self.method not in {
            self.Method.WEB,
            self.Method.ECOCASH_DIRECT,
        }

    @property
    def phone_masked(self):
        """0771234567 -> 077 *** 4567. Enough to recognise, not enough to leak."""
        digits = "".join(c for c in self.phone if c.isdigit())
        if len(digits) < 7:
            return self.phone
        return f"{digits[:3]} *** {digits[-4:]}"

    @property
    def seconds_left(self):
        """How long the seat stays held. Zero once it's gone."""
        if not self.expires_at:
            return 0
        return max(0, int((self.expires_at - timezone.now()).total_seconds()))

    @property
    def method_icon(self):
        return {
            self.Method.ECOCASH_DIRECT: "📱",
            self.Method.ECOCASH: "📱",
            self.Method.ONEMONEY: "📲",
            self.Method.INNBUCKS: "🏦",
        }.get(self.method, "💳")

    # -- transitions ----------------------------------------------------

    def apply_paynow_status(self, paynow_status: str, paynow_reference: str = "") -> bool:
        """Move this payment to whatever Paynow says it is. Returns True if it changed.

        Confirming the registration is the caller's job via `settle()`, so a status
        write and a seat confirmation never half-happen.
        """
        mapped = self.PAYNOW_STATUS_MAP.get((paynow_status or "").strip().lower())
        if mapped is None:
            return False

        changed = False
        if paynow_reference and paynow_reference != self.paynow_reference:
            self.paynow_reference = paynow_reference
            changed = True

        if mapped != self.status:
            self.status = mapped
            changed = True

        if mapped in self.SETTLED_STATUSES and not self.paid_at:
            self.paid_at = timezone.now()
            changed = True

        self.last_polled_at = timezone.now()
        self.save()
        return changed

    def settle(self):
        """Turn a paid transaction into a confirmed ticket."""
        from activity.models import Activity, record
        from events.models import Registration

        if not self.is_settled:
            return False

        registration = self.registration
        if registration.status == Registration.Status.CONFIRMED:
            return False

        registration.status = Registration.Status.CONFIRMED
        registration.save(update_fields=["status"])

        record(
            Activity.Verb.PAID,
            actor=self.user,
            event=registration.event,
            amount=self.amount,
            is_simulated=self.is_simulated,
        )

        # The ticket is already saved. Mail is best-effort by design — see
        # core.mail — so a mail server having a bad day can't undo a payment.
        from core.mail import send_payment_receipt, send_ticket_confirmed

        send_ticket_confirmed(registration)
        send_payment_receipt(self)
        return True

    def submit_confirmation(self, code, paid_from=""):
        """The payer says they've sent the money and gives their EcoCash code."""
        self.confirmation_code = code.strip().upper()
        self.paid_from = paid_from.strip()
        self.status = self.Status.AWAITING_VERIFICATION
        self.save(
            update_fields=["confirmation_code", "paid_from", "status", "updated_at"]
        )

        # Nobody is watching a queue page. Tell the organizers there's money to
        # match, or the student waits on a ticket that nothing will release.
        from core.mail import send_transfer_awaiting_organizer

        send_transfer_awaiting_organizer(self)
        return self

    def verify(self, by_user):
        """An organizer has matched the code against the wallet. Release the ticket."""
        if self.status not in {self.Status.AWAITING_VERIFICATION, self.Status.AWAITING_TRANSFER}:
            return False

        self.status = self.Status.PAID
        self.paid_at = timezone.now()
        self.verified_by = by_user
        self.verified_at = timezone.now()
        self.rejection_reason = ""
        self.save(
            update_fields=[
                "status",
                "paid_at",
                "verified_by",
                "verified_at",
                "rejection_reason",
                "updated_at",
            ]
        )
        self.settle()
        return True

    def reject(self, by_user, reason=""):
        """No matching transfer found. Send the payer back to try again."""
        if self.status != self.Status.AWAITING_VERIFICATION:
            return False

        rejected_code = self.confirmation_code

        self.status = self.Status.AWAITING_TRANSFER
        self.confirmation_code = ""
        self.rejection_reason = reason or "We couldn't find that transfer. Please check the code."
        self.verified_by = by_user
        self.save(
            update_fields=[
                "status",
                "confirmation_code",
                "rejection_reason",
                "verified_by",
                "updated_at",
            ]
        )

        # The email quotes the code they gave us — usually the culprit — and the
        # field above has just been cleared, so pass it through explicitly.
        from core.mail import send_transfer_rejected

        send_transfer_rejected(self, rejected_code=rejected_code)
        return True

    def expire(self):
        """Give up on an abandoned checkout and release the held seat."""
        from events.models import Registration

        if self.status not in self.OPEN_STATUSES:
            return False

        self.status = self.Status.EXPIRED
        self.save(update_fields=["status", "updated_at"])

        registration = self.registration
        if registration.status == Registration.Status.AWAITING_PAYMENT:
            registration.status = Registration.Status.CANCELLED
            registration.cancelled_at = timezone.now()
            registration.save(update_fields=["status", "cancelled_at"])
            registration.event.promote_from_waitlist()
        return True

    def mark_refunded(self):
        self.status = self.Status.REFUNDED
        self.save(update_fields=["status", "updated_at"])


def expire_stale_payments(event=None):
    """Release seats held by checkouts nobody finished.

    Called lazily whenever we're about to read capacity, so an abandoned payment
    can never permanently occupy a place.
    """
    stale = Payment.objects.filter(
        status__in=Payment.OPEN_STATUSES, expires_at__lt=timezone.now()
    )
    if event is not None:
        stale = stale.filter(registration__event=event)

    released = 0
    for payment in stale.select_related("registration", "registration__event"):
        released += int(payment.expire())
    return released


def total_collected(events):
    """Sum of settled payments across a set of events, for organizer dashboards."""
    result = Payment.objects.filter(
        registration__event__in=events, status__in=Payment.SETTLED_STATUSES
    ).aggregate(total=models.Sum("amount"))
    return result["total"] or Decimal("0")
