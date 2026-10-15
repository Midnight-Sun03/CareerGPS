"""Employer ownership stops at the candidate Application relationship."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.models import Opportunity
from ingestion.models import OpportunityCriterion


class Organization(models.Model):
    VERIFICATION = [(key, label) for key, label in [('draft', 'Draft'), ('submitted', 'Submitted for verification'), ('under_review', 'Under review'), ('more_info_required', 'More information required'), ('approved', 'Approved'), ('rejected', 'Rejected')]]
    ACCOUNT = [('active', 'Active'), ('suspended', 'Suspended')]
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    website = models.URLField(blank=True)
    description = models.TextField(blank=True)
    industry = models.CharField(max_length=200, blank=True)
    location = models.CharField(max_length=100, blank=True)
    verification_status = models.CharField(max_length=20, choices=VERIFICATION, default='draft')
    account_status = models.CharField(max_length=20, choices=ACCOUNT, default='active')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_organizations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    primary_domain = models.CharField(max_length=253, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [('review_employer', 'Review employer verification and security')]

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    ROLES = [('owner', 'Owner'), ('recruiter', 'Recruiter'), ('hiring_manager', 'Hiring manager'),
        ('interviewer', 'Interviewer'), ('viewer', 'Viewer')]
    STATUS = [('invited', 'Invited'), ('active', 'Active'), ('disabled', 'Disabled')]
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='employer_memberships')
    role = models.CharField(max_length=20, choices=ROLES)
    status = models.CharField(max_length=20, choices=STATUS, default='invited')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'user'], name='unique_organization_member')]

    def __str__(self):
        return f'{self.user_id}: {self.organization} ({self.role})'


class EmployerVacancy(models.Model):
    STATES = [('draft', 'Draft'), ('published', 'Published'), ('closed', 'Closed')]
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='vacancies')
    opportunity = models.OneToOneField(Opportunity, on_delete=models.PROTECT, null=True, blank=True, related_name='employer_vacancy')
    title = models.CharField(max_length=200)
    reference_number = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=100)
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default='South Africa')
    work_arrangement = models.CharField(max_length=50, blank=True,
        choices=[('', 'Not specified'), ('onsite', 'On-site'), ('hybrid', 'Hybrid'), ('remote', 'Remote')])
    employment_type = models.CharField(max_length=100, blank=True)
    opportunity_kind = models.CharField(max_length=30, choices=Opportunity.OPPORTUNITY_KIND_CHOICES, default='job')
    career_stage = models.CharField(max_length=20, choices=Opportunity.CAREER_STAGE_CHOICES, default='unknown')
    description = models.TextField()
    responsibilities = models.TextField(blank=True)
    closing_date = models.DateField(null=True, blank=True)
    salary_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_currency = models.CharField(max_length=3, blank=True)
    salary_period = models.CharField(max_length=10, blank=True, choices=Opportunity._meta.get_field('salary_period').choices)
    application_instructions = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATES, default='draft')
    no_candidate_fees = models.BooleanField(default=False, help_text='I confirm candidates are not required to pay an application or recruitment fee through CareerGPS.')
    external_destination = models.URLField(max_length=1000, blank=True, help_text='Optional external information/contact destination. CareerGPS direct applications remain on CareerGPS.')
    destination_domain = models.CharField(max_length=253, blank=True)
    trust_hold = models.BooleanField(default=False, help_text='Platform review must release this vacancy before publication.')
    # Immutable-by-service publication copy. Editing source rows never changes
    # matching until the canonical record and this projection commit together.
    published_requirements = models.JSONField(default=list, blank=True)
    published_input_hash = models.CharField(max_length=64, blank=True)
    publication_revision = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='authored_vacancies')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']

    def clean(self):
        from .domains import website_domain
        website_domain(self.external_destination)
        if self.salary_min is not None and self.salary_min < 0 or self.salary_max is not None and self.salary_max < 0:
            raise ValidationError('Compensation cannot be negative.')
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValidationError('Maximum compensation must be at least the minimum.')

    def __str__(self):
        return self.title


class VacancyRequirement(models.Model):
    TYPES = [(key, label) for key, label in OpportunityCriterion.TYPE_CHOICES
        if key in {'skill', 'qualification', 'experience', 'certification', 'language', 'work_eligibility'}]
    vacancy = models.ForeignKey(EmployerVacancy, on_delete=models.CASCADE, related_name='requirements')
    criterion_type = models.CharField(max_length=30, choices=TYPES)
    requirement_level = models.CharField(max_length=20, choices=[('required', 'Required'), ('preferred', 'Preferred')])
    raw_value = models.TextField(help_text='Keep alternatives in one requirement, e.g. Python or R.')
    minimum_years = models.PositiveSmallIntegerField(null=True, blank=True)
    maximum_years = models.PositiveSmallIntegerField(null=True, blank=True)
    minimum_nqf = models.PositiveSmallIntegerField(null=True, blank=True)
    context = models.CharField(max_length=200, blank=True, help_text='Experience context, e.g. overall or software development.')
    source_excerpt = models.TextField(blank=True)
    origin = models.CharField(max_length=30, default='employer_authored', choices=[
        ('employer_authored', 'Employer authored'),
        ('interpreted_confirmed', 'CareerGPS interpretation confirmed'),
        ('employer_corrected', 'Employer corrected interpretation')])

    class Meta:
        ordering = ['pk']

    def clean(self):
        if not self.raw_value.strip():
            raise ValidationError('Enter a requirement.')
        if self.minimum_years is not None and self.maximum_years is not None and self.minimum_years > self.maximum_years:
            raise ValidationError('Experience maximum cannot be below the minimum.')
        if self.criterion_type != 'experience' and (self.minimum_years is not None or self.maximum_years is not None):
            raise ValidationError('Experience bounds belong only on experience requirements.')
        if self.minimum_nqf is not None and (self.criterion_type != 'qualification' or not 1 <= self.minimum_nqf <= 10):
            raise ValidationError('NQF must be 1 to 10 and belongs only on a qualification.')


class VacancyIntake(models.Model):
    """Private, untrusted source and proposed interpretation until employer confirmation."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='vacancy_intakes')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source_text = models.TextField()
    source_filename = models.CharField(max_length=200, blank=True)
    source_sha256 = models.CharField(max_length=64)
    extraction_method = models.CharField(max_length=30)
    extraction_notice = models.CharField(max_length=500, blank=True)
    proposed_facts = models.JSONField(default=dict)
    proposed_requirements = models.JSONField(default=list)
    vacancy = models.OneToOneField(EmployerVacancy, null=True, blank=True, on_delete=models.PROTECT, related_name='intake')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class HiringApplication(models.Model):
    STAGES = [(key, key.replace('_', ' ').title()) for key in
        ('received', 'screening', 'shortlisted', 'assessment', 'interview', 'final_review', 'offer', 'hired', 'rejected')]
    application = models.OneToOneField('applications.Application', on_delete=models.PROTECT, related_name='hiring_application')
    vacancy = models.ForeignKey(EmployerVacancy, on_delete=models.PROTECT, related_name='hiring_applications')
    stage = models.CharField(max_length=20, choices=STAGES, default='received')
    submitted_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.application_id and self.vacancy_id and self.application.opportunity_id != self.vacancy.opportunity_id:
            raise ValidationError('The application must belong to this vacancy.')


