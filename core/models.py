import re
from urllib.parse import urlparse

from django.db import models, transaction
from django.contrib.auth.models import User
from django.utils import timezone

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST


def normalize_profile_value(value):
    """Conservative normalization for user-entered names and labels."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


class UserFact(models.Model):
    SOURCE_CHOICES = [
        ('user_entered', 'Entered by user'),
        ('cv_imported', 'Imported from CV'),
    ]

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='user_entered')
    confirmed_by_user = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    qualification = models.CharField(max_length=200)
    institution = models.CharField(max_length=200)
    location = models.CharField(max_length=200)
    phone = models.CharField(max_length=40, blank=True)
    skills = models.ManyToManyField('Skill', blank=True)
    interests = models.ManyToManyField('Interest', blank=True)
    custom_skills = models.TextField(blank=True)
    profile_complete = models.BooleanField(default=False)
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

class Education(UserFact):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="education_records")
    institution = models.CharField(max_length=200)
    qualification = models.CharField(max_length=200)
    field_of_study = models.CharField(max_length=200, blank=True)
    nqf_level = models.PositiveSmallIntegerField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    currently_studying = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['-currently_studying', '-end_date', '-start_date', '-id']

    def __str__(self):
        return f"{self.qualification} - {self.institution}"


class ProfileSkill(UserFact):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="profile_skills")
    entered_name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100)

    class Meta:
        ordering = ['normalized_name', 'id']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'normalized_name'], name='unique_profile_skill_name'),
        ]

    def save(self, *args, **kwargs):
        self.entered_name = re.sub(r"\s+", " ", self.entered_name.strip())
        self.normalized_name = normalize_profile_value(self.entered_name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.entered_name


class ProfileInterest(UserFact):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="profile_interests")
    entered_name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100)

    class Meta:
        ordering = ['normalized_name', 'id']
        constraints = [
            models.UniqueConstraint(fields=['profile', 'normalized_name'], name='unique_profile_interest_name'),
        ]

    def save(self, *args, **kwargs):
        self.entered_name = re.sub(r"\s+", " ", self.entered_name.strip())
        self.normalized_name = normalize_profile_value(self.entered_name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.entered_name


class Project(UserFact):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="projects")
    title = models.CharField(max_length=200)
    organisation = models.CharField(max_length=200, blank=True)
    dates = models.CharField(max_length=100, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    currently_ongoing = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    bullets = models.TextField(blank=True, help_text="One achievement per line")
    project_url = models.URLField(blank=True)

    def bullet_list(self):
        return [line.strip() for line in self.bullets.splitlines() if line.strip()]

    def __str__(self):
        return f"{self.title} ({self.profile.user.username})"


class WorkExperience(UserFact):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="work_experiences")
    title = models.CharField(max_length=200)
    organisation = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=200, blank=True)
    employment_type = models.CharField(max_length=100, blank=True)
    dates = models.CharField(max_length=100, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    currently_working = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    bullets = models.TextField(blank=True, help_text="One achievement per line")

    def bullet_list(self):
        return [line.strip() for line in self.bullets.splitlines() if line.strip()]

    def __str__(self):
        return f"{self.title} ({self.profile.user.username})"


class Certification(UserFact):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="certifications")
    title = models.CharField(max_length=200)
    dates = models.CharField(max_length=100, blank=True)
    issuer = models.CharField(max_length=200, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    does_not_expire = models.BooleanField(default=False)
    credential_id = models.CharField(max_length=200, blank=True)
    credential_url = models.URLField(blank=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return f"{self.title} ({self.profile.user.username})"


class Achievement(UserFact):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='achievements')
    title = models.CharField(max_length=200)
    organisation = models.CharField(max_length=200, blank=True)
    date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.title


class Volunteering(UserFact):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='volunteering_records')
    role = models.CharField(max_length=200)
    organisation = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    currently_volunteering = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['-currently_volunteering', '-end_date', '-start_date', '-id']

    def __str__(self):
        return f"{self.role} - {self.organisation}"


class Language(UserFact):
    PROFICIENCY_CHOICES = [
        ('basic', 'Basic'),
        ('conversational', 'Conversational'),
        ('professional', 'Professional working proficiency'),
        ('fluent', 'Fluent'),
        ('native', 'Native or bilingual'),
    ]

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='languages')
    language = models.CharField(max_length=100)
    proficiency = models.CharField(max_length=30, choices=PROFICIENCY_CHOICES, blank=True)

    class Meta:
        ordering = ['language', 'id']

    def __str__(self):
        return f"{self.language} - {self.get_proficiency_display() or 'Not specified'}"


class ProfileLink(UserFact):
    LINK_TYPE_CHOICES = [
        ('portfolio', 'Portfolio'),
        ('linkedin', 'LinkedIn'),
        ('github', 'GitHub'),
        ('website', 'Website'),
        ('other', 'Other'),
    ]

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='profile_links')
    label = models.CharField(max_length=100)
    link_type = models.CharField(max_length=20, choices=LINK_TYPE_CHOICES, default='portfolio')
    url = models.URLField()

    class Meta:
        ordering = ['link_type', 'label', 'id']

    def __str__(self):
        return self.label


class CareerPreference(UserFact):
    WORK_ARRANGEMENT_CHOICES = [
        ('', 'No preference'),
        ('onsite', 'On-site'),
        ('hybrid', 'Hybrid'),
        ('remote', 'Remote'),
        ('flexible', 'Flexible'),
    ]

    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name='career_preferences')
    target_roles = models.TextField(blank=True, help_text='Separate values with commas')
    preferred_locations = models.TextField(blank=True, help_text='Separate values with commas')
    work_arrangement = models.CharField(max_length=20, choices=WORK_ARRANGEMENT_CHOICES, blank=True)
    opportunity_kinds = models.TextField(blank=True, help_text='Separate values with commas')
    industries = models.TextField(blank=True, help_text='Separate values with commas')
    short_career_goal = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        # Keep the authored text and its post-save derived occupation rows in
        # the same transaction for Profile and admin edits alike.
        with transaction.atomic():
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Deleting the whole preference removes its target containers, but
        # stored v2 admissions PROTECT their assessments by design, so the
        # admissions must be released first.  This is the delete-path twin of
        # the target-blanked save path: no admission is left dangling, and
        # the PROTECT contract everywhere else is untouched.  The existing
        # post_delete signal then enqueues the NO_TARGETS rebuild as usual.
        with transaction.atomic():
            containers = list(
                self.target_resolutions.values_list('pk', flat=True))
            if containers:
                ProfileFeedEvaluation.objects.filter(
                    relationship_assessment__target_resolution_id__in=containers,
                ).delete()
                live_ids = list(
                    ProfileEvaluationGeneration.objects.filter(
                        profile=self.profile, state='ACTIVE',
                    ).values_list('pk', flat=True))
                if live_ids:
                    ProfileEvaluationGeneration.objects.filter(
                        pk__in=live_ids,
                    ).update(state='SUPERSEDED', superseded_at=timezone.now())
                state = ProfileFeedState.objects.filter(
                    profile=self.profile).first()
                if state is not None and state.active_generation_id in set(live_ids):
                    state.active_generation = None
                    state.save(update_fields=['active_generation', 'updated_at'])
            return super().delete(*args, **kwargs)

    def __str__(self):
        return f"Career preferences for {self.profile.user.username}"


class TaxonomyRelease(models.Model):
    """A versioned trusted taxonomy snapshot available to the matcher.

    Matching never calls external taxonomy services. Import/sync commands populate
    these tables ahead of time and exactly one release per source can be active.
    """

    SOURCE_CHOICES = [
        ('esco', 'ESCO'),
        ('ofo', 'South African OFO'),
        ('onet', 'O*NET'),
        ('isco', 'ISCO-08'),
    ]

    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    version = models.CharField(max_length=40)
    is_active = models.BooleanField(default=False)
    source_url = models.URLField(blank=True)
    imported_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['source', '-imported_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'version'],
                name='unique_taxonomy_source_version',
            ),
            models.UniqueConstraint(
                fields=['source'],
                condition=models.Q(is_active=True),
                name='one_active_taxonomy_release_per_source',
            ),
        ]

    def __str__(self):
        active = ' (active)' if self.is_active else ''
        return f"{self.get_source_display()} {self.version}{active}"


class TaxonomyConcept(models.Model):
    """A concept imported from a trusted labour-market taxonomy."""

    KIND_CHOICES = [
        ('skill', 'Skill or knowledge'),
        ('skill_group', 'Skill group'),
        ('occupation', 'Occupation'),
        ('occupation_group', 'Occupation group'),
    ]

    release = models.ForeignKey(
        TaxonomyRelease,
        on_delete=models.CASCADE,
        related_name='concepts',
    )
    external_id = models.CharField(max_length=500)
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    code = models.CharField(max_length=80, blank=True)
    preferred_label = models.CharField(max_length=300)
    normalized_label = models.CharField(max_length=300, db_index=True)
    definition = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['release__source', 'kind', 'preferred_label']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'external_id'],
                name='unique_taxonomy_concept_external_id',
            ),
        ]
        indexes = [
            models.Index(
                fields=['release', 'kind', 'normalized_label'],
                name='tax_concept_lookup_idx',
            ),
            models.Index(
                fields=['release', 'kind', 'code'],
                name='tax_concept_code_idx',
            ),
        ]

    def __str__(self):
        return f"{self.release.source}:{self.preferred_label}"


class TaxonomyLabel(models.Model):
    """Preferred/alternative/specialisation labels that identify one concept."""

    LABEL_TYPE_CHOICES = [
        ('preferred', 'Preferred'),
        ('alternative', 'Alternative'),
        ('hidden', 'Hidden'),
        ('specialisation', 'Specialisation'),
        ('reported', 'Reported title'),
        ('title_index', 'Title index'),
    ]

    concept = models.ForeignKey(
        TaxonomyConcept,
        on_delete=models.CASCADE,
        related_name='labels',
    )
    label = models.CharField(max_length=300)
    normalized_label = models.CharField(max_length=300, db_index=True)
    label_type = models.CharField(
        max_length=20,
        choices=LABEL_TYPE_CHOICES,
        default='alternative',
    )
    language = models.CharField(max_length=12, default='en')

    class Meta:
        ordering = ['normalized_label', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['concept', 'normalized_label', 'label_type', 'language'],
                name='unique_taxonomy_concept_label_type',
            ),
        ]
        indexes = [
            models.Index(
                fields=['normalized_label', 'language'],
                name='tax_label_lookup_idx',
            ),
        ]

    def __str__(self):
        return self.label


class TaxonomyRelation(models.Model):
    """A relationship asserted by the imported trusted source.

    Direction convention:
    - broader: subject is the narrower concept; object is its broader concept.
    """

    RELATION_CHOICES = [
        ('broader', 'Broader concept'),
        ('essential', 'Essential skill relationship'),
        ('optional', 'Optional skill relationship'),
        ('contextual_related', 'Contextual related occupation'),
    ]

    subject = models.ForeignKey(
        TaxonomyConcept,
        on_delete=models.CASCADE,
        related_name='outgoing_taxonomy_relations',
    )
    relation_type = models.CharField(max_length=30, choices=RELATION_CHOICES)
    object = models.ForeignKey(
        TaxonomyConcept,
        on_delete=models.CASCADE,
        related_name='incoming_taxonomy_relations',
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['relation_type', 'subject_id', 'object_id']
        constraints = [
            models.UniqueConstraint(
                fields=['subject', 'relation_type', 'object'],
                name='unique_taxonomy_relation',
            ),
        ]
        indexes = [
            models.Index(
                fields=['subject', 'relation_type'],
                name='tax_rel_subject_idx',
            ),
            models.Index(
                fields=['object', 'relation_type'],
                name='tax_rel_object_idx',
            ),
        ]

    def __str__(self):
        return f"{self.subject} {self.relation_type} {self.object}"


class OpportunityQuerySet(models.QuerySet):
    def discoverable(self):
        """The single Phase 1-aware definition of the live catalogue."""
        from employers.permissions import approved_organization_filter
        return self.filter(
            models.Q(employer_vacancy__isnull=True) | (approved_organization_filter('employer_vacancy__organization__') & models.Q(employer_vacancy__trust_hold=False)),
            is_active=True,
            lifecycle_status='active',
            scope_status__in=('in_scope', 'unclassified'),
        ).exclude(moderation_status='suppressed').exclude(
            models.Q(listing_kind='vacancy') & (
                models.Q(closing_date__lt=timezone.localdate()) |
                models.Q(application_deadline__lt=timezone.localdate())
            )
        )

    def professionally_ready(self, *, include_obvious_senior=True):
        """Catalogue rows whose active clean revision matches the current raw source."""
        from employers.permissions import approved_organization_filter
        queryset = self.filter(
            models.Q(employer_vacancy__isnull=True) | (
                approved_organization_filter('employer_vacancy__organization__')
                & models.Q(employer_vacancy__trust_hold=False)),
            is_active=True,
            lifecycle_status='active',
            scope_status__in=('in_scope', 'unclassified'),
            active_clean_revision__isnull=False,
            active_clean_revision__status='active',
            active_clean_revision__schema_version='1.1',
            active_clean_revision__source_record__active_at_source=True,
            active_clean_revision__source_record__current_raw_id=models.F(
                'active_clean_revision__raw_opportunity_id'),
        ).exclude(moderation_status='suppressed').exclude(
            models.Q(listing_kind='vacancy')
            & models.Q(active_clean_revision__closing_sort_at__lt=timezone.now()))
        if not include_obvious_senior:
            queryset = queryset.exclude(
                active_clean_revision__seniority_label='OBVIOUS_SENIOR')
        return queryset

class Opportunity(models.Model):
    @property
    def employer_trust(self):
        from django.core.exceptions import ObjectDoesNotExist
        from employers.permissions import organization_can_use_employer_platform
        try:
            organization=self.employer_vacancy.organization
        except ObjectDoesNotExist:
            return None
        return {'joined':organization.created_at} if organization_can_use_employer_platform(organization) else None


    TYPE_CHOICES = [
        ('internship', 'Internship'), 
        ('learnership', 'Learnership'),
        ('graduate', 'Graduate program'),
        ('entry_level', 'Entry Level Position'),
        ('unclassified', 'Unclassified'),
    ]

    OPPORTUNITY_KIND_CHOICES = [
        ('job', 'Job'),
        ('internship', 'Internship'),
        ('learnership', 'Learnership'),
        ('graduate_programme', 'Graduate programme'),
        ('vacation_work', 'Vacation work'),
        ('unknown', 'Other / unknown'),
    ]

    CAREER_STAGE_CHOICES = [
        ('early_career', 'Early career'),
        ('experienced', 'Experienced'),
        ('unknown', 'Unknown'),
    ]

    SCOPE_STATUS_CHOICES = [
        ('in_scope', 'In scope'),
        ('unclassified', 'Unclassified'),
        ('out_of_scope', 'Out of scope'),
    ]

    LISTING_KIND_CHOICES = [
        ('vacancy', 'Vacancy'),
        ('talent_pool', 'Talent Pool'),
        ('pipeline', 'Pipeline'),
    ]

    LIFECYCLE_CHOICES = [
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('removed', 'Removed From Source'),
        ('review', 'Needs Review'),
    ]

    title = models.CharField(max_length=200)
    company = models.CharField(max_length=200)
    opportunity_type = models.CharField(max_length=200, choices = TYPE_CHOICES)
    opportunity_kind = models.CharField(
        max_length=30, choices=OPPORTUNITY_KIND_CHOICES, default='job'
    )
    career_stage = models.CharField(
        max_length=20, choices=CAREER_STAGE_CHOICES, default='unknown'
    )
    scope_status = models.CharField(
        max_length=20, choices=SCOPE_STATUS_CHOICES, default='unclassified', db_index=True
    )
    classification_reason = models.TextField(blank=True)
    classification_evidence = models.JSONField(default=list, blank=True)
    evidence_provenance = models.JSONField(default=dict, blank=True)
    programme_identity = models.CharField(max_length=100, blank=True)
    employment_type = models.CharField(max_length=100, blank=True)
    listing_kind = models.CharField(max_length=20, choices=LISTING_KIND_CHOICES, default='vacancy')
    work_arrangement = models.CharField(max_length=50, blank=True)
    sector = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=100)
    location_text = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    duration = models.CharField(max_length=100, blank=True)
    stipend = models.CharField(max_length=100, blank=True)
    required_skills = models.ManyToManyField(Skill, blank=True)
    preferred_skills = models.ManyToManyField(Skill, blank=True, related_name='preferred_for_opportunities')
    required_qualification = models.CharField(max_length=200, blank=True)
    qualification_text = models.TextField(blank=True)
    qualification_minimum = models.TextField(blank=True)
    qualification_preferred = models.TextField(blank=True)
    minimum_nqf = models.IntegerField(null=True, blank=True)
    experience_text = models.TextField(blank=True)
    experience_min_years = models.PositiveSmallIntegerField(null=True, blank=True)
    experience_max_years = models.PositiveSmallIntegerField(null=True, blank=True)
    preferred_experience_text = models.TextField(blank=True)
    preferred_experience_min_years = models.PositiveSmallIntegerField(null=True, blank=True)
    preferred_experience_max_years = models.PositiveSmallIntegerField(null=True, blank=True)
    source_native_category = models.CharField(max_length=255, blank=True)
    source_native_career_level = models.CharField(max_length=255, blank=True)
    programme_signals = models.JSONField(default=list, blank=True)
    early_career_signals = models.JSONField(default=list, blank=True)
    seniority_signals = models.JSONField(default=list, blank=True)
    source_specific_evidence = models.JSONField(default=dict, blank=True)
    description = models.TextField()
    duties = models.TextField(blank=True)
    responsibilities = models.TextField(blank=True)
    eligibility = models.TextField(blank=True)
    eligibility_text = models.TextField(blank=True)
    department = models.CharField(max_length=200, blank=True)
    business_unit = models.CharField(max_length=200, blank=True)
    office = models.CharField(max_length=200, blank=True)
    source_platform = models.CharField(max_length=200, blank=True)
    redirect_url = models.URLField(blank=True)
    primary_apply_url = models.URLField(blank=True)
    reference_number = models.CharField(max_length=100, blank=True)
    application_deadline = models.DateField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    closing_date = models.DateField(null=True, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    salary_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_currency = models.CharField(max_length=3, blank=True)
    salary_is_predicted = models.BooleanField(null=True, blank=True)
    salary_period = models.CharField(max_length=10, blank=True,
        choices=[('hour', 'Per hour'), ('month', 'Per month'), ('year', 'Per year')],
        help_text='Set only when the source explicitly establishes the pay period.')
    compensation_text = models.CharField(max_length=255, blank=True)
    canonical_fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    lifecycle_status = models.CharField(max_length=20, choices=LIFECYCLE_CHOICES, default='active')
    date_listed = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    active_clean_revision = models.ForeignKey(
        'OpportunityCleanRevision',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    objects = OpportunityQuerySet.as_manager()

    moderation_status = models.CharField(max_length=20, default='clear', db_index=True,
        choices=[('clear', 'Clear'), ('under_review', 'Under review'), ('suppressed', 'Suppressed')])

    class Meta:
        ordering = ['-posted_at', '-date_listed']

    def save(self, *args, **kwargs):
        # Legacy application paths still create opportunities by
        # ``opportunity_type``. Populate the new dimensions only while they
        # are untouched defaults; ingestion supplies all three explicitly.
        if (
            self.opportunity_kind == 'job'
            and self.career_stage == 'unknown'
            and self.scope_status == 'unclassified'
        ):
            legacy_dimensions = {
                'internship': ('internship', 'early_career', 'in_scope'),
                'learnership': ('learnership', 'early_career', 'in_scope'),
                'graduate': ('graduate_programme', 'early_career', 'in_scope'),
                'entry_level': ('job', 'early_career', 'in_scope'),
            }
            dimensions = legacy_dimensions.get(self.opportunity_type)
            if dimensions:
                self.opportunity_kind, self.career_stage, self.scope_status = dimensions
        # Keep legacy fields used by existing templates compatible while the
        # canonical fields become the source of truth for ingestion.
        if not self.location_text:
            self.location_text = self.location
        if not self.location:
            self.location = self.location_text
        if self.closing_date and not self.application_deadline:
            self.application_deadline = self.closing_date
        if self.application_deadline and not self.closing_date:
            self.closing_date = self.application_deadline
        if self.primary_apply_url and not self.redirect_url:
            self.redirect_url = self.primary_apply_url
        if self.redirect_url and not self.primary_apply_url:
            self.primary_apply_url = self.redirect_url
        # Canonical changes and their post-save invalidation commit together.
        with transaction.atomic():
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} at {self.company}"

    @property
    def application_url(self):
        """Backward-compatible destination for legacy internal consumers."""
        if self.source_platform == 'CareerGPS Direct':
            return reverse('applications:start', args=[self.pk])
        return self.external_application_url

    @property
    def external_application_url(self):
        """Return only an active, job-specific route from a direct source."""
        if self.source_platform == 'CareerGPS Direct':
            return ''
        records = getattr(self, '_application_source_records', None)
        if records is None:
            records = self.source_records.filter(
                source__active=True,
                active_at_source=True,
            ).exclude(apply_url='').select_related(
                'source', 'source__greenhouse_board'
            ).order_by('source__priority', '-last_seen_at')
        for record in records:
            source = record.source
            if not (source.active and record.active_at_source and record.apply_url):
                continue
            direct_scraper = (
                source.source_type == 'scraper'
                and source.code in {'nedbank', 'discovery', 'sasol', 'capitec', 'woolworths'}
                and bool(source.company)
            )
            greenhouse = (
                source.source_type == 'api'
                and hasattr(source, 'greenhouse_board')
                and source.greenhouse_board.active
            )
            if not (direct_scraper or greenhouse):
                continue
            parsed = urlparse(record.apply_url.strip())
            if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
                continue
            if parsed.path.rstrip('/').lower() in {
                '', '/', '/jobs', '/job', '/search', '/careers',
                '/candidateapp/jobs/browse', '/candidateapp/jobs/categories',
            }:
                continue
            return record.apply_url
        return ''

    @property
    def is_catalogue_available(self):
        return Opportunity.objects.professionally_ready().filter(pk=self.pk).exists()
    
class SavedOpportunity(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['user', 'opportunity']
        ordering = ['-saved_at']

    def __str__(self):
        return f"{self.user.username}"

class CVtip(models.Model):
    
    TYPE_CHOICES = [ 
        ('internship', 'Internship'),
        ('learnership', 'Learnership'),
        ('graduate', 'Graduate Programme'),
        ('entry_level', 'Entry Level Position'),
        ('general', 'General')
    ]

    opportunity_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='general')
    related_skills = models.ManyToManyField(Skill, blank=True)
    title = models.CharField(max_length=200)
    content = models.TextField()
    example = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.opportunity_type})"
    
class MotivationalNudge(models.Model):

    TRIGGER_CHOICES = [('applications_milestone', 'Applications Milestone'),
                       ('inactivity', 'Inactivity'),
                       ('profile_incomplete', 'Profile Incomplete'),
                       ('first_save', 'First Saved Opportunity'),
                       ('first login', 'First Login'),
                       ('general', 'General')
                       ]
    
    trigger = models.CharField(max_length=50, choices=TRIGGER_CHOICES, default='general')
    trigger_value = models.IntegerField(null=True, blank=True)
    title = models.CharField(max_length=200)
    message = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.trigger})"
    
class Notification(models.Model):

    CATEGORY_CHOICES = [(key, label) for key, label in (
        ('stories', 'Story updates'), ('opportunities', 'Opportunity updates'),
        ('deadlines', 'Deadline reminders'), ('applications', 'Application updates'),
        ('interviews', 'Interview updates'), ('career_intelligence', 'Career Intelligence'),
        ('system', 'System / security'))]
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='system')
    event_key = models.CharField(max_length=200, blank=True, default='')

    TYPE_CHOICES = [
        (
            "story_approved",
            "Story approved",
        ),
        (
            "story_rejected",
            "Story rejected",
        ),
        (
            "opportunity_deadline",
            "Opportunity deadline",
        ),
        (
            "application_status",
            "Application status",
        ),
        (
            "system",
            "System",
        ),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
    )

    notification_type = models.CharField(
        max_length=40,
        choices=TYPE_CHOICES,
        default="system",
    )

    title = models.CharField(
        max_length=200,
    )

    message = models.TextField(
        blank=True,
    )

    # Where CareerGPS can take the user after they
    # have read the full notification.
    #
    # Example:
    # /stories/
    # /applications/15/
    target_url = models.CharField(
        max_length=500,
        blank=True,
    )

    is_read = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    read_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = [
            "-created_at",
            "-id",
        ]

        constraints = [models.UniqueConstraint(fields=['user', 'event_key'],
            condition=~models.Q(event_key=''), name='unique_user_notification_event')]

        indexes = [
            models.Index(
                fields=[
                    "user",
                    "is_read",
                    "created_at",
                ],
                name="core_notif_user_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{self.user_id} - "
            f"{self.title}"
        )

    def mark_read(self):
        if self.is_read:
            return

        self.is_read = True
        self.read_at = timezone.now()

        self.save(
            update_fields=[
                "is_read",
                "read_at",
            ]
        )

    @property
    def safe_target_url(self):
        from core.services.notifications import validate_target_url
        from django.core.exceptions import ValidationError
        try:
            return validate_target_url(self.target_url)
        except ValidationError:
            return ''


class Story(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("removed", "Removed by moderation"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    display_name = models.CharField(
        max_length=200,
    )

    qualification = models.CharField(
        max_length=200,
        blank=True,
    )

    institution = models.CharField(
        max_length=200,
        blank=True,
    )

    title = models.CharField(
        max_length=200,
    )

    content = models.TextField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    moderation_note = models.TextField(
        blank=True,
        help_text=(
            "Optional explanation shown to the author "
            "when a story is rejected."
        ),
    )

    submitted_at = models.DateTimeField(
        auto_now_add=True,
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = [
            "-submitted_at",
        ]

    def __str__(self):
        return self.title

    # Moderation transitions and notifications belong to services.moderation.
    # Ordinary saves have no notification side effects.


class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preferences')
    stories = models.BooleanField(default=True, verbose_name='Story updates')
    opportunities = models.BooleanField(default=True, verbose_name='Opportunity updates')
    deadlines = models.BooleanField(default=True, verbose_name='Deadline reminders')
    applications = models.BooleanField(default=True, verbose_name='Application updates')
    interviews = models.BooleanField(default=True, verbose_name='Interview updates')
    career_intelligence = models.BooleanField(default=True, verbose_name='Career Intelligence')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Notification preferences for user {self.user_id}'


class ReportRecord(models.Model):
    reporter = models.ForeignKey(User, on_delete=models.PROTECT, related_name='%(class)s_reports')
    details = models.TextField(blank=True, max_length=4000)
    status = models.CharField(max_length=20, default='open', db_index=True,
        choices=[('open', 'Open'), ('resolved', 'Resolved')])
    created_at = models.DateTimeField(auto_now_add=True)
    reviewer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='%(class)s_reviews')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(max_length=30, blank=True,
        choices=[('dismissed', 'Dismissed'), ('action_taken', 'Action taken')])
    moderator_note = models.TextField(blank=True, max_length=4000)

    class Meta:
        abstract = True
        ordering = ['status', '-created_at']


class StoryReport(ReportRecord):
    REASON_CHOICES = [
        ('harassment', 'Harassment / abusive content'), ('hate', 'Hate or discrimination'),
        ('privacy', 'Personal / private information'), ('spam', 'Spam / scam'),
        ('misleading', 'False or misleading content'), ('inappropriate', 'Explicit / inappropriate content'),
        ('other', 'Other')]
    story = models.ForeignKey(Story, on_delete=models.PROTECT, related_name='reports')
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)

    class Meta(ReportRecord.Meta):
        constraints = [models.UniqueConstraint(fields=['story', 'reporter'],
            condition=models.Q(status='open'), name='one_open_story_report_per_user')]

    def __str__(self):
        return f'Story report {self.pk}: {self.get_reason_display()}'


class OpportunityReport(ReportRecord):
    REASON_CHOICES = [
        ('scam', 'Possible scam'), ('money', 'Asks candidate for money'),
        ('fake_employer', 'Fake employer'), ('broken_link', 'Broken application link'),
        ('unavailable', 'Opportunity no longer available'), ('misleading', 'Misleading information'),
        ('duplicate', 'Duplicate listing'), ('wrong_location', 'Wrong location'),
        ('wrong_deadline', 'Wrong closing date'), ('inappropriate', 'Discriminatory / inappropriate content'),
        ('other', 'Other')]
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT, related_name='reports')
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)

    class Meta(ReportRecord.Meta):
        constraints = [models.UniqueConstraint(fields=['opportunity', 'reporter'],
            condition=models.Q(status='open'), name='one_open_opportunity_report_per_user')]

    def __str__(self):
        return f'Opportunity report {self.pk}: {self.get_reason_display()}'


class ModerationEvent(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ['-created_at', '-pk']


class StoryModerationEvent(ModerationEvent):
    story = models.ForeignKey(Story, on_delete=models.PROTECT, related_name='moderation_events')


class OpportunityModerationEvent(ModerationEvent):
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT, related_name='moderation_events')


class ProjectCapability(models.Model):
    """A candidate-confirmed project capability; project prose is never authority."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='capabilities')
    entered_name = models.CharField(max_length=200)
    normalized_name = models.CharField(max_length=200, db_index=True)
    skill_concept = models.ForeignKey(
        TaxonomyConcept, null=True, blank=True, on_delete=models.PROTECT,
        limit_choices_to={'kind': 'skill'}, related_name='project_capabilities',
    )
    evidence_statement = models.TextField()
    confirmed_by_user = models.BooleanField(default=False, db_index=True)
    provenance = models.CharField(max_length=30, default='user_entered', choices=[
        ('user_entered', 'Entered by user'),
        ('reviewed_import', 'Reviewed imported suggestion'),
    ])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['project', 'normalized_name'], name='unique_project_capability')]
        indexes = [models.Index(
            fields=['project', 'confirmed_by_user'], name='project_cap_confirmed_idx')]

    def save(self, *args, **kwargs):
        self.entered_name = re.sub(r'\s+', ' ', self.entered_name.strip())
        self.normalized_name = normalize_profile_value(self.entered_name)
        super().save(*args, **kwargs)


