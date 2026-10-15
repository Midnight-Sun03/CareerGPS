from django.db import models


class IngestionSource(models.Model):
    TYPE_CHOICES = [("api", "API"), ("scraper", "Scraper"), ("direct", "Employer entered")]

    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=80, unique=True)
    source_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    active = models.BooleanField(default=True)
    company = models.CharField(max_length=160, blank=True)
    base_url = models.URLField(blank=True)
    priority = models.PositiveSmallIntegerField(default=50, help_text="Lower values are preferred for canonical content.")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self):
        return self.name


class GreenhouseBoard(models.Model):
    source = models.OneToOneField(IngestionSource, on_delete=models.CASCADE, related_name="greenhouse_board")
    company_name = models.CharField(max_length=160)
    board_token = models.SlugField(max_length=120, unique=True)
    careers_url = models.URLField(blank=True)
    country_codes = models.JSONField(default=list, blank=True)
    relevance_notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name"]

    def __str__(self):
        return f"{self.company_name} ({self.board_token})"


class SmartRecruitersCompany(models.Model):
    """One configured SmartRecruiters tenant in the generic platform family.

    Employer-specific configuration (companyIdentifier, country scope, careers
    entry point) lives here so the SmartRecruiters adapter stays generic.
    """

    source = models.OneToOneField(IngestionSource, on_delete=models.CASCADE, related_name="smartrecruiters_company")
    company_name = models.CharField(max_length=160)
    company_identifier = models.SlugField(max_length=120, unique=True)
    careers_url = models.URLField(blank=True)
    country_codes = models.JSONField(default=list, blank=True)
    relevance_notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name"]

    def __str__(self):
        return f"{self.company_name} ({self.company_identifier})"


class WorkdayBoard(models.Model):
    """One configured public Workday (Candidate Experience / CXS) job board.

    Employer-specific configuration (tenant, site, country scope, careers entry
    point) lives here so the Workday adapter stays generic.  New Workday
    employers are added through configuration, never through new scraper code.
    """

    source = models.OneToOneField(IngestionSource, on_delete=models.CASCADE, related_name="workday_board")
    company_name = models.CharField(max_length=160)
    tenant = models.SlugField(max_length=120, help_text="CXS tenant segment, e.g. 'absa'.")
    site = models.SlugField(max_length=160, help_text="CXS site segment, e.g. 'ABSAcareersite'.")
    careers_url = models.URLField(blank=True)
    country_codes = models.JSONField(default=list, blank=True)
    relevance_notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name", "site"]
        # One tenant can host several boards (Old Mutual publishes both
        # Old_Mutual_Careers and Old_Mutual_Early_Careers), so the CXS identity
        # of a board is the tenant/site pair, not the tenant alone.
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "site"], name="unique_workday_tenant_site",
            ),
        ]

    def __str__(self):
        return f"{self.company_name} ({self.tenant}/{self.site})"


class IngestionRun(models.Model):
    STATUS_CHOICES = [
        ("running", "Running"), ("success", "Success"),
        ("partial", "Partial"), ("failed", "Failed"), ("dry_run", "Dry run"),
    ]

    source = models.ForeignKey(IngestionSource, on_delete=models.PROTECT, related_name="runs")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="running")
    records_found = models.PositiveIntegerField(default=0)
    raw_records_created = models.PositiveIntegerField(default=0)
    opportunities_created = models.PositiveIntegerField(default=0)
    opportunities_updated = models.PositiveIntegerField(default=0)
    duplicates_found = models.PositiveIntegerField(default=0)
    out_of_scope_count = models.PositiveIntegerField(default=0)
    internship_count = models.PositiveIntegerField(default=0)
    learnership_count = models.PositiveIntegerField(default=0)
    graduate_count = models.PositiveIntegerField(default=0)
    entry_level_count = models.PositiveIntegerField(default=0)
    unclassified_count = models.PositiveIntegerField(default=0)
    job_kind_count = models.PositiveIntegerField(default=0)
    vacation_work_kind_count = models.PositiveIntegerField(default=0)
    unknown_kind_count = models.PositiveIntegerField(default=0)
    early_career_count = models.PositiveIntegerField(default=0)
    experienced_count = models.PositiveIntegerField(default=0)
    unknown_career_stage_count = models.PositiveIntegerField(default=0)
    in_scope_count = models.PositiveIntegerField(default=0)
    scope_unclassified_count = models.PositiveIntegerField(default=0)
    errors_count = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    error_details = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.source.code} at {self.started_at:%Y-%m-%d %H:%M}"


