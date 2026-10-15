from django import forms
from django.forms import formset_factory
from applications.models import GeneratedDocument
from .models import Organization, OrganizationMembership, EmployerVacancy, VacancyRequirement, HiringApplication, ApplicationStageEvent
from .services import VACANCY_FIELDS, REQUIREMENT_FIELDS


class OrganizationForm(forms.ModelForm):
    def clean_website(self):
        from .domains import website_domain
        value=self.cleaned_data['website']
        website_domain(value)
        return value

    class Meta:
        model = Organization
        fields = ['name', 'website', 'description', 'industry', 'location']
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}


class VacancyForm(forms.ModelForm):
    class Meta:
        model = EmployerVacancy
        fields = VACANCY_FIELDS
        widgets = {'description': forms.Textarea(attrs={'rows': 6}), 'responsibilities': forms.Textarea(attrs={'rows': 4}),
            'application_instructions': forms.Textarea(attrs={'rows': 3}), 'closing_date': forms.DateInput(attrs={'type': 'date'})}


class RequirementForm(forms.ModelForm):
    proposal_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)
    source_requirement_id = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    class Meta:
        model = VacancyRequirement
        fields = REQUIREMENT_FIELDS
        widgets = {'raw_value': forms.Textarea(attrs={'rows': 2})}


RequirementFormSet = formset_factory(RequirementForm, extra=2, can_delete=True, max_num=30, validate_max=True, absolute_max=35)


class VacancyIntakeForm(forms.Form):
    vacancy_text = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 12}),
        label='Paste vacancy text')
    vacancy_document = forms.FileField(required=False, label='Or upload PDF, DOCX or plain text')

    def clean(self):
        data = super().clean()
        if not data.get('vacancy_text') and not data.get('vacancy_document'):
            raise forms.ValidationError('Paste vacancy text or upload a vacancy document.')
        if data.get('vacancy_text') and data.get('vacancy_document'):
            raise forms.ValidationError('Choose either pasted text or one document.')
        return data


class MembershipForm(forms.Form):
    username = forms.CharField(max_length=150, help_text='Exact existing CareerGPS username. Invitations appear in their employer area.')
    role = forms.ChoiceField(choices=OrganizationMembership.ROLES)
    status = forms.ChoiceField(choices=OrganizationMembership.STATUS, initial='invited')


class StageForm(forms.Form):
    stage = forms.ChoiceField(choices=HiringApplication.STAGES)
    reason_category = forms.ChoiceField(choices=ApplicationStageEvent._meta.get_field('reason_category').choices, required=False)


class NoteForm(forms.Form):
    content = forms.CharField(max_length=10000, widget=forms.Textarea(attrs={'rows': 5}), label='Private note')


class DocumentChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.get_document_type_display()}. {obj.created_at:%d %b %Y %H:%M} (version {obj.pk})'


class SubmissionForm(forms.Form):
    cv = DocumentChoice(queryset=GeneratedDocument.objects.none(), label='CV to submit')
    cover_letter = DocumentChoice(queryset=GeneratedDocument.objects.none(), required=False, label='Cover letter (optional)')
    consent = forms.BooleanField(label='Share these document versions and job-relevant profile evidence with this employer for my application.')

    def __init__(self, *args, application, **kwargs):
        super().__init__(*args, **kwargs)
        documents = GeneratedDocument.objects.filter(application=application, user=application.user, opportunity=application.opportunity)
        self.fields['cv'].queryset = documents.filter(document_type='cv')
        self.fields['cover_letter'].queryset = documents.filter(document_type='cover_letter')