class TaxonomyCrosswalk(models.Model):
    """One official, directional mapping row with release provenance."""
    RELATIONS = [
        ('exact', 'Exact match'), ('close', 'Close match'),
        ('broad', 'Broad match'), ('narrow', 'Narrow match'),
        ('exact_isco', 'Exact ISCO mapping'),
    ]
    DIRECTIONS = [
        ('forward', 'Source to target'), ('reverse', 'Target to source'),
        ('bidirectional', 'Bidirectional'),
    ]
    STATUSES = [
        ('approved_identity', 'Approved identity'),
        ('approved_related', 'Approved related'),
        ('quarantined', 'Quarantined'),
        ('contextual', 'Contextual only'),
    ]
    source_release = models.ForeignKey(
        TaxonomyRelease, on_delete=models.PROTECT, related_name='crosswalks_from')
    source_concept = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT, related_name='crosswalks_from')
    source_external_id = models.CharField(max_length=500, default='')
    target_release = models.ForeignKey(
        TaxonomyRelease, on_delete=models.PROTECT, related_name='crosswalks_to')
    target_concept = models.ForeignKey(
        TaxonomyConcept, null=True, blank=True, on_delete=models.PROTECT,
        related_name='crosswalks_to')
    target_external_id = models.CharField(max_length=500, default='')
    relation_type = models.CharField(max_length=20, choices=RELATIONS)
    direction = models.CharField(max_length=20, choices=DIRECTIONS, default='forward')
    approval_status = models.CharField(max_length=24, choices=STATUSES, default='contextual')
    mapping_name = models.CharField(max_length=120)
    mapping_version = models.CharField(max_length=60)
    published_at = models.DateField(null=True, blank=True)
    source_reference = models.CharField(max_length=500)
    provenance = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['mapping_name', 'mapping_version', 'source_concept',
                    'target_concept', 'relation_type', 'source_reference'],
            name='unique_crosswalk_source_row')]
        indexes = [
            models.Index(fields=['source_concept', 'relation_type', 'active'], name='crosswalk_forward_idx'),
            models.Index(fields=['target_concept', 'relation_type', 'active'], name='crosswalk_reverse_idx'),
            models.Index(fields=['approval_status', 'active'], name='crosswalk_approval_idx'),
        ]


