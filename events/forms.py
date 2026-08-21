import re

from django import forms
from django.utils import timezone

from core.forms import DateTimeLocalInput, TailwindFormMixin

from .models import Event, EventUpdate, Registration, Review, TicketOutlet


class EventForm(TailwindFormMixin, forms.ModelForm):
    """Create / edit an event. Organizations are limited to those the user manages."""

    class Meta:
        model = Event
        fields = (
            "title",
            "summary",
            "description",
            "banner",
            "organization",
            "category",
            "tags",
            "starts_at",
            "ends_at",
            "registration_deadline",
            "is_online",
            "online_url",
            "venue",
            "location_note",
            "capacity",
            "allow_waitlist",
            "requires_approval",
            "is_free",
            "price",
            "currency",
            "ticket_status",
            "ticket_notes",
            "visibility",
        )
        widgets = {
            "starts_at": DateTimeLocalInput(),
            "ends_at": DateTimeLocalInput(),
            "registration_deadline": DateTimeLocalInput(),
            "summary": forms.TextInput(
                attrs={"placeholder": "Free pizza, live demos and a hiring panel"}
            ),
            "description": forms.Textarea(
                attrs={"rows": 8, "placeholder": "What's happening, who should come, what to bring"}
            ),
            "tags": forms.TextInput(attrs={"placeholder": "tech, careers, networking"}),
            "location_note": forms.TextInput(attrs={"placeholder": "Lecture Hall 4, Ground floor"}),
            "ticket_notes": forms.TextInput(
                attrs={"placeholder": "Cash or EcoCash only · student ID required"}
            ),
        }
        help_texts = {
            "capacity": "Leave blank for unlimited places.",
            "tags": "Comma-separated keywords that help students find this.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None:
            self.fields["organization"].queryset = user.managed_organizations()
            self.fields["organization"].empty_label = None
        self.fields["price"].required = False
        self.fields["capacity"].required = False

    def clean(self):
        cleaned = super().clean()
        starts_at = cleaned.get("starts_at")
        ends_at = cleaned.get("ends_at")
        deadline = cleaned.get("registration_deadline")
        is_online = cleaned.get("is_online")
        is_free = cleaned.get("is_free")

        if starts_at and ends_at and ends_at <= starts_at:
            self.add_error("ends_at", "The end time must be after the start time.")

        if starts_at and deadline and deadline > starts_at:
            self.add_error(
                "registration_deadline", "Registration must close before the event starts."
            )

        if is_online and not cleaned.get("online_url"):
            self.add_error("online_url", "Add the joining link for an online event.")

        if not is_online and not cleaned.get("venue") and not cleaned.get("location_note"):
            self.add_error("venue", "Pick a venue or describe where it's happening.")

        if not is_free and not cleaned.get("price"):
            self.add_error("price", "Set a ticket price, or mark the event as free.")

        if is_free:
            cleaned["price"] = 0

        if not self.instance.pk and starts_at and starts_at < timezone.now():
            self.add_error("starts_at", "New events can't start in the past.")

        return cleaned


class TicketOutletForm(TailwindFormMixin, forms.ModelForm):
    """One place students can buy a ticket. Blank rows are ignored."""

    class Meta:
        model = TicketOutlet
        fields = ("kind", "name", "detail", "url", "phone", "price_note", "is_available")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "SRC Offices, Student Union Building"}),
            "detail": forms.TextInput(attrs={"placeholder": "Weekdays 09:00–16:00"}),
            "phone": forms.TextInput(attrs={"placeholder": "+263 77 000 0000"}),
            "price_note": forms.TextInput(attrs={"placeholder": "USD 5 students / USD 8 general"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("name", "detail", "url", "phone", "price_note"):
            self.fields[name].required = False
            self.fields[name].widget.attrs.pop("required", None)

    def clean(self):
        cleaned = super().clean()
        # Untouched extra rows are dropped by the formset; anything the organizer
        # did touch has to carry a name, since that is what students read.
        if self.has_changed() and not cleaned.get("name") and not cleaned.get("DELETE"):
            self.add_error("name", "Give this outlet a name, or clear the row.")
        return cleaned


TicketOutletFormSet = forms.inlineformset_factory(
    Event,
    TicketOutlet,
    form=TicketOutletForm,
    extra=2,
    can_delete=True,
)


class RegistrationForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Registration
        fields = ("notes",)
        widgets = {
            "notes": forms.TextInput(
                attrs={"placeholder": "Anything the organizer should know? (optional)"}
            )
        }
        labels = {"notes": "Notes for the organizer"}


class EventUpdateForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = EventUpdate
        fields = ("title", "body")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Venue change, schedule update…"}),
            "body": forms.Textarea(attrs={"rows": 4}),
        }


class ReviewForm(TailwindFormMixin, forms.ModelForm):
    rating = forms.ChoiceField(
        choices=[(i, f"{i} star{'s' if i > 1 else ''}") for i in range(5, 0, -1)],
        label="How was it?",
    )

    class Meta:
        model = Review
        fields = ("rating", "comment")
        widgets = {
            "comment": forms.Textarea(
                attrs={"rows": 3, "placeholder": "What worked, what could be better?"}
            )
        }


# Mirrors generate_ticket_code(): VE-XXXX-XXXX over an alphabet with no
# look-alike characters. Kept next to the only thing that parses one.
TICKET_CODE_PATTERN = re.compile(r"VE-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}")


class CheckInForm(forms.Form):
    ticket_code = forms.CharField(
        # Long enough for a scanned URL, not just the code inside it — the
        # field's own length check runs before clean_ticket_code gets to pull
        # the code out, so a 20-character limit would reject every scan.
        max_length=300,
        label="Ticket code",
        widget=forms.TextInput(
            attrs={
                "class": (
                    "w-full rounded-xl border border-slate-300 px-4 py-3 font-mono text-lg "
                    "uppercase tracking-widest shadow-sm focus:border-indigo-500 "
                    "focus:outline-none focus:ring-2 focus:ring-indigo-500/30"
                ),
                "placeholder": "VE-XXXX-XXXX",
                "autofocus": "autofocus",
                "autocomplete": "off",
                # What the camera scanner fills in. See static/js/scanner.js.
                "data-scanner-target": "",
            }
        ),
    )

    def clean_ticket_code(self):
        """Accept a code, or anything a scanner might hand over containing one.

        The QR on a ticket encodes the ticket's *URL*, so a phone's built-in
        camera, a generic scanner app and our own in-page scanner all produce
        `https://…/events/tickets/VE-8F3K-2QD7/` rather than the bare code. The
        door is not the place to ask somebody to retype the interesting part.
        """
        raw = (self.cleaned_data["ticket_code"] or "").strip().upper()

        match = TICKET_CODE_PATTERN.search(raw)
        return match.group(0) if match else raw
