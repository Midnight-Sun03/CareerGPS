from django import forms
from .models import CandidateVisibilityPreference, TalentPool, EmployerVacancy
from core.models import Opportunity


class VisibilityForm(forms.ModelForm):
    class Meta:
        model = CandidateVisibilityPreference
        fields = ['discoverable_by_employers', 'discovery_location', 'allow_talent_pool_saving',
            'allow_contact_reveal', 'allow_prior_applicant_rediscovery']
        labels = {
            'discoverable_by_employers': 'Let verified CareerGPS employers discover my profile',
            'discovery_location': 'City or region to share',
            'allow_talent_pool_saving': 'Allow employers to save me and add me to talent pools',
            'allow_contact_reveal': 'Allow verified employers to reveal my contact details for a published vacancy',
            'allow_prior_applicant_rediscovery': 'Allow employers I applied to to rediscover me'}
        help_texts = {
            'discoverable_by_employers': 'Share your name, confirmed skills, education, selected work history, certifications, languages and any trusted occupations derived from your Career Preferences. Contact details and documents stay hidden.',
            'allow_talent_pool_saving': 'Applies to both search and prior-applicant rediscovery. Turning this off makes saved and pool entries unavailable; history remains.',
            'allow_contact_reveal': 'Authorized recruiters may explicitly reveal your email and phone. Each access is recorded. CareerGPS does not send outreach.',
            'allow_prior_applicant_rediscovery': 'Let organizations you applied to view your current shared evidence for future roles, even when general discovery is off.'}


class TalentSearchForm(forms.Form):
    scope = forms.ChoiceField(choices=[('public', 'Discoverable candidates'), ('prior', 'Our prior applicants who allow rediscovery')], required=False)
    vacancy = forms.ModelChoiceField(queryset=EmployerVacancy.objects.none(), required=False, empty_label='General search (no match score)')
    career = forms.CharField(max_length=100, required=False, label='Exact career occupation',
        help_text='Enter the exact name of a career the candidate selected from the current structured taxonomy.')
    skill = forms.CharField(max_length=100, required=False, label='Confirmed skill')
    qualification = forms.CharField(max_length=100, required=False)
    location = forms.CharField(max_length=100, required=False, label='Shared city or region')
    certification = forms.CharField(max_length=100, required=False)
    min_experience = forms.DecimalField(min_value=0, max_value=60, decimal_places=1, required=False, label='Minimum years of dated experience')
    saved = forms.ChoiceField(choices=[('', 'Any'), ('yes', 'Saved'), ('no', 'Not saved')], required=False)

    def __init__(self, *args, member, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vacancy'].queryset = EmployerVacancy.objects.filter(organization=member.organization,
            status='published', opportunity__in=Opportunity.objects.professionally_ready()).select_related('opportunity')


class PoolForm(forms.ModelForm):
    class Meta:
        model = TalentPool
        fields = ['name', 'description']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}


class CandidateActionForm(forms.Form):
    vacancy_id = forms.IntegerField(required=False, min_value=1)
    hiring_id = forms.IntegerField(required=False, min_value=1)


class PoolAddForm(CandidateActionForm):
    pool_id = forms.IntegerField(min_value=1)
    source = forms.ChoiceField(choices=[(key, key) for key in ['search', 'saved_candidate', 'prior_applicant', 'vacancy_rediscovery', 'applicant_view']])


class ContactForm(CandidateActionForm):
    reason = forms.ChoiceField(choices=[('vacancy_review', 'Review for a published vacancy'), ('application_followup', 'Follow up on a current application')])
