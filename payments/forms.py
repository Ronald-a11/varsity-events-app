import re

from django import forms
from django.conf import settings

from core.forms import TailwindFormMixin

from .models import Payment

# Zimbabwean mobile numbers: 07x xxx xxxx, optionally +263 / 263 prefixed.
ZW_MOBILE = re.compile(r"^(?:\+?263|0)(7[1378])\d{7}$")

# Which prefixes belong to which wallet, so we can catch obvious mismatches early
# rather than after the gateway has already rejected the push.
WALLET_PREFIXES = {
    Payment.Method.ECOCASH: {"77", "78"},
    Payment.Method.ONEMONEY: {"71"},
}


class CheckoutForm(TailwindFormMixin, forms.Form):
    method = forms.ChoiceField(
        choices=Payment.Method.choices,
        initial=Payment.Method.ECOCASH_DIRECT,
        widget=forms.RadioSelect,
        label="How would you like to pay?",
    )
    phone = forms.CharField(
        required=False,
        label="Mobile number",
        help_text="The wallet we should push the payment prompt to.",
        widget=forms.TextInput(attrs={"placeholder": "0771 234 567", "inputmode": "tel"}),
    )

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").replace(" ", "").replace("-", "")
        return phone

    # Wallets need a method code from the merchant account; the hosted page and the
    # direct transfer don't, so they're always on offer.
    CODELESS_METHODS = {Payment.Method.WEB, Payment.Method.ECOCASH_DIRECT}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        choices = [
            choice
            for choice in Payment.Method.choices
            if self._is_offered(choice[0])
        ]
        self.fields["method"].choices = choices

        available = {code for code, _ in choices}
        if self.fields["method"].initial not in available:
            self.fields["method"].initial = choices[0][0] if choices else None

    def _is_offered(self, method):
        """Hide anything this deployment can't actually collect."""
        if method == Payment.Method.ECOCASH_DIRECT:
            # Without a wallet number there is nowhere to tell students to send
            # the money, so don't offer it.
            return bool(settings.ECOCASH_DIRECT_ENABLED and settings.ECOCASH_MERCHANT_NUMBER)
        if method in self.CODELESS_METHODS:
            return True
        # A wallet with no code would be rejected by Pesepay at the push, so
        # don't put it in front of a student in the first place.
        return bool(settings.PESEPAY_METHOD_CODES.get(method))

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("method")
        phone = cleaned.get("phone", "")

        # The redirect flow collects payment details on Pesepay's own page, and a
        # direct transfer is initiated by the student on their own handset.
        if method in self.CODELESS_METHODS:
            return cleaned

        if not phone:
            self.add_error("phone", "Enter the mobile number to send the prompt to.")
            return cleaned

        match = ZW_MOBILE.match(phone)
        if not match:
            self.add_error("phone", "Enter a valid Zimbabwean mobile number, e.g. 0771 234 567.")
            return cleaned

        prefix = match.group(1)  # '77' from '0771234567'
        expected = WALLET_PREFIXES.get(method)
        if expected and prefix not in expected:
            label = dict(Payment.Method.choices)[method]
            self.add_error("phone", f"That number doesn't look like a {label} line.")

        # Normalise to the 07xxxxxxxx form the gateway expects.
        cleaned["phone"] = "0" + match.group(1) + phone[-7:]
        return cleaned


class ConfirmTransferForm(TailwindFormMixin, forms.Form):
    """What the student types back after sending the money themselves."""

    confirmation_code = forms.CharField(
        max_length=40,
        label="EcoCash confirmation code",
        help_text="The code in the SMS EcoCash sent you, e.g. MP240816.1423.A12345",
        widget=forms.TextInput(
            attrs={
                "placeholder": "MP240816.1423.A12345",
                "autocomplete": "off",
                "autocapitalize": "characters",
                "autofocus": "autofocus",
                "class": "font-mono uppercase tracking-wide",
            }
        ),
    )
    paid_from = forms.CharField(
        max_length=30,
        required=False,
        label="Number you paid from",
        help_text="Optional, but it makes checking your payment much faster.",
        widget=forms.TextInput(attrs={"placeholder": "0771 234 567", "inputmode": "tel"}),
    )

    def clean_confirmation_code(self):
        code = self.cleaned_data["confirmation_code"].strip().upper()
        if len(code) < 6:
            raise forms.ValidationError("That code looks too short — check the SMS again.")
        return code

    def clean_paid_from(self):
        phone = (self.cleaned_data.get("paid_from") or "").replace(" ", "").replace("-", "")
        if phone and not ZW_MOBILE.match(phone):
            raise forms.ValidationError("Enter a valid Zimbabwean number, or leave it blank.")
        return phone


class VerifyPaymentForm(forms.Form):
    """Organizer's decision on a submitted transfer."""

    payment_id = forms.IntegerField(widget=forms.HiddenInput)
    decision = forms.ChoiceField(choices=[("verify", "Verify"), ("reject", "Reject")])
    reason = forms.CharField(max_length=200, required=False)