class TaxonomyReleaseSet(models.Model):
    """Compact fingerprint of the professional taxonomy/crosswalk knowledge set."""
    version = models.CharField(max_length=60, unique=True)
    fingerprint = models.CharField(max_length=64, unique=True)
    releases = models.ManyToManyField(TaxonomyRelease, related_name='release_sets')
    crosswalk_fingerprint = models.CharField(max_length=64)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['is_active'], condition=models.Q(is_active=True),
            name='one_active_taxonomy_set')]


class TaxonomyEmbedding(models.Model):
    """Retrieval-only embedding metadata; never an identity decision."""
    concept = models.ForeignKey(TaxonomyConcept, on_delete=models.CASCADE, related_name='embeddings')
    model_identifier = models.CharField(max_length=200)
    model_fingerprint = models.CharField(max_length=64)
    dimension = models.PositiveIntegerField()
    source_text_hash = models.CharField(max_length=64)
    vector = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['concept', 'model_identifier', 'model_fingerprint', 'source_text_hash'],
            name='unique_taxonomy_embedding')]
        indexes = [models.Index(
            fields=['model_identifier', 'model_fingerprint'], name='tax_embedding_model_idx')]


class TaxonomyOccupationContext(models.Model):
    """O*NET contextual knowledge retained for retrieval, never vacancy authority."""
    KINDS = [('skill', 'Skill'), ('knowledge', 'Knowledge'), ('activity', 'Activity')]
    occupation = models.ForeignKey(
        TaxonomyConcept, on_delete=models.CASCADE, related_name='occupation_context')
    context_type = models.CharField(max_length=20, choices=KINDS)
    element_id = models.CharField(max_length=40)
    element_name = models.CharField(max_length=300)
    scale_id = models.CharField(max_length=20)
    value = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    provenance = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['occupation', 'context_type', 'element_id', 'scale_id'],
            name='unique_occupation_context')]
        indexes = [models.Index(
            fields=['occupation', 'context_type'], name='occupation_context_idx')]


