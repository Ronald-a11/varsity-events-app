from django import forms

from core.forms import TailwindFormMixin

from .models import Membership, Organization, OrganizationClaim


class OrganizationForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Organization
        fields = (
            "name",
            "kind",
            "tagline",
            "description",
            "logo",
            "cover",
            "university",
            "email",
            "website",
            "instagram",
            "twitter",
        )
        widgets = {
            "tagline": forms.TextInput(
                attrs={"placeholder": "Building things and shipping them since 2014"}
            ),
            "description": forms.Textarea(
                attrs={"rows": 5, "placeholder": "What the society does, who can join, when you meet"}
            ),
            "instagram": forms.TextInput(attrs={"placeholder": "@handle"}),
            "twitter": forms.TextInput(attrs={"placeholder": "@handle"}),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Organization.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A society with this name is already registered.")
        return name


class MembershipForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Membership
        fields = ("role", "title", "is_active")


class OrganizationClaimForm(TailwindFormMixin, forms.ModelForm):
    """What somebody has to say to be handed a society.

    Two fields, both required. A claim with no position and no evidence cannot
    be judged, and a queue of those is what turns a review step into a rubber
    stamp.
    """

    class Meta:
        model = OrganizationClaim
        fields = ("role_title", "evidence")
        widgets = {
            "role_title": forms.TextInput(
                attrs={"placeholder": "Secretary", "autofocus": "autofocus"}
            ),
            "evidence": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": (
                        "I'm the 2026 secretary — student number R2011234. Our Dean of "
                        "Students, Mr Chibaya, can confirm it, and we run the "
                        "@uzdebateunion Instagram account."
                    ),
                }
            ),
        }

    def clean_evidence(self):
        evidence = self.cleaned_data["evidence"].strip()
        if len(evidence) < 30:
            raise forms.ValidationError(
                "Tell us a bit more — enough that somebody who doesn't know you "
                "could check it."
            )
        return evidence