class RawOpportunity(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"), ("published", "Published"),
        ("unclassified", "Unclassified"), ("out_of_scope", "Out of scope"),
        ("review", "Needs review"), ("error", "Error"),
    ]

    source = models.ForeignKey(IngestionSource, on_delete=models.PROTECT, related_name="raw_opportunities")
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="raw_opportunities")
    source_external_id = models.CharField(max_length=255)
    listing_url = models.URLField(blank=True)
    source_url = models.URLField(blank=True)
    apply_url = models.URLField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    raw_content = models.TextField(blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    parser_version = models.CharField(max_length=40, default="1")
    normalization_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    classified_type = models.CharField(max_length=30, blank=True)
    opportunity_kind = models.CharField(max_length=30, blank=True)
    career_stage = models.CharField(max_length=20, blank=True)
    scope_status = models.CharField(max_length=20, blank=True)
    normalization_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-fetched_at"]
        constraints = [models.UniqueConstraint(
            fields=["source", "source_external_id", "content_hash"],
            name="unique_raw_source_version",
        )]
        indexes = [models.Index(fields=["source", "source_external_id"])]

    def __str__(self):
        return f"{self.source.code}:{self.source_external_id}"


class OpportunitySourceRecord(models.Model):
    opportunity = models.ForeignKey("core.Opportunity", on_delete=models.CASCADE, related_name="source_records")
    source = models.ForeignKey(IngestionSource, on_delete=models.PROTECT, related_name="opportunity_records")
    source_external_id = models.CharField(max_length=255)
    current_raw = models.ForeignKey(RawOpportunity, on_delete=models.SET_NULL, null=True, blank=True, related_name="current_for_records")
    listing_url = models.URLField(blank=True)
    source_url = models.URLField(blank=True)
    apply_url = models.URLField(blank=True)
    reference_number = models.CharField(max_length=120, blank=True, db_index=True)
    source_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    missing_since = models.DateTimeField(null=True, blank=True)
    active_at_source = models.BooleanField(default=True)

    class Meta:
        ordering = ["source__priority", "-last_seen_at"]
        constraints = [models.UniqueConstraint(
            fields=["source", "source_external_id"],
            name="unique_source_external_opportunity",
        )]

    def __str__(self):
        return f"{self.source.code}:{self.source_external_id} -> {self.opportunity_id}"


class OpportunityExtraction(models.Model):
    """The reusable post-AI interpretation of one opportunity.

    Phase 2.4 runs the language model on opportunity-side data only. The result
    is persisted once and reused for every candidate. User/profile data must
    never be included in this extraction record or the model request that
    produced it.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
        ("stale", "Stale"),
    ]
    COMPLETENESS_CHOICES = [
        ("full_structured", "Full structured source"),
        ("full_text", "Full text"),
        ("partial_text", "Partial text / snippet"),
        ("unknown", "Unknown"),
    ]

    opportunity = models.OneToOneField(
        "core.Opportunity",
        on_delete=models.CASCADE,
        related_name="ai_extraction",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    source_completeness = models.CharField(
        max_length=30,
        choices=COMPLETENESS_CHOICES,
        default="unknown",
    )
    input_hash = models.CharField(max_length=64, db_index=True)
    extractor = models.CharField(max_length=50, default="gemini")
    model_name = models.CharField(max_length=100)
    thinking_level = models.CharField(max_length=20, default="medium")
    schema_version = models.CharField(max_length=30, default="2.4.1")
    prompt_version = models.CharField(max_length=30, default="2.4.1")
    raw_output = models.JSONField(default=dict, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "opportunity_id"]
        indexes = [
            models.Index(fields=["status", "input_hash"], name="opp_extract_status_hash_idx"),
        ]

    def __str__(self):
        return f"AI extraction for opportunity {self.opportunity_id} [{self.status}]"


class OpportunityCriterion(models.Model):
    """One source-grounded fact extracted from an opportunity.

    The model may interpret language, but each criterion must retain source
    evidence. Matching consumes these records only after the parent extraction
    reaches READY.
    """

    TYPE_CHOICES = [
        ("occupation", "Occupation / role"),
        ("skill", "Skill / knowledge"),
        ("qualification", "Qualification"),
        ("experience", "Experience"),
        ("certification", "Certification / licence"),
        ("language", "Language"),
        ("work_eligibility", "Work eligibility"),
        ("work_arrangement", "Work arrangement"),
        ("location", "Location"),
        ("responsibility", "Responsibility"),
        ("other", "Other"),
    ]
    REQUIREMENT_CHOICES = [
        ("required", "Required"),
        ("preferred", "Preferred"),
        ("informational", "Informational"),
        ("unknown", "Unknown"),
    ]

    extraction = models.ForeignKey(
        OpportunityExtraction,
        on_delete=models.CASCADE,
        related_name="criteria",
    )
    criterion_type = models.CharField(max_length=30, choices=TYPE_CHOICES, db_index=True)
    requirement_level = models.CharField(
        max_length=20,
        choices=REQUIREMENT_CHOICES,
        default="unknown",
        db_index=True,
    )
    raw_value = models.TextField()
    normalized_value = models.TextField(blank=True)
    evidence_text = models.TextField()
    source_section = models.CharField(max_length=120, blank=True)
    minimum_years = models.PositiveSmallIntegerField(null=True, blank=True)
    maximum_years = models.PositiveSmallIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["criterion_type", "requirement_level", "id"]
        indexes = [
            models.Index(
                fields=["extraction", "criterion_type", "requirement_level"],
                name="opp_criterion_lookup_idx",
            ),
        ]

    def __str__(self):
        return f"{self.criterion_type}:{self.raw_value[:80]}"


class OpportunityCriterionTaxonomyMatch(models.Model):
    """Persisted OFO/ESCO normalization for an extracted criterion."""

    MATCH_CHOICES = [
        ("exact", "Exact identity"),
        ("related", "Related label / specialisation"),
    ]

    criterion = models.ForeignKey(
        OpportunityCriterion,
        on_delete=models.CASCADE,
        related_name="taxonomy_matches",
    )
    concept = models.ForeignKey(
        "core.TaxonomyConcept",
        on_delete=models.CASCADE,
        related_name="opportunity_criterion_matches",
    )
    match_type = models.CharField(max_length=20, choices=MATCH_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["criterion", "concept"],
                name="unique_criterion_taxonomy_concept",
            ),
        ]
        indexes = [
            models.Index(fields=["criterion", "match_type"], name="criterion_tax_match_idx"),
        ]

    def __str__(self):
        return f"{self.criterion_id} -> {self.concept_id} ({self.match_type})"