class _ImmutableAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('Immutable clean records cannot be updated through a queryset.')

    def delete(self):
        raise ValueError('Immutable clean records cannot be deleted.')


class _RevisionQuerySet(_ImmutableAuditQuerySet):
    def update(self, **kwargs):
        allowed = {'status', 'activated_at', 'superseded_at'}
        if set(kwargs) - allowed:
            raise ValueError('Only clean-revision lifecycle fields may be updated.')
        return models.QuerySet.update(self, **kwargs)


class OpportunityCleanAttempt(models.Model):
    """Every submitted external-cleaner result, including quarantined failures."""
    STATUSES = [
        ('received', 'Received'), ('quarantined', 'Quarantined'),
        ('validated', 'Validated'), ('activated', 'Activated'),
    ]
    opportunity = models.ForeignKey(
        Opportunity, null=True, blank=True, on_delete=models.PROTECT, related_name='clean_attempts')
    source_record = models.ForeignKey(
        'ingestion.OpportunitySourceRecord', null=True, blank=True,
        on_delete=models.PROTECT, related_name='clean_attempts')
    raw_opportunity = models.ForeignKey(
        'ingestion.RawOpportunity', null=True, blank=True,
        on_delete=models.PROTECT, related_name='clean_attempts')
    status = models.CharField(max_length=20, choices=STATUSES, default='received', db_index=True)
    schema_version = models.CharField(max_length=30, blank=True)
    source_input_hash = models.CharField(max_length=64, blank=True, db_index=True)
    payload_hash = models.CharField(max_length=64, blank=True, db_index=True)
    cleaner_name = models.CharField(max_length=120, blank=True)
    cleaner_version = models.CharField(max_length=60, blank=True)
    cleaner_run_id = models.CharField(max_length=120, blank=True)
    raw_payload = models.JSONField(default=dict)
    validation_errors = models.JSONField(default=list)
    submitted_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    objects = _ImmutableAuditQuerySet.as_manager()

    class Meta:
        ordering = ['-submitted_at', '-pk']
        indexes = [
            models.Index(fields=['opportunity', 'status'], name='clean_attempt_opp_state_idx'),
            models.Index(fields=['source_record', 'source_input_hash'], name='clean_attempt_source_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.only(
                'opportunity_id', 'source_record_id', 'raw_opportunity_id',
                'schema_version', 'source_input_hash', 'raw_payload').get(pk=self.pk)
            protected = ('opportunity_id', 'source_record_id', 'raw_opportunity_id',
                         'schema_version', 'source_input_hash', 'raw_payload')
            if any(getattr(self, field) != getattr(original, field) for field in protected):
                raise ValueError('Clean attempts are append-only; submitted identity and payload are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Clean attempts are immutable audit records and cannot be deleted.')


class OpportunityCleanRevision(models.Model):
    """Immutable, validated professional facts for one exact source version."""
    STATUSES = [
        ('validated', 'Validated'), ('active', 'Active'), ('superseded', 'Superseded')]
    SENIORITY = [
        ('NON_SENIOR', 'Non-senior'), ('OBVIOUS_SENIOR', 'Obvious senior'),
        ('UNCERTAIN', 'Uncertain')]
    WORK_ARRANGEMENTS = [
        ('ONSITE', 'On-site'), ('HYBRID', 'Hybrid'), ('REMOTE', 'Remote'),
        ('FIELD_BASED', 'Field based'), ('OTHER', 'Other'), ('UNKNOWN', 'Unknown')]
    OPPORTUNITY_KINDS = [
        ('VACANCY', 'Vacancy'), ('INTERNSHIP', 'Internship'),
        ('LEARNERSHIP', 'Learnership'), ('APPRENTICESHIP', 'Apprenticeship'),
        ('GRADUATE_PROGRAMME', 'Graduate programme'), ('BURSARY', 'Bursary'),
        ('OTHER', 'Other')]
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.PROTECT, related_name='clean_revisions')
    attempt = models.OneToOneField(
        OpportunityCleanAttempt, on_delete=models.PROTECT, related_name='revision')
    source_record = models.ForeignKey(
        'ingestion.OpportunitySourceRecord', on_delete=models.PROTECT,
        related_name='clean_revisions')
    raw_opportunity = models.ForeignKey(
        'ingestion.RawOpportunity', on_delete=models.PROTECT, related_name='clean_revisions')
    revision_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUSES, default='validated', db_index=True)
    schema_version = models.CharField(max_length=30)
    source_input_hash = models.CharField(max_length=64, db_index=True)
    payload_hash = models.CharField(max_length=64, db_index=True)
    cleaner_name = models.CharField(max_length=120)
    cleaner_version = models.CharField(max_length=60)
    cleaner_cleaned_at = models.DateTimeField()
    cleaner_run_id = models.CharField(max_length=120)
    source_name = models.CharField(max_length=120)
    external_opportunity_id = models.CharField(max_length=255)
    source_url = models.URLField(blank=True)
    source_captured_at = models.DateTimeField()
    cleaned_title = models.CharField(max_length=300)
    normalized_title = models.CharField(max_length=300, db_index=True, default='')
    company = models.CharField(max_length=300)
    location = models.JSONField(default=dict)
    employment_type = models.JSONField(default=list)
    work_arrangement = models.CharField(max_length=20, choices=WORK_ARRANGEMENTS)
    opportunity_kind = models.CharField(max_length=30, choices=OPPORTUNITY_KINDS)
    posted_at = models.CharField(max_length=40, blank=True)
    posted_sort_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closing_at = models.CharField(max_length=40, blank=True)
    closing_sort_at = models.DateTimeField(null=True, blank=True, db_index=True)
    salary = models.JSONField(null=True, blank=True)
    cleaned_description = models.TextField()
    seniority_label = models.CharField(max_length=20, choices=SENIORITY, db_index=True)
    seniority_reason = models.TextField()
    seniority_evidence = models.JSONField(default=list)
    canonical_payload = models.JSONField(default=dict)
    activated_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = _RevisionQuerySet.as_manager()

    class Meta:
        ordering = ['opportunity_id', '-revision_number']
        constraints = [
            models.UniqueConstraint(fields=['opportunity', 'revision_number'], name='unique_clean_revision_number'),
            models.UniqueConstraint(
                fields=['source_record', 'source_input_hash', 'payload_hash'],
                name='unique_clean_source_payload'),
        ]
        indexes = [
            models.Index(fields=['opportunity', 'status'], name='clean_revision_opp_state_idx'),
            models.Index(fields=['status', 'seniority_label', '-posted_sort_at'], name='clean_catalogue_order_idx'),
            models.Index(fields=['source_input_hash', 'payload_hash'], name='clean_revision_hash_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            lifecycle = {'status', 'activated_at', 'superseded_at'}
            original = type(self).objects.get(pk=self.pk)
            changed = [field.attname for field in self._meta.concrete_fields
                       if field.name not in lifecycle and field.name not in {'id'}
                       and getattr(self, field.attname) != getattr(original, field.attname)]
            if changed:
                raise ValueError(f'Clean revisions are immutable; changed fields: {", ".join(changed)}')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Clean revisions are immutable and cannot be deleted.')


class CleanRequirementGroup(models.Model):
    revision = models.ForeignKey(
        OpportunityCleanRevision, on_delete=models.PROTECT, related_name='requirement_groups')
    group_id = models.CharField(max_length=100)
    operator = models.CharField(max_length=8, choices=[('OR', 'OR')])
    objects = _ImmutableAuditQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['revision', 'group_id'], name='unique_clean_requirement_group')]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('Clean requirement groups are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Clean requirement groups are immutable.')


