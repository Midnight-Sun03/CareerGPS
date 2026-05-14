from django.db import models
from django.contrib.auth.models import User

class profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    degree = models.CharField(max_length=200)
    institution = models.CharField(max_length=200)
    location = models.CharField(max_length=200)
    skills = models.ManyToManyField('Skill', blank=True)
    interests = models.ManyToManyField('Interest', blank=True)
    custom_skills = models.TextField(blank=True)
    profile_complete = models.BooleanField(default=False)
    applications_started = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - Profile"

class Skill(models.Model):
    name = models.CharField(max_length=100)
    
    def __str__(self):
        return self.name

class Interest(models.Model):
    name = models.CharField(max_length=100)
    
    def __str__(self):
        return self.name

class Opportunity(models.Model):
    
    TYPE_CHOICES = [
        ('internship', 'Internship'),
        ('learnership', 'Learnership'),
        ('graduate', 'Graduate Programme'),
    ]
    title = models.CharField(max_length=200)
    company = models.CharField(max_length=200)
    opportunity_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    sector = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=100)
    duration = models.CharField(max_length=100, blank=True)
    stipend = models.CharField(max_length=100, blank=True)
    required_skills = models.ManyToManyField(Skill, blank=True)
    required_degree = models.CharField(max_length=200, blank=True)
    minimum_nqf = models.IntegerField(null=True, blank=True)
    description = models.TextField()
    duties = models.TextField(blank=True)
    eligibility = models.TextField(blank=True)
    source_platform = models.CharField(max_length=200, blank=True)
    redirect_url = models.URLField(blank=True)
    reference_number = models.CharField(max_length=100, blank=True)
    application_deadline = models.DateField(null=True, blank=True)
    date_listed = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.title} - {self.company}"

class SavedOpportunity(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'opportunity']
        ordering = ['-saved_at']

    def __str__(self):
        return f"{self.user.username} saved {self.opportunity.title}"

class CVTip(models.Model):
    
    TYPE_CHOICES = [
        ('internship', 'Internship'),
        ('learnership', 'Learnership'),
        ('graduate', 'Graduate Programme'),
        ('general', 'General'),
    ]
    
    opportunity_type = models.CharField(
        max_length=20, 
        choices=TYPE_CHOICES,
        default='general'
    )
    related_skills = models.ManyToManyField(Skill, blank=True)
    title = models.CharField(max_length=200)
    content = models.TextField()
    example = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.opportunity_type})"
    
class MotivationalNudge(models.Model):
    
    TRIGGER_CHOICES = [
        ('applications_milestone', 'Applications Milestone'),
        ('inactivity', 'Inactivity'),
        ('profile_incomplete', 'Profile Incomplete'),
        ('first_save', 'First Saved Opportunity'),
        ('first_login', 'First Login'),
        ('general', 'General'),
    ]

    trigger = models.CharField(
        max_length=50,
        choices=TRIGGER_CHOICES,
        default='general'
    )
    trigger_value = models.IntegerField(null=True, blank=True)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.trigger})"
    
class Story(models.Model):

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    user = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    display_name = models.CharField(max_length=200)
    degree = models.CharField(max_length=200, blank=True)
    institution = models.CharField(max_length=200, blank=True)
    title = models.CharField(max_length=200)
    content = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.display_name} - {self.title}"
    



    