class ApplicationStageEvent(models.Model):
    hiring_application = models.ForeignKey(HiringApplication, on_delete=models.PROTECT, related_name='stage_events')
    from_stage = models.CharField(max_length=20, blank=True)
    to_stage = models.CharField(max_length=20, choices=HiringApplication.STAGES)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    candidate_visible = models.BooleanField(default=True)
    reason_category = models.CharField(max_length=40, blank=True,
        choices=[('', 'Not specified'), ('progression', 'Progression'), ('position_filled', 'Position filled'),
            ('requirements', 'Requirements reviewed'), ('other', 'Other')])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']


class EmployerApplicationNote(models.Model):
    hiring_application = models.ForeignKey(HiringApplication, on_delete=models.PROTECT, related_name='private_notes')
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    content = models.TextField(max_length=10000)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.hiring_application_id and self.organization_id != self.hiring_application.vacancy.organization_id:
            raise ValidationError('Notes must belong to the application organization.')


class SubmittedDocument(models.Model):
    """Frozen bounded file bytes; never exposed through a public MEDIA_URL."""
    hiring_application = models.ForeignKey(HiringApplication, on_delete=models.PROTECT, related_name='submitted_documents')
    original_document = models.ForeignKey('applications.GeneratedDocument', null=True, on_delete=models.SET_NULL)
    document_type = models.CharField(max_length=30, choices=[('cv', 'CV'), ('cover_letter', 'Cover letter')])
    pdf_bytes = models.BinaryField(blank=True)
    docx_bytes = models.BinaryField(blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    docx_sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['hiring_application', 'document_type'], name='unique_submitted_document_type')]


