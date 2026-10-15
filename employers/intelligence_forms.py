from django import forms
from .models import OrganizationMembership, EmployerVacancy, HiringApplication, HiringAssignment, HiringTask


class MemberChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.user.get_full_name() or obj.user.get_username()} ({obj.get_role_display()})'


class ApplicationChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        name = obj.application.user.get_full_name() or 'Applicant'
        return f'{name} / {obj.vacancy.title} / {obj.submitted_at:%d %b %Y %H:%M} / {obj.get_stage_display()}'


class ContextForm(forms.Form):
    vacancy = forms.ModelChoiceField(queryset=EmployerVacancy.objects.none(), required=False)
    hiring = ApplicationChoice(queryset=HiringApplication.objects.none(), required=False, label='Hiring application')
    member = MemberChoice(queryset=OrganizationMembership.objects.none(), label='Assigned member')

    def __init__(self, organization, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vacancy'].queryset = organization.vacancies.all()
        self.fields['hiring'].queryset = HiringApplication.objects.filter(vacancy__organization=organization).select_related('vacancy', 'application__user')
        self.fields['member'].queryset = organization.memberships.filter(status='active', user__is_active=True).select_related('user')

    def service_data(self):
        data = dict(self.cleaned_data)
        for key, target in [('vacancy','vacancy_id'), ('hiring','hiring_id'), ('member','member_id')]:
            obj = data.pop(key)
            data[target] = obj.pk if obj else None
        return data


class AssignmentForm(ContextForm):
    responsibility = forms.ChoiceField(choices=HiringAssignment.ROLES)


class TaskForm(ContextForm):
    title = forms.CharField(max_length=200)
    description = forms.CharField(max_length=4000, required=False, widget=forms.Textarea(attrs={'rows':3}))
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={'type':'date'}))

    def __init__(self, organization, *args, **kwargs):
        super().__init__(organization, *args, **kwargs)
        self.fields['member'].queryset = self.fields['member'].queryset.exclude(role='viewer')


class TaskEditForm(forms.ModelForm):
    class Meta:
        model = HiringTask
        fields = ['title','description','due_date']
        widgets = {'description': forms.Textarea(attrs={'rows':3}), 'due_date': forms.DateInput(attrs={'type':'date'})}


class ReassignForm(forms.Form):
    member = MemberChoice(queryset=OrganizationMembership.objects.none())

    def __init__(self, organization, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['member'].queryset = organization.memberships.filter(status='active', user__is_active=True).exclude(role='viewer').select_related('user')
