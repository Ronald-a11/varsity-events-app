from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from core.forms import TailwindFormMixin
from events.models import Category

from .models import University, User


class SignUpForm(TailwindFormMixin, UserCreationForm):
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Last name")
    email = forms.EmailField(help_text="Use your university email where possible.")
    university = forms.ModelChoiceField(
        queryset=University.objects.all(),
        empty_label="Select your university",
        label="Which university are you at?",
        help_text="We use this to put your own campus first — you'll still see events nationwide.",
    )
    role = forms.ChoiceField(
        choices=[
            (User.Role.STUDENT, "I'm a student — I want to find events"),
            (User.Role.ORGANIZER, "I'm an organizer — I run a society or events"),
        ],
        initial=User.Role.STUDENT,
        widget=forms.RadioSelect,
        label="What brings you here?",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "username", "email", "university", "role")
        widgets = {
            "first_name": forms.TextInput(
                attrs={"autocomplete": "given-name", "autofocus": "autofocus"}
            ),
            "last_name": forms.TextInput(attrs={"autocomplete": "family-name"}),
            "username": forms.TextInput(
                attrs={"autocomplete": "username", "autocapitalize": "none", "spellcheck": "false"}
            ),
            "email": forms.EmailInput(
                attrs={
                    "autocomplete": "email",
                    "autocapitalize": "none",
                    "inputmode": "email",
                    "spellcheck": "false",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Browsers offer a generated password when the field advertises itself.
        self.fields["password1"].widget.attrs.update({"autocomplete": "new-password"})
        self.fields["password2"].widget.attrs.update({"autocomplete": "new-password"})

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.university = self.cleaned_data.get("university")
        user.role = self.cleaned_data["role"]
        if commit:
            user.save()
        return user


class LoginForm(TailwindFormMixin, AuthenticationForm):
    username = forms.CharField(
        label="Username or email",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "username",
                "autocapitalize": "none",
                "autofocus": "autofocus",
                "placeholder": "you@students.ac.zw",
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password", "placeholder": "Your password"}
        ),
    )
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        label="Keep me signed in on this device",
    )

    def clean(self):
        """Let people sign in with either their username or their email."""
        identifier = self.cleaned_data.get("username")
        if identifier and "@" in identifier:
            match = User.objects.filter(email__iexact=identifier).first()
            if match:
                self.cleaned_data["username"] = match.username
        return super().clean()


class ProfileForm(TailwindFormMixin, forms.ModelForm):
    interests = forms.ModelMultipleChoiceField(
        queryset=Category.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="We use these to highlight events you'll care about.",
    )

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
            "avatar",
            "university",
            "student_id",
            "course",
            "year_of_study",
            "phone",
            "bio",
            "interests",
        )
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 3, "placeholder": "A line or two about you"}),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email