class EmployerAuditEvent(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='audit_events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    event_type = models.CharField(max_length=60)
    object_type = models.CharField(max_length=60)
    object_id = models.PositiveBigIntegerField()
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def display_label(self):
        return self.event_type.replace('_', ' ').capitalize()

    class Meta:
        ordering = ['-created_at', '-pk']



class CandidateVisibilityPreference(models.Model):
    """Candidate-owned controls. Absence of a row always means private."""
    profile = models.OneToOneField('core.Profile', on_delete=models.CASCADE, related_name='employer_visibility')
    discoverable_by_employers = models.BooleanField(default=False, db_index=True)
    allow_verified_employers_only = models.BooleanField(default=True)
    allow_contact_reveal = models.BooleanField(default=False)
    allow_talent_pool_saving = models.BooleanField(default=False)
    allow_prior_applicant_rediscovery = models.BooleanField(default=False, db_index=True)
    discovery_location = models.CharField(max_length=100, blank=True, help_text='City or region to share. Leave blank to hide your location.')
    updated_at = models.DateTimeField(auto_now=True)


class CandidateEmployerBlock(models.Model):
    profile = models.ForeignKey('core.Profile', on_delete=models.CASCADE, related_name='employer_blocks')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='candidate_blocks')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['profile', 'organization'], name='unique_candidate_employer_block')]


class TalentInvitation(models.Model):
    """A single, historical invitation for one candidate and published vacancy."""
    STATES = [('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')]
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='talent_invitations')
    vacancy = models.ForeignKey(EmployerVacancy, on_delete=models.PROTECT, related_name='talent_invitations')
    candidate = models.ForeignKey('core.Profile', null=True, on_delete=models.SET_NULL, related_name='talent_invitations')
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=STATES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['vacancy', 'candidate'], name='unique_talent_invitation_vacancy_candidate')]
        ordering = ['-created_at', '-pk']

    def clean(self):
        if self.vacancy_id and self.organization_id != self.vacancy.organization_id:
            raise ValidationError('The invitation and vacancy must belong to one organization.')


class DiscoveryExperienceIndex(models.Model):
    """Derived numeric search index, not a copy of the candidate profile."""
    profile = models.OneToOneField('core.Profile', on_delete=models.CASCADE, related_name='discovery_experience')
    months = models.PositiveIntegerField(null=True, db_index=True)
    calculated_on = models.DateField(db_index=True)


class SavedCandidate(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='saved_candidates')
    candidate = models.ForeignKey('core.Profile', null=True, on_delete=models.SET_NULL, related_name='employer_saves')
    saved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    related_vacancy = models.ForeignKey(EmployerVacancy, null=True, blank=True, on_delete=models.PROTECT)
    source = models.CharField(max_length=30, default='search')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization', 'candidate'], name='unique_saved_org_candidate')]


