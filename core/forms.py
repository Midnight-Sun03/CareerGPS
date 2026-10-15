from django import forms
from django.contrib.auth.models import User
from .models import NotificationPreference, StoryReport, OpportunityReport, PrivacyRequest


class NotificationPreferenceForm(forms.ModelForm):
    class Meta:
        model = NotificationPreference
        fields = ['stories', 'opportunities', 'deadlines', 'applications', 'interviews', 'career_intelligence']


class StoryReportForm(forms.ModelForm):
    class Meta:
        model = StoryReport
        fields = ['reason', 'details']
        widgets = {'details': forms.Textarea(attrs={'rows': 5, 'maxlength': 4000})}


class OpportunityReportForm(forms.ModelForm):
    class Meta:
        model = OpportunityReport
        fields = ['reason', 'details']
        widgets = {'details': forms.Textarea(attrs={'rows': 5, 'maxlength': 4000})}


from .models import (
    Achievement, CareerPreference, Certification, Education, Interest,
    Language, Profile, ProfileInterest, ProfileLink, ProfileSkill, Project,
    Skill, Story, Volunteering, WorkExperience, normalize_profile_value,
)

class RegistrationForm(forms.ModelForm):
    terms_agreed = forms.BooleanField(required=True, error_messages={
        'required': 'Agree to the Terms of Service to create an account.'})
    privacy_acknowledged = forms.BooleanField(required=True, error_messages={
        'required': 'Acknowledge the Privacy Notice to create an account.'})
    password = forms.CharField(
        widget=forms.PasswordInput
    )

    confirm_password = forms.CharField(
        widget=forms.PasswordInput
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
            "password",
            "confirm_password",
            "terms_agreed",
            "privacy_acknowledged",
        ]

    def clean_email(self):
        email = (
            self.cleaned_data
            .get("email", "")
            .strip()
            .lower()
        )

        # Active accounts must not be registered again.
        #
        # Inactive accounts are allowed through so the
        # registration view can resend verification rather
        # than permanently trapping an unverified user.
        if User.objects.filter(
            email__iexact=email,
            is_active=True,
        ).exists():
            raise forms.ValidationError(
                "An account with this email already exists."
            )

        return email

    def clean_password(self):
        password = self.cleaned_data.get(
            "password"
        )

        if password:
            from django.contrib.auth.password_validation import (
                validate_password,
            )

            validate_password(
                password
            )

        return password

    def clean(self):
        cleaned_data = super().clean()

        password = cleaned_data.get(
            "password"
        )

        confirm_password = cleaned_data.get(
            "confirm_password"
        )

        if (
            password
            and confirm_password
            and password != confirm_password
        ):
            self.add_error(
                "confirm_password",
                "Passwords do not match.",
            )

        return cleaned_data

    def save(self, commit=True):
        user = super().save(
            commit=False
        )

        user.email = (
            self.cleaned_data["email"]
            .strip()
            .lower()
        )

        user.username = user.email

        user.set_password(
            self.cleaned_data["password"]
        )

        user.is_active = False

        if commit:
            user.save()

        return user


class PrivacyRequestForm(forms.ModelForm):
    class Meta:
        model = PrivacyRequest
        fields = ['category', 'description']
        widgets = {'description': forms.Textarea(attrs={'rows': 5, 'maxlength': 4000})}

class ProfileCompletionForm(forms.ModelForm):
    """Deprecated legacy form retained for import compatibility only."""
    class Meta:
        model = Profile
        fields = ['qualification', 'institution', 'location', 'skills', 'interests', 'custom_skills']
        widgets = {'skills': forms.CheckboxSelectMultiple(), 'interests': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].required = False
        self.fields['skills'].required = False
        self.fields['interests'].required = False


class PersonalDetailsForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    phone = forms.CharField(max_length=40, required=False)
    location = forms.CharField(max_length=200, required=False)

    def __init__(self, *args, user=None, profile=None, **kwargs):
        self.user = user
        self.profile = profile
        if not args and not kwargs.get('data') and user and profile:
            kwargs['initial'] = {
                'first_name': user.first_name,
                'last_name': user.last_name,
                'phone': profile.phone,
                'location': profile.location,
            }
        super().__init__(*args, **kwargs)

    def save(self):
        self.user.first_name = self.cleaned_data['first_name'].strip()
        self.user.last_name = self.cleaned_data['last_name'].strip()
        self.user.save(update_fields=['first_name', 'last_name'])
        self.profile.phone = self.cleaned_data['phone'].strip()
        self.profile.location = self.cleaned_data['location'].strip()
        self.profile.save(update_fields=['phone', 'location', 'updated_at'])
        return self.profile


class ProfileOwnedModelForm(forms.ModelForm):
    def __init__(self, *args, profile=None, **kwargs):
        self.profile = profile
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.profile is not None:
            instance.profile = self.profile
        if not instance.pk:
            instance.source = 'user_entered'
        instance.confirmed_by_user = True
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class DateInput(forms.DateInput):
    input_type = 'date'


class EducationForm(ProfileOwnedModelForm):
    class Meta:
        model = Education
        fields = [
            'institution', 'qualification', 'field_of_study', 'nqf_level',
            'start_date', 'end_date', 'currently_studying', 'description',
        ]
        widgets = {'start_date': DateInput(), 'end_date': DateInput(), 'description': forms.Textarea(attrs={'rows': 3})}


