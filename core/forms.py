from django import forms

from core.models import Report

BASE_INPUT = (
    "w-full rounded-xl border border-hairline bg-surface px-4 py-2.5 text-sm text-ink "
    "placeholder:text-ink-subtle shadow-sm transition duration-200 "
    "focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30 "
    "disabled:cursor-not-allowed disabled:bg-surface-3 disabled:opacity-70"
)
ERROR_INPUT = "border-rose-400 focus:border-rose-500 focus:ring-rose-500/30"
CHECKBOX = (
    "h-4 w-4 shrink-0 rounded border-hairline bg-surface text-brand-600 "
    "transition focus:ring-2 focus:ring-brand-500/40 focus:ring-offset-0"
)
FILE_INPUT = (
    "w-full text-sm text-ink-muted file:mr-4 file:rounded-lg file:border-0 "
    "file:bg-brand-50 file:px-4 file:py-2 file:text-sm file:font-semibold "
    "file:text-brand-700 hover:file:bg-brand-100 dark:file:bg-brand-500/15 "
    "dark:file:text-brand-300"
)


class TailwindFormMixin:
    """Applies consistent Tailwind classes to every widget on a form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            existing = widget.attrs.get("class", "")

            if isinstance(widget, (forms.CheckboxInput,)):
                widget.attrs["class"] = f"{CHECKBOX} {existing}".strip()
            elif isinstance(widget, forms.CheckboxSelectMultiple):
                widget.attrs["class"] = existing
            elif isinstance(widget, (forms.FileInput, forms.ClearableFileInput)):
                widget.attrs["class"] = f"{FILE_INPUT} {existing}".strip()
            else:
                # Deliberately not consulting self.errors here: touching it runs
                # full_clean() mid-__init__, so any subclass that adjusts its
                # fields after super().__init__() would be validated against the
                # fields it had before. The error styling is applied in
                # add_error() instead, once validation actually finds a problem.
                widget.attrs["class"] = f"{BASE_INPUT} {existing}".strip()

            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 4)

            if field.required and not isinstance(
                widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)
            ):
                widget.attrs.setdefault("required", "required")

    def add_error(self, field, error):
        """Mark the offending input as validation reports it."""
        super().add_error(field, error)

        if not field or field not in self.fields:
            return  # a non-field error has no widget to highlight

        widget = self.fields[field].widget
        classes = widget.attrs.get("class", "")
        if ERROR_INPUT not in classes:
            widget.attrs["class"] = f"{classes} {ERROR_INPUT}".strip()


class DateTimeLocalInput(forms.DateTimeInput):
    """Native browser date-time picker that round-trips with Django."""

    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%dT%H:%M")


class ReportForm(TailwindFormMixin, forms.ModelForm):
    """Telling us an event or a society shouldn't be up.

    A reason and, optionally, a sentence. Deliberately short: a form that asks
    for an essay gets abandoned by the person who spotted the scam and
    completed by the person with a grievance.
    """

    class Meta:
        model = Report
        fields = ("reason", "detail")
        widgets = {
            "reason": forms.RadioSelect,
            "detail": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": (
                        "What made you think so? Anything we can check helps — a "
                        "screenshot you saw, a number you were asked to send money to, "
                        "the real date."
                    ),
                }
            ),
        }
        labels = {"reason": "What's wrong with it?", "detail": "Tell us more (optional)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Radios shouldn't carry the text-input styling the mixin applies to
        # everything, and there is no sensible default reason to preselect.
        self.fields["reason"].widget.attrs.pop("class", None)
        self.fields["reason"].empty_label = None
        self.fields["detail"].required = False
