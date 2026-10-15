from django import forms
from .models import OrganizationVerification
from .trust_services import EVIDENCE_FIELDS
from .domains import website_domain


class VerificationForm(forms.ModelForm):
    evidence_document = forms.FileField(required=False, help_text='Optional PDF, maximum 5 MiB. Private to platform reviewers and authorized owners/submitter.')
    class Meta:
        model = OrganizationVerification
        fields = EVIDENCE_FIELDS
        widgets = {'evidence_notes':forms.Textarea(attrs={'rows':4})}

    def clean_website(self):
        value=self.cleaned_data['website']
        website_domain(value)
        return value


class DecisionForm(forms.Form):
    """Single decision form for the three admin actions.

    Verify and decline carry no mandatory fields: the reviewer has already
    inspected the submitted information. Request-information requires an
    employer-visible message.
    """
    action = forms.ChoiceField(choices=[('verify', 'Verify employer'),
        ('decline', 'Decline employer'), ('request_info', 'Request missing information')],
        widget=forms.HiddenInput)
    employer_message = forms.CharField(max_length=4000, required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        label='Message to the employer',
        help_text='Visible to the employer. Required to decline or request information.')
    internal_note = forms.CharField(max_length=4000, required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        label='Internal note (optional)',
        help_text='Platform-only.')