class CleanOpportunityCriterion(models.Model):
    TYPES = [
        ('SKILL', 'Skill'), ('QUALIFICATION', 'Qualification'),
        ('EXPERIENCE', 'Experience'), ('CERTIFICATION', 'Certification'),
        ('LICENCE', 'Licence'), ('LANGUAGE', 'Language'),
        ('ELIGIBILITY', 'Eligibility'), ('OTHER', 'Other')]
    LEVELS = [
        ('REQUIRED', 'Required'), ('PREFERRED', 'Preferred'),
        ('INFORMATIONAL', 'Informational'), ('UNKNOWN', 'Unknown')]
    SIGNIFICANCE = [
        ('CRUCIAL', 'Crucial'), ('CORE', 'Core'),
        ('SUPPORTING', 'Supporting'), ('UNKNOWN', 'Unknown')]
    revision = models.ForeignKey(
        OpportunityCleanRevision, on_delete=models.PROTECT, related_name='criteria')
    criterion_id = models.CharField(max_length=100)
    criterion_type = models.CharField(max_length=24, choices=TYPES, db_index=True)
    normalized_value = models.TextField()
    raw_value = models.TextField()
    requirement_level = models.CharField(max_length=20, choices=LEVELS, db_index=True)
    significance = models.CharField(max_length=20, choices=SIGNIFICANCE, db_index=True)
    source_section = models.CharField(max_length=200)
    requirement_group = models.ForeignKey(
        CleanRequirementGroup, null=True, blank=True, on_delete=models.PROTECT,
        related_name='criteria')
    qualification = models.JSONField(null=True, blank=True)
    experience = models.JSONField(null=True, blank=True)
    language = models.JSONField(null=True, blank=True)
    objects = _ImmutableAuditQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['revision', 'criterion_id'], name='unique_clean_criterion_id')]
        indexes = [
            models.Index(
                fields=['revision', 'criterion_type', 'requirement_level', 'significance'],
                name='clean_criterion_policy_idx'),
            models.Index(fields=['criterion_type', 'normalized_value'], name='clean_criterion_value_idx'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('Clean criteria are immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Clean criteria are immutable.')


class CleanCriterionEvidence(models.Model):
    criterion = models.ForeignKey(
        CleanOpportunityCriterion, on_delete=models.PROTECT, related_name='evidence')
    ordinal = models.PositiveSmallIntegerField()
    text = models.TextField()
    start_char = models.PositiveIntegerField(null=True, blank=True)
    end_char = models.PositiveIntegerField(null=True, blank=True)
    objects = _ImmutableAuditQuerySet.as_manager()

    class Meta:
        ordering = ['ordinal', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['criterion', 'ordinal'], name='unique_clean_evidence_ordinal')]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError('Clean criterion evidence is immutable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Clean criterion evidence is immutable.')


class OpportunityOccupationResolution(models.Model):
    STATES = [('RESOLVED', 'Resolved'), ('PARTIAL', 'Partial'),
              ('AMBIGUOUS', 'Ambiguous'), ('UNRESOLVED', 'Unresolved')]
    revision = models.OneToOneField(
        OpportunityCleanRevision, on_delete=models.CASCADE, related_name='occupation_resolution')
    state = models.CharField(max_length=20, choices=STATES)
    resolver_version = models.CharField(max_length=40)
    dependency_hash = models.CharField(max_length=64, db_index=True)
    diagnostics = models.JSONField(default=dict)
    resolved_at = models.DateTimeField(auto_now=True)


class OpportunityOccupationIdentity(models.Model):
    resolution = models.ForeignKey(
        OpportunityOccupationResolution, on_delete=models.CASCADE, related_name='identities')
    concept = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT, related_name='clean_opportunity_identities')
    route = models.CharField(max_length=40)
    source_text = models.CharField(max_length=300)
    provenance = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['resolution', 'concept'], name='unique_opp_resolution_identity')]
        indexes = [models.Index(fields=['concept', 'resolution'], name='opp_identity_concept_idx')]


class CareerTargetResolution(models.Model):
    STATES = [('RESOLVED', 'Resolved'), ('PARTIAL', 'Partial'),
              ('AMBIGUOUS', 'Ambiguous'), ('UNRESOLVED', 'Unresolved')]
    preference = models.ForeignKey(
        CareerPreference, on_delete=models.CASCADE, related_name='target_resolutions')
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='target_resolutions')
    ordinal = models.PositiveSmallIntegerField()
    authored_text = models.CharField(max_length=300)
    normalized_text = models.CharField(max_length=300, db_index=True)
    state = models.CharField(max_length=20, choices=STATES)
    resolver_version = models.CharField(max_length=40)
    dependency_hash = models.CharField(max_length=64, db_index=True)
    diagnostics = models.JSONField(default=dict)
    resolved_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['preference', 'ordinal'], name='unique_preference_target_ordinal')]
        indexes = [models.Index(fields=['profile', 'state'], name='target_resolution_state_idx')]


class CareerTargetIdentity(models.Model):
    resolution = models.ForeignKey(
        CareerTargetResolution, on_delete=models.CASCADE, related_name='identities')
    concept = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT, related_name='career_target_identities')
    route = models.CharField(max_length=40)
    provenance = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['resolution', 'concept'], name='unique_target_resolution_identity')]
        indexes = [models.Index(fields=['concept', 'resolution'], name='target_identity_concept_idx')]


class OpportunityTitleGateDecision(models.Model):
    """Versioned title-only seniority decision over an immutable clean revision."""
    DECISIONS = [('ALLOW', 'Allow'), ('BLOCK', 'Block')]
    revision = models.OneToOneField(
        OpportunityCleanRevision, on_delete=models.CASCADE,
        related_name='title_gate_decision')
    decision = models.CharField(max_length=12, choices=DECISIONS, db_index=True)
    reason_code = models.CharField(max_length=80)
    matched_construction = models.CharField(max_length=200, blank=True)
    exception_applied = models.CharField(max_length=80, blank=True)
    policy_version = models.CharField(max_length=40)
    dependency_hash = models.CharField(max_length=64, db_index=True)
    evaluated_at = models.DateTimeField(auto_now=True)


class OpportunityTitleResolution(models.Model):
    """Shadow container for independent source resolution of one cleaned title."""
    STATES = [('READY', 'Ready'), ('BLOCKED_TITLE', 'Blocked by title gate')]
    revision = models.OneToOneField(
        OpportunityCleanRevision, on_delete=models.CASCADE,
        related_name='independent_occupation_resolution')
    state = models.CharField(max_length=24, choices=STATES, db_index=True)
    dependency_hash = models.CharField(max_length=64, db_index=True)
    diagnostics = models.JSONField(default=dict)
    resolved_at = models.DateTimeField(auto_now=True)


