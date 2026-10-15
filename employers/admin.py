from django.contrib import admin
from .models import (Organization, OrganizationMembership, EmployerVacancy, VacancyRequirement,
    HiringApplication, ApplicationStageEvent, EmployerApplicationNote, SubmittedDocument, EmployerAuditEvent)
from .services import audit


class InspectOnlyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Organization)
class OrganizationAdmin(InspectOnlyAdmin):
    list_display = ['name','verification_status','account_status','review_link','created_at']
    list_filter = ['verification_status','account_status']
    search_fields = ['name','slug']

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('employers.review_employer')

    def has_change_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('Use explicit platform review actions.')

    @admin.display(description='Review')
    def review_link(self,obj):
        from django.urls import reverse
        from django.utils.html import format_html
        return format_html('<a href="{}">Open verification review</a>',reverse('employers:verification_review',args=[obj.pk]))


@admin.register(OrganizationMembership)
class MembershipAdmin(InspectOnlyAdmin):
    list_display = ['organization', 'user', 'role', 'status']
    list_filter = ['role', 'status', 'organization']


@admin.register(EmployerVacancy)
class VacancyAdmin(InspectOnlyAdmin):
    list_display = ['title', 'organization', 'status', 'opportunity', 'publication_revision']
    list_filter = ['status', 'organization']


@admin.register(HiringApplication)
class HiringAdmin(InspectOnlyAdmin):
    list_display = ['id', 'vacancy', 'application', 'stage', 'submitted_at', 'withdrawn_at']
    list_filter = ['stage', 'vacancy__organization']


@admin.register(SubmittedDocument)
class SubmittedDocumentAdmin(InspectOnlyAdmin):
    # The audit interface identifies versions/hashes; bytes are served only by
    # explicitly authorized application download routes.
    exclude = ['pdf_bytes', 'docx_bytes']

    def get_readonly_fields(self, request, obj=None):
        return [name for name in super().get_readonly_fields(request, obj) if name not in self.exclude]


for model in (VacancyRequirement, ApplicationStageEvent, EmployerApplicationNote, EmployerAuditEvent):
    admin.site.register(model, InspectOnlyAdmin)


from .models import (CandidateVisibilityPreference, CandidateEmployerBlock, SavedCandidate,
    TalentPool, TalentPoolMember, TalentPoolMemberEvent, DiscoveryExperienceIndex)


@admin.register(CandidateVisibilityPreference)
class VisibilityAdmin(InspectOnlyAdmin):
    list_display = ['profile_id', 'discoverable_by_employers', 'allow_talent_pool_saving', 'allow_contact_reveal', 'allow_prior_applicant_rediscovery']
    list_filter = ['discoverable_by_employers', 'allow_contact_reveal', 'allow_talent_pool_saving']
    search_fields = ['profile__user__username']


@admin.register(CandidateEmployerBlock)
class BlockAdmin(InspectOnlyAdmin):
    list_display = ['profile_id', 'organization', 'created_at']
    list_filter = ['organization']


@admin.register(TalentPool)
class PoolAdmin(InspectOnlyAdmin):
    list_display = ['name', 'organization', 'active', 'created_at']
    list_filter = ['organization', 'active']
    search_fields = ['name']


@admin.register(SavedCandidate)
class SavedCandidateAdmin(InspectOnlyAdmin):
    list_display = ['candidate_id', 'organization', 'active', 'source', 'created_at']
    list_filter = ['organization', 'active', 'source']


for model in (TalentPoolMember, TalentPoolMemberEvent, DiscoveryExperienceIndex):
    admin.site.register(model, InspectOnlyAdmin)


class DiscoveryAuditAdmin(InspectOnlyAdmin):
    list_display = ['event_type', 'organization', 'actor', 'object_type', 'object_id', 'created_at']
    list_filter = ['event_type', 'organization']
    search_fields = ['event_type', 'actor__username']


admin.site.unregister(EmployerAuditEvent)
admin.site.register(EmployerAuditEvent, DiscoveryAuditAdmin)

from .models import HiringAssignment, HiringAssignmentEvent, HiringTask, HiringTaskEvent
for model in (HiringAssignment, HiringAssignmentEvent, HiringTask, HiringTaskEvent):
    admin.site.register(model, InspectOnlyAdmin)

from .models import (OrganizationVerification, VerificationReviewEvent, VerificationDocument,
    OrganizationRiskFlag, MemberDomainSignal, EmployerAbuseCounter)

class TrustInspectAdmin(InspectOnlyAdmin):
    def save_model(self,request,obj,form,change):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied('Use explicit platform review actions.')
    def has_view_permission(self,request,obj=None):
        return request.user.has_perm('employers.review_employer')
    def has_change_permission(self,request,obj=None):
        return False

@admin.register(OrganizationVerification)
class VerificationAdmin(TrustInspectAdmin):
    list_display=['organization','organization_type','legal_name','registration_number','website_domain','email_domain','status','submitted_at','review_link']
    list_filter=['status','organization_type']
    search_fields=['legal_name','registration_number','organization__name']
    @admin.display(description='Review')
    def review_link(self,obj):
        from django.urls import reverse
        from django.utils.html import format_html
        return format_html('<a href="{}">Review evidence</a>',reverse('employers:verification_review',args=[obj.organization_id]))

@admin.register(VerificationDocument)
class VerificationDocumentAdmin(TrustInspectAdmin):
    exclude=['content']
    def get_readonly_fields(self,request,obj=None):
        return [name for name in super().get_readonly_fields(request,obj) if name!='content']

for model in (VerificationReviewEvent,OrganizationRiskFlag,MemberDomainSignal,EmployerAbuseCounter):
    admin.site.register(model,TrustInspectAdmin)
