from django import forms
from django.contrib.auth.models import User
from .models import Profile, Story, Skill, Interest

class RegistrationForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = User 
        fields = ['first_name','last_name', 'email', 'password', 'confirm_password']

    def clean(self):
        Cleaned_data = super().clean()
        password = Cleaned_data.get('password')
        confirm_password = Cleaned_data.get('confirm_password')

        if password and confirm_password:
            if password != confirm_password:
                raise forms.ValidationError("Passwords do not match")
        return self.cleaned_data
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = user.email
        user.set_password(self.cleaned_data['password'])
        user.is_active = False
        if commit:
            user.save()
        return user

class ProfileCompletionForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['qualification', 'institution', 'location', 'skills', 'interests', 'custom_skills']
        widgets = {'skills': forms.CheckboxSelectMultiple(), 'interests': forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].required = False
        self.fields['skills'].required = False
        self.fields['interests'].required = False

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