class OccupationSourceResolution(models.Model):
    """One taxonomy source's answer; crosswalks never participate here.

    A title may match any number of concepts inside one taxonomy.  Every match
    is a retained occupation result, recorded on its own
    ``OccupationSourceResolutionCandidate`` row with its own code, label route,
    evidence, status, and taxonomy version.  Several results are not ambiguity
    and no favourite is chosen: ``selected_concept`` is populated only when the
    source returned exactly one result, so nothing downstream can mistake it
    for a preferred occupation.  ``AMBIGUOUS`` remains a valid stored choice for
    rows written before multi-result retention; the resolver no longer produces
    it.
    """
    SOURCES = [('ofo', 'South African OFO'), ('esco', 'ESCO')]
    STATES = [('RESOLVED', 'Resolved'), ('UNRESOLVED', 'Unresolved'),
              ('AMBIGUOUS', 'Ambiguous')]
    target_resolution = models.ForeignKey(
        CareerTargetResolution, null=True, blank=True, on_delete=models.CASCADE,
        related_name='source_resolutions')
    opportunity_resolution = models.ForeignKey(
        OpportunityTitleResolution, null=True, blank=True, on_delete=models.CASCADE,
        related_name='source_resolutions')
    source = models.CharField(max_length=12, choices=SOURCES)
    taxonomy_release = models.ForeignKey(
        TaxonomyRelease, null=True, blank=True, on_delete=models.PROTECT,
        related_name='occupation_source_resolutions')
    resolver_version = models.CharField(max_length=40)
    label_policy_version = models.CharField(max_length=40)
    original_input = models.CharField(max_length=300)
    normalized_input = models.CharField(max_length=300, db_index=True)
    input_hash = models.CharField(max_length=64, db_index=True)
    state = models.CharField(max_length=20, choices=STATES, db_index=True)
    result_count = models.PositiveSmallIntegerField(default=0)
    selected_concept = models.ForeignKey(
        TaxonomyConcept, null=True, blank=True, on_delete=models.PROTECT,
        related_name='selected_source_resolutions')
    matched_label = models.CharField(max_length=300, blank=True)
    label_type = models.CharField(max_length=20, blank=True)
    label_route = models.CharField(max_length=40, blank=True)
    provenance = models.JSONField(default=dict)
    diagnostics = models.JSONField(default=dict)
    dependency_hash = models.CharField(max_length=64, db_index=True)
    resolved_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(target_resolution__isnull=False)
                     & models.Q(opportunity_resolution__isnull=True))
                    | (models.Q(target_resolution__isnull=True)
                       & models.Q(opportunity_resolution__isnull=False))
                ),
                name='source_resolution_one_parent'),
            models.UniqueConstraint(
                fields=['target_resolution', 'source'],
                condition=models.Q(target_resolution__isnull=False),
                name='unique_target_source_resolution'),
            models.UniqueConstraint(
                fields=['opportunity_resolution', 'source'],
                condition=models.Q(opportunity_resolution__isnull=False),
                name='unique_vacancy_source_resolution'),
        ]
        indexes = [
            models.Index(fields=['source', 'state'], name='occ_source_state_idx'),
            models.Index(fields=['selected_concept', 'source'], name='occ_source_concept_idx'),
        ]


class OccupationSourceResolutionCandidate(models.Model):
    """One retained occupation result of one source, never a rejected runner-up.

    Every concept a title matched inside one taxonomy gets its own row, with the
    code, taxonomy, label evidence, status, and versions that produced it, so a
    downstream reader can use all of them without re-resolving anything.
    ``selected`` marks the single-result case only: when a title matches several
    concepts no row is selected, because none of them is preferred.
    """
    RETAINED = 'RETAINED'
    resolution = models.ForeignKey(
        OccupationSourceResolution, on_delete=models.CASCADE,
        related_name='candidates')
    concept = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT,
        related_name='source_resolution_candidates')
    ordinal = models.PositiveSmallIntegerField()
    matched_label = models.CharField(max_length=300)
    label_type = models.CharField(max_length=20)
    label_route = models.CharField(max_length=40)
    code = models.CharField(max_length=80, blank=True)
    preferred_label = models.CharField(max_length=300, blank=True)
    taxonomy_source = models.CharField(max_length=12, blank=True)
    taxonomy_version = models.CharField(max_length=40, blank=True)
    resolver_version = models.CharField(max_length=40, blank=True)
    label_policy_version = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, default=RETAINED)
    selected = models.BooleanField(default=False)
    provenance = models.JSONField(default=dict)

    class Meta:
        ordering = ['ordinal', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['resolution', 'concept'], name='unique_source_resolution_candidate')]
        indexes = [models.Index(
            fields=['concept', 'resolution'], name='occ_candidate_concept_idx')]


class OccupationRelationshipPolicy(models.Model):
    """CareerGPS candidate-facing policy layered over raw taxonomy evidence."""
    RELATIONSHIPS = [
        ('SAME_OCCUPATION', 'Same occupation'),
        ('SAME_OCCUPATION_SPECIALISATION', 'Same-occupation specialisation'),
        ('CLOSE_NEIGHBOUR', 'Close occupational neighbour'),
        ('SAFE_BROADER_NARROWER', 'Safe broader/narrower relationship'),
        ('WEAK_ADJACENCY', 'Weak adjacency'),
        ('UNSUPPORTED', 'Unsupported relationship'),
    ]
    DIRECTIONS = [('forward', 'Forward'), ('reverse', 'Reverse'),
                  ('bidirectional', 'Bidirectional')]
    subject_concept = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT,
        related_name='relationship_policies_as_subject')
    object_concept = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT,
        related_name='relationship_policies_as_object')
    relationship_type = models.CharField(max_length=40, choices=RELATIONSHIPS)
    direction = models.CharField(max_length=20, choices=DIRECTIONS, default='forward')
    admission_allowed = models.BooleanField(default=False, db_index=True)
    policy_version = models.CharField(max_length=40)
    rationale = models.TextField()
    provenance = models.JSONField(default=dict)
    crosswalk = models.ForeignKey(
        TaxonomyCrosswalk, null=True, blank=True, on_delete=models.PROTECT,
        related_name='candidate_relationship_policies')
    taxonomy_relation = models.ForeignKey(
        TaxonomyRelation, null=True, blank=True, on_delete=models.PROTECT,
        related_name='candidate_relationship_policies')
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['policy_version', 'subject_concept', 'object_concept',
                    'relationship_type', 'direction'],
            name='unique_candidate_relationship_policy')]
        indexes = [
            models.Index(
                fields=['policy_version', 'active', 'admission_allowed'],
                name='candidate_rel_policy_idx'),
        ]


class OccupationRelationshipRule(models.Model):
    """Reusable candidate-facing policy over one direct trusted source fact."""
    EVIDENCE_SOURCES = [
        ('taxonomy_relation', 'Taxonomy relation'),
        ('crosswalk', 'Taxonomy crosswalk'),
    ]
    TAXONOMY_SOURCES = [('', 'Any supported source'), ('ofo', 'OFO'), ('esco', 'ESCO')]
    CANDIDATE_CLASSES = [('EXACT', 'Exact'), ('RELATED', 'Related'),
                         ('UNRESOLVED', 'Non-admitting')]
    DIRECTIONS = [('forward', 'Forward'), ('reverse', 'Reverse'),
                  ('bidirectional', 'Bidirectional')]
    policy_version = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    evidence_source = models.CharField(max_length=30, choices=EVIDENCE_SOURCES)
    taxonomy_source = models.CharField(max_length=12, choices=TAXONOMY_SOURCES, blank=True)
    evidence_relation_type = models.CharField(max_length=40)
    candidate_relationship_type = models.CharField(
        max_length=40, choices=OccupationRelationshipPolicy.RELATIONSHIPS)
    direction = models.CharField(max_length=20, choices=DIRECTIONS)
    candidate_class = models.CharField(max_length=20, choices=CANDIDATE_CLASSES)
    admission_allowed = models.BooleanField(default=False, db_index=True)
    rationale = models.TextField()
    provenance = models.JSONField(default=dict)
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['policy_version', 'name'],
            name='unique_candidate_relationship_rule')]
        indexes = [models.Index(
            fields=['policy_version', 'active', 'evidence_source'],
            name='candidate_rel_rule_idx')]


class TargetOpportunityRelationshipAssessment(models.Model):
    """One target/job pair: the aggregate of every relationship it produced.

    Exactly one row exists per authored target and clean job revision, because a
    job is still one job.  It summarises the strongest reading of the pair for
    ranking and carries the per-source payload, while
    ``TargetOpportunityRelationshipFinding`` retains every individual ESCO and
    OFO relationship the pair produced.  ``UNRELATED`` is reserved for explicit
    negative evidence from every configured source; absent or uncertain
    evidence stays ``UNRESOLVED``.
    """
    CLASSES = [('EXACT', 'Exact'), ('RELATED', 'Related'),
               ('UNRESOLVED', 'Unresolved'), ('CONFLICT', 'Conflict'),
               ('UNRELATED', 'Unrelated'),
               ('TITLE_MATCH', 'Title match only (not ESCO/OFO confirmed)')]
    TIERS = [
        ('EXACT_MULTI_RESOLVER', 'Exact: multiple resolvers', 1),
        ('EXACT_SINGLE_RESOLVER', 'Exact: one resolver', 2),
        ('EXACT_ALIAS_SPECIALISATION', 'Exact: alias or specialisation', 3),
        ('EXACT_STAGE_VARIANT',
         'Historical: title-only stage variant, never taxonomy-confirmed', 4),
        ('TITLE_ONLY', 'Title match only: not ESCO/OFO confirmed', 4),
        ('RELATED_MULTI_RESOLVER', 'Related: multiple resolvers', 1),
        ('RELATED_SINGLE_RESOLVER', 'Related: one resolver', 2),
        ('RELATED_CLOSE_NEIGHBOUR', 'Related: close neighbour', 3),
        ('RELATED_SAFE_HIERARCHY', 'Related: safe hierarchy', 4),
        ('NONE', 'No admitted relationship tier', 99),
    ]
    TIER_CHOICES = [(key, label) for key, label, _rank in TIERS]
    target_resolution = models.ForeignKey(
        CareerTargetResolution, on_delete=models.CASCADE,
        related_name='opportunity_relationships')
    revision = models.ForeignKey(
        OpportunityCleanRevision, on_delete=models.CASCADE,
        related_name='target_relationships')
    final_class = models.CharField(max_length=20, choices=CLASSES, db_index=True)
    evidence_tier = models.CharField(max_length=50, choices=TIER_CHOICES, default='NONE')
    tier_rank = models.PositiveSmallIntegerField(default=99)
    reason_code = models.CharField(max_length=100)
    # The counts below never replace the relationship rows: they are a summary
    # of them, kept so an already-built feed can rank a job without reading
    # children. Every individual ESCO and OFO relationship of this pair lives on
    # ``TargetOpportunityRelationshipFinding``.
    target_result_count = models.PositiveSmallIntegerField(default=0)
    job_result_count = models.PositiveSmallIntegerField(default=0)
    pairing_count = models.PositiveSmallIntegerField(default=0)
    relationship_count = models.PositiveSmallIntegerField(default=0)
    exact_relationship_count = models.PositiveSmallIntegerField(default=0)
    related_relationship_count = models.PositiveSmallIntegerField(default=0)
    denied_pairing_count = models.PositiveSmallIntegerField(default=0)
    inconclusive_pairing_count = models.PositiveSmallIntegerField(default=0)
    ofo_comparison = models.JSONField(default=dict)
    esco_comparison = models.JSONField(default=dict)
    policy_evidence = models.JSONField(default=list)
    conflicting_evidence = models.JSONField(default=list)
    aggregation_version = models.CharField(max_length=50)
    relationship_policy_version = models.CharField(max_length=50)
    dependency_hash = models.CharField(max_length=64, db_index=True)
    diagnostics = models.JSONField(default=dict)
    assessed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['target_resolution', 'revision'],
            name='unique_target_vacancy_relationship')]
        indexes = [
            models.Index(
                fields=['final_class', 'tier_rank'], name='target_rel_class_tier_idx'),
            models.Index(
                fields=['target_resolution', 'final_class'], name='target_rel_target_idx'),
        ]