class TalentPool(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='talent_pools')
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, max_length=4000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class TalentPoolMember(models.Model):
    SOURCES = [(key, key.replace('_', ' ').capitalize()) for key in
        ['search', 'saved_candidate', 'prior_applicant', 'vacancy_rediscovery', 'applicant_view']]
    pool = models.ForeignKey(TalentPool, on_delete=models.PROTECT, related_name='members')
    candidate = models.ForeignKey('core.Profile', null=True, on_delete=models.SET_NULL, related_name='talent_memberships')
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source = models.CharField(max_length=30, choices=SOURCES)
    source_vacancy = models.ForeignKey(EmployerVacancy, null=True, blank=True, on_delete=models.PROTECT)
    source_application = models.ForeignKey(HiringApplication, null=True, blank=True, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['pool', 'candidate'], name='unique_pool_candidate')]


class TalentPoolMemberEvent(models.Model):
    member = models.ForeignKey(TalentPoolMember, on_delete=models.PROTECT, related_name='history')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=30, choices=[('added', 'Added'), ('removed', 'Removed'), ('unavailable', 'Unavailable')])
    source = models.CharField(max_length=30, blank=True)
    source_vacancy = models.ForeignKey(EmployerVacancy, null=True, blank=True, on_delete=models.PROTECT)
    source_application = models.ForeignKey(HiringApplication, null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']


class HiringAssignment(models.Model):
    """Current responsibility; changes are retained in HiringAssignmentEvent."""
    ROLES = [('primary_recruiter', 'Primary recruiter'), ('hiring_manager', 'Hiring manager'),
        ('collaborator', 'Collaborator'), ('application', 'Application assignee')]
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    vacancy = models.ForeignKey(EmployerVacancy, on_delete=models.PROTECT, related_name='assignments')
    hiring_application = models.ForeignKey(HiringApplication, null=True, blank=True, on_delete=models.PROTECT, related_name='assignments')
    responsibility = models.CharField(max_length=30, choices=ROLES)
    assigned_member = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name='hiring_assignments')
    assigned_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name='assignments_made')
    assigned_at = models.DateTimeField(auto_now=True)
    active = models.BooleanField(default=True)

    def clean(self):
        if self.vacancy.organization_id != self.organization_id or self.assigned_member.organization_id != self.organization_id or self.assigned_by.organization_id != self.organization_id:
            raise ValidationError('Assignments must stay within one organization.')
        if self.hiring_application_id and self.hiring_application.vacancy_id != self.vacancy_id:
            raise ValidationError('The application must belong to the vacancy.')
        if bool(self.hiring_application_id) != (self.responsibility == 'application'):
            raise ValidationError('Choose an appropriate assignment responsibility.')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['vacancy', 'responsibility'], condition=models.Q(active=True, responsibility__in=['primary_recruiter', 'hiring_manager']), name='one_active_vacancy_responsibility'),
            models.UniqueConstraint(fields=['hiring_application'], condition=models.Q(active=True, responsibility='application'), name='one_active_application_assignee'),
            models.UniqueConstraint(fields=['vacancy', 'assigned_member'], condition=models.Q(active=True, responsibility='collaborator'), name='unique_active_vacancy_collaborator')]


class HiringAssignmentEvent(models.Model):
    assignment = models.ForeignKey(HiringAssignment, on_delete=models.PROTECT, related_name='history')
    assigned_member = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name='+')
    actor = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=20, choices=[('assigned', 'Assigned'), ('removed', 'Removed')])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']


class HiringTask(models.Model):
    STATUS = [('open', 'Open'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='hiring_tasks')
    vacancy = models.ForeignKey(EmployerVacancy, null=True, blank=True, on_delete=models.PROTECT, related_name='hiring_tasks')
    hiring_application = models.ForeignKey(HiringApplication, null=True, blank=True, on_delete=models.PROTECT, related_name='hiring_tasks')
    title = models.CharField(max_length=200)
    description = models.TextField(max_length=4000, blank=True)
    assigned_to = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name='hiring_tasks')
    created_by = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name='created_hiring_tasks')
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='open', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        if self.assigned_to.organization_id != self.organization_id or self.created_by.organization_id != self.organization_id:
            raise ValidationError('Task members must belong to this organization.')
        if self.vacancy_id and self.vacancy.organization_id != self.organization_id:
            raise ValidationError('Task vacancy must belong to this organization.')
        if self.hiring_application_id and (self.hiring_application.vacancy.organization_id != self.organization_id or self.hiring_application.vacancy_id != self.vacancy_id):
            raise ValidationError('Task application must belong to its vacancy and organization.')

    class Meta:
        ordering = ['status', 'due_date', '-pk']