class ProfileSkillForm(ProfileOwnedModelForm):
    class Meta:
        model = ProfileSkill
        fields = ['entered_name']
        labels = {'entered_name': 'Skill'}
        widgets = {'entered_name': forms.TextInput(attrs={'placeholder': 'e.g. Python, SQL or Business Analysis'})}

    def clean_entered_name(self):
        entered_name = ' '.join(self.cleaned_data['entered_name'].split())
        normalized_name = normalize_profile_value(entered_name)
        if not normalized_name:
            raise forms.ValidationError('Enter a skill.')
        existing = ProfileSkill.objects.filter(profile=self.profile, normalized_name=normalized_name)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError('That skill is already on your profile.')
        return entered_name


class ProfileInterestForm(ProfileOwnedModelForm):
    class Meta:
        model = ProfileInterest
        fields = ['entered_name']
        labels = {'entered_name': 'Interest'}
        widgets = {'entered_name': forms.TextInput(attrs={'placeholder': 'e.g. Renewable Energy or Data Science'})}

    def clean_entered_name(self):
        entered_name = ' '.join(self.cleaned_data['entered_name'].split())
        normalized_name = normalize_profile_value(entered_name)
        if not normalized_name:
            raise forms.ValidationError('Enter an interest.')
        existing = ProfileInterest.objects.filter(profile=self.profile, normalized_name=normalized_name)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError('That interest is already on your profile.')
        return entered_name


class WorkExperienceForm(ProfileOwnedModelForm):
    class Meta:
        model = WorkExperience
        fields = [
            'title', 'organisation', 'location', 'employment_type', 'start_date',
            'end_date', 'currently_working', 'description', 'bullets',
        ]
        widgets = {
            'start_date': DateInput(), 'end_date': DateInput(),
            'description': forms.Textarea(attrs={'rows': 3}),
            'bullets': forms.Textarea(attrs={'rows': 4, 'placeholder': 'One achievement per line'}),
        }


class ProjectForm(ProfileOwnedModelForm):
    class Meta:
        model = Project
        fields = [
            'title', 'organisation', 'start_date', 'end_date', 'currently_ongoing',
            'description', 'bullets', 'project_url',
        ]
        labels = {'organisation': 'Organisation or context'}
        widgets = {
            'start_date': DateInput(), 'end_date': DateInput(),
            'description': forms.Textarea(attrs={'rows': 3}),
            'bullets': forms.Textarea(attrs={'rows': 4, 'placeholder': 'One achievement per line'}),
        }


class CertificationForm(ProfileOwnedModelForm):
    class Meta:
        model = Certification
        fields = [
            'title', 'issuer', 'issue_date', 'expiry_date', 'does_not_expire',
            'credential_id', 'credential_url', 'description',
        ]
        widgets = {
            'issue_date': DateInput(), 'expiry_date': DateInput(),
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class AchievementForm(ProfileOwnedModelForm):
    class Meta:
        model = Achievement
        fields = ['title', 'organisation', 'date', 'description']
        widgets = {'date': DateInput(), 'description': forms.Textarea(attrs={'rows': 3})}


class VolunteeringForm(ProfileOwnedModelForm):
    class Meta:
        model = Volunteering
        fields = [
            'role', 'organisation', 'location', 'start_date', 'end_date',
            'currently_volunteering', 'description',
        ]
        widgets = {
            'start_date': DateInput(), 'end_date': DateInput(),
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class LanguageForm(ProfileOwnedModelForm):
    class Meta:
        model = Language
        fields = ['language', 'proficiency']


class ProfileLinkForm(ProfileOwnedModelForm):
    class Meta:
        model = ProfileLink
        fields = ['label', 'link_type', 'url']


class CareerPreferenceForm(ProfileOwnedModelForm):
    class Meta:
        model = CareerPreference
        fields = [
            'target_roles', 'preferred_locations', 'work_arrangement',
            'opportunity_kinds', 'industries', 'short_career_goal',
        ]
        widgets = {
            'target_roles': forms.Textarea(attrs={'rows': 2, 'placeholder': 'e.g. Data Analyst, Business Analyst'}),
            'preferred_locations': forms.Textarea(attrs={'rows': 2, 'placeholder': 'e.g. Johannesburg, Cape Town'}),
            'opportunity_kinds': forms.Textarea(attrs={'rows': 2, 'placeholder': 'e.g. Graduate programmes, internships'}),
            'industries': forms.Textarea(attrs={'rows': 2}),
            'short_career_goal': forms.Textarea(attrs={'rows': 3}),
        }

class StoryForm(forms.ModelForm):
    class Meta:
        model = Story
        fields = ['title', 'content']

class LoginForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={'placeholder': 'Enter your email'}
        )
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={'placeholder': 'Enter your password'}
        )
    )


class AccountClosurePasswordForm(forms.Form):
    """Password re-entry before the destructive account closure step."""

    password = forms.CharField(
        label='Current password',
        error_messages={'required': 'Enter your current password.'},
        widget=forms.PasswordInput(
            attrs={'autocomplete': 'current-password', 'autofocus': True}
        ),
    )