class TargetOpportunityRelationshipFinding(models.Model):
    """One occupational relationship between one target result and one job result.

    A target role and a job title each carry 0, 1, or many retained occupation
    results per taxonomy, and every retained result is compared under the
    applicable relationship rules.  This table keeps the outcome of every such
    comparison instead of collapsing a pair to a single yes/no answer, so a job
    linked to a target by three ESCO occupations and one OFO occupation keeps
    all four connections.  ESCO and OFO are recorded independently and neither
    can erase the other.

    ``relationship_class`` separates an established relationship from absent or
    contradictory evidence: ``EXACT``/``RELATED`` are the retained
    relationships, ``DENIED`` is an explicit negative policy finding, and
    ``NOT_ESTABLISHED`` means no evidence connected the two concepts, which is
    never reported as "unrelated".
    """
    CLASSES = [('EXACT', 'Exact'), ('RELATED', 'Related'),
               ('NOT_ESTABLISHED', 'No relationship evidence'),
               ('DENIED', 'Explicitly unsupported by policy')]
    TIER_CHOICES = TargetOpportunityRelationshipAssessment.TIER_CHOICES
    assessment = models.ForeignKey(
        TargetOpportunityRelationshipAssessment, on_delete=models.CASCADE,
        related_name='findings')
    source = models.CharField(max_length=12,
                              choices=OccupationSourceResolution.SOURCES)
    ordinal = models.PositiveSmallIntegerField(default=0)
    target_concept = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT,
        related_name='target_relationship_findings')
    target_code = models.CharField(max_length=80, blank=True)
    target_label = models.CharField(max_length=300, blank=True)
    target_matched_label = models.CharField(max_length=300, blank=True)
    target_label_type = models.CharField(max_length=20, blank=True)
    target_label_route = models.CharField(max_length=40, blank=True)
    target_resolution_state = models.CharField(max_length=20, blank=True)
    target_taxonomy_version = models.CharField(max_length=40, blank=True)
    job_concept = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT,
        related_name='job_relationship_findings')
    job_code = models.CharField(max_length=80, blank=True)
    job_label = models.CharField(max_length=300, blank=True)
    job_matched_label = models.CharField(max_length=300, blank=True)
    job_label_type = models.CharField(max_length=20, blank=True)
    job_label_route = models.CharField(max_length=40, blank=True)
    job_resolution_state = models.CharField(max_length=20, blank=True)
    job_taxonomy_version = models.CharField(max_length=40, blank=True)
    relationship_class = models.CharField(max_length=20, choices=CLASSES,
                                          db_index=True)
    relationship_type = models.CharField(max_length=40, blank=True)
    route = models.CharField(max_length=60, blank=True)
    reason_code = models.CharField(max_length=100)
    evidence_tier = models.CharField(max_length=50, choices=TIER_CHOICES,
                                     default='NONE')
    tier_rank = models.PositiveSmallIntegerField(default=99)
    evidence = models.JSONField(default=dict)
    aggregation_version = models.CharField(max_length=50)
    relationship_policy_version = models.CharField(max_length=50)
    assessed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['source', 'ordinal', 'pk']
        constraints = [models.UniqueConstraint(
            fields=['assessment', 'source', 'target_concept', 'job_concept'],
            name='unique_pair_relationship_finding')]
        indexes = [
            models.Index(fields=['assessment', 'source', 'ordinal'],
                         name='rel_finding_assessment_idx'),
            models.Index(fields=['source', 'relationship_class'],
                         name='rel_finding_source_class_idx'),
            models.Index(fields=['target_concept', 'relationship_class'],
                         name='rel_finding_target_concept_idx'),
            models.Index(fields=['job_concept', 'relationship_class'],
                         name='rel_finding_job_concept_idx'),
        ]


class OccupationDefiningCriterion(models.Model):
    """Human-approved Profile-Relevant policy entry; never auto-seeded."""
    ROUTES = [('strong_single', 'Strong single evidence'),
              ('credential', 'Credential'), ('experience', 'Experience'),
              ('multi_evidence', 'Multi-evidence role link')]
    policy_version = models.CharField(max_length=40)
    occupation = models.ForeignKey(
        TaxonomyConcept, on_delete=models.PROTECT, related_name='defining_criteria')
    criterion_type = models.CharField(max_length=24, choices=CleanOpportunityCriterion.TYPES)
    normalized_value = models.CharField(max_length=500)
    route = models.CharField(max_length=30, choices=ROUTES)
    approved = models.BooleanField(default=False, db_index=True)
    rationale = models.TextField()
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['policy_version', 'occupation', 'criterion_type', 'normalized_value', 'route'],
            name='unique_defining_criterion')]


class ProfileOpportunityEvaluation(models.Model):
    STATES = [('CURRENT', 'Current'), ('STALE', 'Stale'), ('FAILED', 'Failed')]
    CATEGORIES = [('TARGET', 'Target'), ('RELATED', 'Related'),
                  ('PROFILE_RELEVANT', 'Profile relevant')]
    BANDS = [(value, value) for value in ('A', 'B', 'C', 'E', 'D', 'F')]
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='opportunity_evaluations')
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name='profile_evaluations')
    clean_revision = models.ForeignKey(
        OpportunityCleanRevision, on_delete=models.PROTECT, related_name='profile_evaluations')
    state = models.CharField(max_length=12, choices=STATES, default='CURRENT', db_index=True)
    dependency_hash = models.CharField(max_length=64, db_index=True)
    discovery_category = models.CharField(max_length=24, choices=CATEGORIES)
    discovery_rank = models.PositiveSmallIntegerField()
    band = models.CharField(max_length=1, choices=BANDS)
    band_rank = models.PositiveSmallIntegerField()
    required_important_coverage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    preferred_important_coverage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    matched_important_category_count = models.PositiveSmallIntegerField(default=0)
    found_important_category_count = models.PositiveSmallIntegerField(default=0)
    positive_important_count = models.PositiveIntegerField(default=0)
    supporting_found_count = models.PositiveIntegerField(default=0)
    strongest_important_match = models.CharField(max_length=20, blank=True)
    major_gap_count = models.PositiveIntegerField(default=0)
    gate_gap_count = models.PositiveIntegerField(default=0)
    foundation_gap_count = models.PositiveIntegerField(default=0)
    professional_score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    discovery_reasons = models.JSONField(default=list)
    triggering_target_resolution_ids = models.JSONField(default=list)
    criterion_results = models.JSONField(default=list)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['profile', 'opportunity'], name='unique_profile_opportunity_eval')]
        indexes = [
            models.Index(
                fields=['profile', 'state', 'discovery_rank', 'band_rank'],
                name='profile_eval_feed_idx'),
            models.Index(fields=['profile', 'dependency_hash'], name='profile_eval_dependency_idx'),
            models.Index(fields=['opportunity', 'state'], name='opportunity_eval_state_idx'),
        ]


class ProfileEvaluationGeneration(models.Model):
    STATES = [
        ('PENDING', 'Pending'), ('RUNNING', 'Running'), ('COMPLETE', 'Complete'),
        ('BUILDING', 'Building'), ('ACTIVE', 'Active'),
        ('SUPERSEDED', 'Superseded'), ('FAILED', 'Failed'),
    ]
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='evaluation_generations')
    dependency_hash = models.CharField(max_length=64)
    catalogue_fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=12, choices=STATES, default='PENDING', db_index=True)
    generation_version = models.CharField(max_length=40, default='legacy-v1', db_index=True)
    build_reason = models.CharField(max_length=100, blank=True)
    intent_dependency_hash = models.CharField(max_length=64, blank=True, db_index=True)
    evidence_dependency_hash = models.CharField(max_length=64, blank=True, db_index=True)
    build_token = models.CharField(max_length=64, blank=True)
    evaluation_count = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    diagnostics = models.JSONField(default=dict)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    failure_diagnostics = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['profile', 'dependency_hash', 'catalogue_fingerprint'],
                name='unique_profile_eval_generation'),
            models.UniqueConstraint(
                fields=['profile'], condition=models.Q(state='ACTIVE'),
                name='one_active_feed_generation_per_profile'),
        ]
        indexes = [models.Index(fields=['profile', 'state'], name='profile_generation_state_idx')]