class HiringTaskEvent(models.Model):
    task = models.ForeignKey(HiringTask, on_delete=models.PROTECT, related_name='history')
    actor = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name='+')
    assigned_to = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']


class OrganizationVerification(models.Model):
    TYPES = [('sa_company','South African registered company'), ('public_body','Government / public body'),
        ('education','University / education institution'), ('nonprofit','Nonprofit / NGO'),
        ('foreign','Foreign organization'), ('other','Other legitimate organization')]
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='verifications')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    submitted_at = models.DateTimeField(auto_now_add=True)
    legal_name = models.CharField(max_length=200)
    trading_name = models.CharField(max_length=200, blank=True)
    registration_number = models.CharField(max_length=100, blank=True)
    organization_type = models.CharField(max_length=30, choices=TYPES)
    country = models.CharField(max_length=100)
    website = models.URLField(max_length=1000, blank=True)
    website_domain = models.CharField(max_length=253, blank=True)
    business_email = models.EmailField(blank=True)
    email_domain = models.CharField(max_length=253, blank=True)
    registry_source = models.CharField(max_length=300, blank=True)
    registry_reference = models.CharField(max_length=300, blank=True)
    evidence_notes = models.TextField(max_length=5000, blank=True)
    status = models.CharField(max_length=20, choices=Organization.VERIFICATION, default='submitted')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    review_started_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    registry_checked_at = models.DateTimeField(null=True, blank=True)
    registry_checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    checked_source = models.CharField(max_length=300, blank=True)
    checked_reference = models.CharField(max_length=300, blank=True)
    independent_contact_source = models.CharField(max_length=300, blank=True)
    independent_contact_reference = models.CharField(max_length=300, blank=True)
    independent_contact_method = models.CharField(max_length=20, blank=True,
        choices=[('', 'Not recorded'), ('phone', 'Phone'), ('email', 'Email'), ('other', 'Other')])
    independent_contact_value = models.CharField(max_length=300, blank=True)
    independent_contact_confirmed_at = models.DateTimeField(null=True, blank=True)
    independent_contact_confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='+')
    employer_message = models.TextField(max_length=4000, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at','-pk']


class VerificationReviewEvent(models.Model):
    verification = models.ForeignKey(OrganizationVerification, on_delete=models.PROTECT, related_name='review_events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=40)
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    internal_note = models.TextField(max_length=4000, blank=True)
    employer_message = models.TextField(max_length=4000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at','pk']


class VerificationDocument(models.Model):
    verification = models.ForeignKey(OrganizationVerification, on_delete=models.PROTECT, related_name='documents')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=200)
    content = models.BinaryField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class OrganizationRiskFlag(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='risk_flags')
    code = models.CharField(max_length=60)
    scope = models.CharField(max_length=100, default='organization')
    severity = models.CharField(max_length=20, choices=[('informational','Informational'),('review','Review'),('high_risk','High risk')])
    evidence = models.JSONField(default=dict)
    detected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    resolution_note = models.CharField(max_length=1000, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['organization','code','scope'], condition=models.Q(resolved_at__isnull=True), name='one_open_org_security_flag')]
        ordering = ['resolved_at','-detected_at']


class MemberDomainSignal(models.Model):
    membership = models.OneToOneField(OrganizationMembership, on_delete=models.PROTECT, related_name='domain_signal')
    email_domain = models.CharField(max_length=253, blank=True)
    matches_organization = models.BooleanField(default=False)
    free_email = models.BooleanField(default=False)
    checked_at = models.DateTimeField(auto_now=True)


class EmployerAbuseCounter(models.Model):
    key = models.CharField(max_length=180, unique=True)
    window_started_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
