from django.conf import settings
from django.db import models
class Application(models.Model):
    STATUS_CHOICES = [
        ("planning", "Preparing"),
        ("ready_to_apply", "Ready to apply"),
        ("applied", "Applied"),
        ("assessment", "Assessment"),
        ("interview", "Interview"),
        ("offer", "Offer"),
        ("hired", "Hired"),
        ("rejected", "Rejected"),
        ("withdrawn", "Withdrawn"),
    ]

    JOB_INFO_CHOICES = [
        ("catalogue_full", "CareerGPS full job information"),
        ("catalogue_available", "Source information available"),
        ("manual", "User-provided job information"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="career_applications",
    )
    opportunity = models.ForeignKey(
        "core.Opportunity",
        on_delete=models.CASCADE,
        related_name="applications",
    )
    clean_revision = models.ForeignKey(
        "core.OpportunityCleanRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="applications",
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="planning")
    job_info_status = models.CharField(
        max_length=30,
        choices=JOB_INFO_CHOICES,
        default="catalogue_available",
    )
    notes = models.TextField(blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "opportunity"],
                name="unique_user_opportunity_application",
            )
        ]

    def __str__(self):
        return f"{self.user_id} -> {self.opportunity_id} ({self.status})"

    @property
    def workspace_action_label(self):
        return "Continue preparation" if self.status in {"planning", "ready_to_apply"} else "View application"


class GeneratedDocument(models.Model):
    DOCUMENT_CHOICES = [
        ("cv", "CV"),
        ("cover_letter", "Cover letter"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="generated_documents",
    )
    application = models.ForeignKey(
        Application,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_documents",
    )
    opportunity = models.ForeignKey(
        "core.Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_documents",
    )
    clean_revision = models.ForeignKey(
        "core.OpportunityCleanRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="generated_documents",
    )
    document_type = models.CharField(max_length=30, choices=DOCUMENT_CHOICES)
    structured_data = models.JSONField(default=dict, blank=True)
    json_resume = models.JSONField(default=dict, blank=True)
    candidate_fact_ids = models.JSONField(default=list, blank=True)
    job_info_status = models.CharField(max_length=30, blank=True)
    template_key = models.CharField(max_length=60, default="azurill")
    layout_settings = models.JSONField(default=dict, blank=True)
    renderer_backend = models.CharField(max_length=60, default="careergps_rr_layout_adapter")
    pdf_file = models.FileField(upload_to="generated_documents/pdf/", blank=True)
    docx_file = models.FileField(upload_to="generated_documents/docx/", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_document_type_display()} {self.id} for user {self.user_id}"
