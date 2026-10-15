from django.conf import settings
from django.db import models


class Interview(models.Model):
    MODE_CHOICES = [
        ("written", "Written"),
        ("voice", "Voice"),
    ]

    QUESTION_DELIVERY_CHOICES = [
        ("text_only", "Text only"),
        ("voice_only", "Interviewer voice"),
        ("text_voice", "Text + interviewer voice"),
    ]

    CAMERA_MODE_CHOICES = [
        ("audio_only", "Audio only"),
        ("camera_analysis", "Audio + camera analysis"),
    ]

    SOURCE_CHOICES = [
        ("saved", "Saved opportunity"),
        ("application", "Application workspace"),
    ]

    STATUS_CHOICES = [
        ("setup", "Setup"),
        ("active", "Active"),
        ("transcript_review", "Transcript review"),
        ("evaluating", "Generating feedback"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="interviews",
    )

    opportunity = models.ForeignKey(
        "core.Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interview_sessions",
    )

    clean_revision = models.ForeignKey(
        "core.OpportunityCleanRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="interviews",
    )

    application = models.ForeignKey(
        "applications.Application",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interview_sessions",
    )

    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        blank=True,
    )

    mode = models.CharField(
        max_length=20,
        choices=MODE_CHOICES,
        default="written",
    )

    question_delivery = models.CharField(
        max_length=20,
        choices=QUESTION_DELIVERY_CHOICES,
        default="text_only",
    )

    camera_mode = models.CharField(
        max_length=24,
        choices=CAMERA_MODE_CHOICES,
        default="audio_only",
    )

    camera_consent = models.BooleanField(default=False)

    visual_analysis_version = models.CharField(
        max_length=40,
        blank=True,
        default="",
    )

    question_count = models.PositiveSmallIntegerField(default=5)

    job_info_status = models.CharField(
        max_length=30,
        blank=True,
    )

    context_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )

    candidate_fact_ids = models.JSONField(
        default=list,
        blank=True,
    )

    generated_document_ids = models.JSONField(
        default=list,
        blank=True,
    )

    feedback_data = models.JSONField(
        default=dict,
        blank=True,
    )

    ai_model_name = models.CharField(
        max_length=120,
        blank=True,
    )

    error_message = models.TextField(blank=True)

    popia_consent = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="setup",
    )

    # ---------------------------------------------------------
    # Legacy prototype compatibility fields.
    # They remain temporarily so old rows are not destroyed.
    # The rebuilt flow does NOT use them for interview logic.
    # ---------------------------------------------------------

    occupation = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    experience_level = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )

    cv_text = models.TextField(
        blank=True,
        null=True,
    )

    overall_presence_score = models.FloatField(
        null=True,
        blank=True,
    )
    eye_contact_score = models.FloatField(
        null=True,
        blank=True,
    )
    posture_score = models.FloatField(
        null=True,
        blank=True,
    )
    gesture_score = models.FloatField(
        null=True,
        blank=True,
    )

    communication_score = models.FloatField(
        null=True,
        blank=True,
    )
    confidence_score = models.FloatField(
        null=True,
        blank=True,
    )
    structure_score = models.FloatField(
        null=True,
        blank=True,
    )
    relevance_score = models.FloatField(
        null=True,
        blank=True,
    )

    strengths = models.JSONField(
        default=list,
        blank=True,
    )
    improvements = models.JSONField(
        default=list,
        blank=True,
    )
    feedback_summary = models.TextField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        if self.clean_revision_id:
            title = self.clean_revision.cleaned_title
        else:
            title = self.occupation or "Interview"

        return (
            f"{self.user_id} - {title} "
            f"({self.mode}/{self.status})"
        )


class InterviewQuestion(models.Model):
    QUESTION_TYPE_CHOICES = [
        ("opening", "Opening"),
        ("motivation", "Motivation"),
        ("experience", "Experience"),
        ("behavioural", "Behavioural"),
        ("situational", "Situational"),
        ("role_specific", "Role specific"),
    ]

    ANSWER_MODE_CHOICES = [
        ("written", "Written"),
        ("voice", "Voice"),
    ]

    TRANSCRIPTION_STATUS_CHOICES = [
        ("not_applicable", "Not applicable"),
        ("pending", "Pending"),
        ("available", "Available"),
        ("unavailable", "Unavailable"),
    ]

    VOICE_ANALYSIS_STATUS_CHOICES = [
        ("not_applicable", "Not applicable"),
        ("pending", "Pending"),
        ("available", "Available"),
        ("partial", "Partially available"),
        ("unavailable", "Unavailable"),
    ]

    VISUAL_ANALYSIS_STATUS_CHOICES = [
        ("not_requested", "Not requested"),
        ("ready", "Ready"),
        ("collecting", "Collecting"),
        ("complete", "Complete"),
        ("partial", "Partially available"),
        ("unavailable", "Unavailable"),
        ("error", "Error"),
    ]

    interview = models.ForeignKey(
        Interview,
        on_delete=models.CASCADE,
        related_name="questions",
    )

    question_index = models.PositiveSmallIntegerField()

    question_text = models.TextField()

    question_type = models.CharField(
        max_length=30,
        choices=QUESTION_TYPE_CHOICES,
        default="behavioural",
    )

    focus = models.CharField(
        max_length=255,
        blank=True,
    )

    answer_mode = models.CharField(
        max_length=20,
        choices=ANSWER_MODE_CHOICES,
        default="written",
    )

    answer_text = models.TextField(
        blank=True,
        null=True,
    )

    # Raw ASR evidence is immutable after processing. Candidate corrections
    # are stored separately and become the semantic evaluation input.
    raw_transcript = models.TextField(blank=True, null=True)
    corrected_transcript = models.TextField(blank=True, null=True)
    provisional_transcript = models.TextField(blank=True, null=True)

    transcription_status = models.CharField(
        max_length=20,
        choices=TRANSCRIPTION_STATUS_CHOICES,
        default="not_applicable",
    )
    transcription_error = models.TextField(blank=True)
    word_timestamps = models.JSONField(default=list, blank=True)

    voice_analysis_status = models.CharField(
        max_length=20,
        choices=VOICE_ANALYSIS_STATUS_CHOICES,
        default="not_applicable",
    )

    audio_file = models.FileField(
        upload_to="interviews/audio/",
        blank=True,
        null=True,
    )

    delivery_metrics = models.JSONField(
        default=dict,
        blank=True,
    )

    visual_analysis_status = models.CharField(
        max_length=20,
        choices=VISUAL_ANALYSIS_STATUS_CHOICES,
        default="not_requested",
    )

    visual_metrics = models.JSONField(
        default=dict,
        blank=True,
    )

    visual_analysis_error = models.TextField(blank=True)

    evaluation = models.JSONField(
        default=dict,
        blank=True,
    )

    # Legacy prototype fields.
    # Not used by the rebuilt interview engine.

    eye_contact_score = models.FloatField(
        null=True,
        blank=True,
    )
    posture_score = models.FloatField(
        null=True,
        blank=True,
    )
    gesture_score = models.FloatField(
        null=True,
        blank=True,
    )
    presence_score = models.FloatField(
        null=True,
        blank=True,
    )

    video_file = models.FileField(
        upload_to="interviews/video/",
        blank=True,
        null=True,
    )

    answer_feedback = models.TextField(
        blank=True,
        null=True,
    )

    answer_strengths = models.JSONField(
        default=list,
        blank=True,
    )

    answer_improvements = models.JSONField(
        default=list,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["question_index", "id"]

    def __str__(self):
        return (
            f"Q{self.question_index + 1}: "
            f"{self.question_text[:60]}"
        )
