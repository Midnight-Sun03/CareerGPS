from django.db import models
from django.contrib.auth.models import User

class Interview(models.Model):
    EXPERIENCE_CHOICES = [
        ('entry', 'Entry Level'),
        ('mid', 'Mid Level'),
        ('senior', 'Senior Level'),
        ('executive', 'Executive'),
    ]
    STATUS_CHOICES = [
        ('setup', 'Setup'),
        ('active', 'Active'),
        ('completed', 'Completed'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='interviews')
    occupation = models.CharField(max_length=255)
    experience_level = models.CharField(max_length=20, choices=EXPERIENCE_CHOICES)
    question_count = models.IntegerField(default=5)
    cv_text = models.TextField(blank=True, null=True)
    popia_consent = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='setup')

    # Overall body language scores (0-100)
    overall_presence_score = models.FloatField(null=True, blank=True)
    eye_contact_score = models.FloatField(null=True, blank=True)
    posture_score = models.FloatField(null=True, blank=True)
    gesture_score = models.FloatField(null=True, blank=True)

    # AI performance scores
    communication_score = models.FloatField(null=True, blank=True)
    confidence_score = models.FloatField(null=True, blank=True)
    structure_score = models.FloatField(null=True, blank=True)
    relevance_score = models.FloatField(null=True, blank=True)

    # AI feedback
    strengths = models.JSONField(default=list, blank=True)
    improvements = models.JSONField(default=list, blank=True)
    feedback_summary = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.occupation} ({self.status})"


class InterviewQuestion(models.Model):
    QUESTION_TYPE_CHOICES = [
        ('behavioural', 'Behavioural'),
        ('technical', 'Technical'),
        ('situational', 'Situational'),
        ('competency', 'Competency'),
    ]

    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name='questions')
    question_index = models.IntegerField()
    question_text = models.TextField()
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPE_CHOICES, default='behavioural')

    # Transcribed answer
    answer_text = models.TextField(blank=True, null=True)

    # Per-question body language scores
    eye_contact_score = models.FloatField(null=True, blank=True)
    posture_score = models.FloatField(null=True, blank=True)
    gesture_score = models.FloatField(null=True, blank=True)
    presence_score = models.FloatField(null=True, blank=True)

    # Recordings
    audio_file = models.FileField(upload_to='interviews/audio/', blank=True, null=True)
    video_file = models.FileField(upload_to='interviews/video/', blank=True, null=True)

    # AI per-question feedback
    answer_feedback = models.TextField(blank=True, null=True)
    answer_strengths = models.JSONField(default=list, blank=True)
    answer_improvements = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['question_index']

    def __str__(self):
        return f"Q{self.question_index + 1}: {self.question_text[:50]}"