class ProfileFeedState(models.Model):
    STATES = [
        ('READY', 'Ready'),
        ('BUILDING', 'Building'),
        ('UPDATING_INTENT', 'Updating career targets'),
        ('UPDATING_EVIDENCE', 'Updating current-fit evidence'),
        ('UPDATING_CATALOGUE', 'Updating catalogue evidence'),
        ('UPDATING_POLICY', 'Updating occupational policy'),
        ('FAILED', 'Latest build failed'),
        ('NO_TARGETS', 'No authored targets'),
    ]
    profile = models.OneToOneField(
        Profile, on_delete=models.CASCADE, related_name='feed_state')
    active_generation = models.ForeignKey(
        ProfileEvaluationGeneration, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='active_for_states')
    state = models.CharField(max_length=30, choices=STATES, default='BUILDING', db_index=True)
    pending_reason = models.CharField(max_length=100, blank=True)
    last_error = models.TextField(blank=True)
    diagnostics = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)


class ProfileFeedEvaluation(models.Model):
    CATEGORIES = [
        ('EXACT', 'Exact match'), ('RELATED', 'Related role'),
        ('CONFLICT', 'Conflicting occupation evidence'),
        ('UNRESOLVED', 'Occupation relationship not established'),
        ('TITLE_MATCH', 'Title match only - not confirmed by ESCO or OFO')]
    BANDS = [(value, value) for value in ('A', 'B', 'C', 'E', 'D', 'F')]
    generation = models.ForeignKey(
        ProfileEvaluationGeneration, on_delete=models.CASCADE,
        related_name='v2_evaluations')
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.CASCADE, related_name='v2_feed_evaluations')
    clean_revision = models.ForeignKey(
        OpportunityCleanRevision, on_delete=models.PROTECT,
        related_name='v2_feed_evaluations')
    relationship_assessment = models.ForeignKey(
        TargetOpportunityRelationshipAssessment, on_delete=models.PROTECT,
        related_name='v2_feed_evaluations')
    assessment_dependency_hash = models.CharField(max_length=64, db_index=True)
    dependency_hash = models.CharField(max_length=64, db_index=True)
    discovery_category = models.CharField(max_length=20, choices=CATEGORIES, db_index=True)
    discovery_rank = models.PositiveSmallIntegerField()
    relationship_tier = models.CharField(max_length=50)
    relationship_tier_rank = models.PositiveSmallIntegerField()
    contributing_target_assessment_ids = models.JSONField(default=list)
    conflicting_target_assessment_ids = models.JSONField(default=list)
    admission_reasons = models.JSONField(default=list)
    band = models.CharField(max_length=1, choices=BANDS)
    band_rank = models.PositiveSmallIntegerField()
    required_important_coverage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    preferred_important_coverage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    matched_important_category_count = models.PositiveSmallIntegerField(default=0)
    found_important_category_count = models.PositiveSmallIntegerField(default=0)
    positive_important_count = models.PositiveIntegerField(default=0)
    supporting_found_count = models.PositiveIntegerField(default=0)
    strongest_important_match = models.CharField(max_length=20, blank=True)
    major_gap_count = models.PositiveIntegerField(default=0)
    gate_gap_count = models.PositiveIntegerField(default=0)
    foundation_gap_count = models.PositiveIntegerField(default=0)
    professional_score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    criterion_results = models.JSONField(default=list)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['generation', 'opportunity'],
            name='unique_v2_generation_opportunity')]
        indexes = [
            models.Index(
                fields=['generation', 'discovery_rank', 'relationship_tier_rank', 'band_rank'],
                name='v2_feed_generation_rank_idx'),
            models.Index(
                fields=['opportunity', 'clean_revision'],
                name='v2_feed_revision_idx'),
        ]


class EvaluationRecomputeTask(models.Model):
    STATES = [('PENDING', 'Pending'), ('RUNNING', 'Running'),
              ('COMPLETE', 'Complete'), ('FAILED', 'Failed'),
              ('SUPERSEDED', 'Superseded')]
    profile = models.ForeignKey(
        Profile, null=True, blank=True, on_delete=models.CASCADE, related_name='recompute_tasks')
    opportunity = models.ForeignKey(
        Opportunity, null=True, blank=True, on_delete=models.CASCADE, related_name='recompute_tasks')
    reason = models.CharField(max_length=80)
    task_version = models.CharField(max_length=30, default='legacy-v1', db_index=True)
    task_key = models.CharField(max_length=180, null=True, blank=True, db_index=True)
    dependency_hash = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=12, choices=STATES, default='PENDING', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    error_message = models.TextField(blank=True)
    claim_token = models.CharField(max_length=64, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    supersession_reason = models.TextField(blank=True)
    diagnostics = models.JSONField(default=dict)
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(
            check=(
                (models.Q(profile__isnull=False) & models.Q(opportunity__isnull=True))
                | (models.Q(profile__isnull=True) & models.Q(opportunity__isnull=False))
            ),
            name='evaluation_task_one_parent'),
            # ``task_key`` identifies a logical dependency, not one execution of
            # it.  Exactly one *live* task may exist per key so a duplicate
            # enqueue cannot create concurrent work, while terminal FAILED and
            # SUPERSEDED rows stay as audit history and a later enqueue of the
            # same dependency can legitimately start a fresh task.
            models.UniqueConstraint(
                fields=['task_key'],
                condition=models.Q(state__in=('PENDING', 'RUNNING', 'COMPLETE')),
                name='evaluation_task_one_live_per_key'),
        ]
        indexes = [
            models.Index(fields=['state', 'available_at'], name='eval_queue_ready_idx'),
            models.Index(
                fields=['task_version', 'state', 'available_at'],
                name='eval_v2_queue_ready_idx'),
        ]


class CareerMarketSnapshot(models.Model):
    occupation = models.ForeignKey(TaxonomyConcept, on_delete=models.PROTECT, related_name='market_snapshots')
    geographic_scope = models.CharField(max_length=100, default='all_observed')
    observation_start = models.DateField()
    observation_end = models.DateField()
    generated_at = models.DateTimeField(auto_now_add=True)
    aggregation_version = models.CharField(max_length=40)
    assignment_version = models.CharField(max_length=40)
    taxonomy_versions = models.JSONField(default=list)
    extraction_versions = models.JSONField(default=list)
    opportunity_count = models.PositiveIntegerField(default=0)
    coverage = models.JSONField(default=dict)
    metrics = models.JSONField(default=dict)
    provenance = models.JSONField(default=dict)
    input_hash = models.CharField(max_length=64)
    is_stale = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['-generated_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['occupation', 'geographic_scope',
            'observation_start', 'observation_end', 'aggregation_version', 'input_hash'], name='unique_career_snapshot_input')]

    def __str__(self):
        return f'{self.occupation} / {self.observation_end} / {self.opportunity_count} observed'


class CareerMarketSnapshotOpportunity(models.Model):
    """Frozen aggregation inputs survive later canonical/extraction revisions."""
    snapshot = models.ForeignKey(CareerMarketSnapshot, on_delete=models.CASCADE, related_name='members')
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT, related_name='snapshot_memberships')
    evidence = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['snapshot', 'opportunity'], name='unique_snapshot_opportunity')]


class LegalAcceptanceEvent(models.Model):
    class Document(models.TextChoices):
        TERMS = 'terms', 'Terms of Service'
        PRIVACY = 'privacy', 'Privacy Notice'

    class Action(models.TextChoices):
        AGREED = 'agreed', 'Agreed'
        ACKNOWLEDGED = 'acknowledged', 'Acknowledged'

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='legal_acceptance_events')
    document_type = models.CharField(max_length=12, choices=Document.choices)
    document_version = models.CharField(max_length=40)
    content_sha256 = models.CharField(max_length=64)
    action = models.CharField(max_length=14, choices=Action.choices)
    context = models.CharField(max_length=40, default='registration')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.CheckConstraint(
                check=(models.Q(document_type='terms', action='agreed') |
                           models.Q(document_type='privacy', action='acknowledged')),
                name='legal_document_action_pair',
            ),
            models.UniqueConstraint(
                fields=['user', 'document_type', 'document_version', 'content_sha256', 'action', 'context'],
                name='unique_user_legal_document_event',
            ),
        ]
        indexes = [models.Index(fields=['user', '-created_at'], name='legal_user_created_idx')]


class PrivacyRequest(models.Model):
    class Category(models.TextChoices):
        ACCESS = 'access', 'Access to information'
        CORRECTION = 'correction', 'Correction'
        DELETION = 'deletion', 'Deletion or account closure'
        OBJECTION = 'objection', 'Objection or restriction'
        EXPORT = 'export', 'Data export'
        OTHER = 'other', 'Other privacy enquiry'

    class Status(models.TextChoices):
        RECEIVED = 'received', 'Received'
        REVIEWING = 'reviewing', 'Under review'
        COMPLETED = 'completed', 'Completed'
        CLOSED = 'closed', 'Closed'

    requester = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='privacy_requests')
    contact_email = models.EmailField()
    category = models.CharField(max_length=12, choices=Category.choices)
    description = models.TextField(max_length=4000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED)
    staff_note = models.TextField(blank=True)
    handled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['requester', '-created_at'], name='privacy_user_created_idx')]


class DataRightsEvent(models.Model):
    """Minimal accountability trail for self-service data rights actions.

    This trail deliberately stores no personal information: ``detail`` is
    limited to counts and category names so the record can survive account
    closure without becoming a new copy of the closed account.
    """

    class Action(models.TextChoices):
        EXPORT_CREATED = 'export_created', 'Data export created'
        CLOSURE_PASSWORD_FAILED = 'closure_password_failed', 'Closure password check failed'
        CLOSURE_BLOCKED = 'closure_blocked', 'Closure blocked'
        CLOSURE_CONFIRMED = 'closure_confirmed', 'Closure confirmed'
        CLOSURE_COMPLETED = 'closure_completed', 'Closure completed'

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='data_rights_events')
    action = models.CharField(max_length=40, choices=Action.choices)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['user', '-created_at'], name='datarights_user_created_idx'),
            models.Index(fields=['action', '-created_at'], name='datarights_action_created_idx'),
        ]

    def __str__(self):
        return f'{self.get_action_display()} ({self.created_at:%Y-%m-%d %H:%M})'

