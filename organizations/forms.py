from django import forms

from core.forms import TailwindFormMixin

from .models import Membership, Organization


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
