from django.contrib import admin
from django.contrib import messages
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from core.services.moderation import moderate_story, moderate_opportunity, resolve_report
from .models import (StoryReport, OpportunityReport, StoryModerationEvent, OpportunityModerationEvent,
    Notification, NotificationPreference,
    CareerMarketSnapshot, CareerMarketSnapshotOpportunity, LegalAcceptanceEvent, PrivacyRequest)


@admin.register(LegalAcceptanceEvent)
class LegalAcceptanceEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'document_type', 'action', 'document_version', 'created_at')
    search_fields = ('user__email', 'document_version', 'content_sha256')
    list_filter = ('document_type', 'action', 'context')
    readonly_fields = ('user', 'document_type', 'document_version', 'content_sha256', 'action', 'context', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PrivacyRequest)
class PrivacyRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'requester', 'category', 'status', 'created_at', 'handled_by')
    list_filter = ('category', 'status', 'created_at')
    search_fields = ('requester__email', 'contact_email')
    readonly_fields = ('requester', 'contact_email', 'category', 'description', 'created_at', 'updated_at', 'handled_by', 'handled_at')
    fields = ('requester', 'contact_email', 'category', 'description', 'status', 'staff_note', 'handled_by', 'handled_at', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        if change and 'status' in form.changed_data:
            obj.handled_by = request.user
            obj.handled_at = timezone.now()
        super().save_model(request, obj, form, change)
from .models import (
    Achievement, CareerPreference, Certification, CVtip, Education, Interest,
    Language, MotivationalNudge, Opportunity, Profile, ProfileInterest,
    ProfileLink, ProfileSkill, Project, SavedOpportunity, Skill, Story,
    Volunteering, WorkExperience,
)


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(Interest)
class InterestAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

admin.site.register(Project)
admin.site.register(WorkExperience)
admin.site.register(Certification)
admin.site.register(Education)
admin.site.register(ProfileSkill)
admin.site.register(ProfileInterest)
admin.site.register(Achievement)
admin.site.register(Volunteering)
admin.site.register(Language)
admin.site.register(ProfileLink)
admin.site.register(CareerPreference)

@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'company', 'opportunity_kind', 'career_stage', 'scope_status',
        'listing_kind', 'location_text', 'closing_date', 'lifecycle_status', 'is_active',
        'moderation_status',
    )
    search_fields = ('title', 'company', 'location', 'location_text', 'reference_number')
    list_filter = (
        'opportunity_kind', 'career_stage', 'scope_status', 'opportunity_type',
        'listing_kind', 'lifecycle_status', 'is_active', 'province', 'country',
        'moderation_status',
    )
    filter_horizontal = ('required_skills', 'preferred_skills')
    readonly_fields = ('first_seen_at', 'last_seen_at', 'date_listed', 'canonical_fingerprint')

    def save_model(self, request, obj, form, change):
        status = obj.moderation_status
        obj.moderation_status = Opportunity.objects.get(pk=obj.pk).moderation_status if change else 'clear'
        super().save_model(request, obj, form, change)
        moderate_opportunity(opportunity_id=obj.pk, actor=request.user, status=status, note='Changed in Opportunity admin.')

    @admin.action(description='Suppress selected opportunities (retain history)')
    def suppress(self, request, queryset):
        for obj in queryset:
            moderate_opportunity(opportunity_id=obj.pk, actor=request.user, status='suppressed', note='Suppressed in Opportunity admin.')

    @admin.action(description='Restore selected opportunities to clear moderation state')
    def restore(self, request, queryset):
        for obj in queryset:
            moderate_opportunity(opportunity_id=obj.pk, actor=request.user, status='clear', note='Restored in Opportunity admin; source lifecycle still applies.')

    @admin.action(description='Mark selected opportunities under review (remain discoverable)')
    def under_review(self, request, queryset):
        for obj in queryset:
            moderate_opportunity(opportunity_id=obj.pk, actor=request.user, status='under_review', note='Review opened in Opportunity admin.')

    actions = ['suppress', 'restore', 'under_review']

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'qualification', 'institution', 'location', 'phone', 'profile_complete')
    search_fields = ('user__username', 'qualification', 'institution')
    list_filter = ('profile_complete',)

@admin.register(SavedOpportunity)
class SavedOpportunityAdmin(admin.ModelAdmin):
    list_display = ('user', 'opportunity', 'saved_at')
    search_fields = ('user__username', 'opportunity__title')

@admin.register(CVtip)
class CVtipAdmin(admin.ModelAdmin):
    list_display = ('title', 'opportunity_type', 'is_active')
    search_fields = ('title', 'content')
    list_filter = ('opportunity_type', 'is_active')

@admin.register(MotivationalNudge)
class MotivationalNudgeAdmin(admin.ModelAdmin):
    list_display = ('title', 'trigger', 'trigger_value', 'is_active')
    search_fields = ('title', 'message')
    list_filter = ('trigger', 'is_active')

@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'title', 'status', 'submitted_at')
    search_fields = ('display_name', 'title', 'content')
    list_filter = ('status',) 
    readonly_fields = ('submitted_at', 'approved_at')

    def save_model(self, request, obj, form, change):
        status, note = obj.status, obj.moderation_note
        obj.status = Story.objects.get(pk=obj.pk).status if change else 'pending'
        super().save_model(request, obj, form, change)
        moderate_story(story_id=obj.pk, actor=request.user, status=status, note=note)

    @admin.action(description='Restore selected Stories to approved (publish again)')
    def restore(self, request, queryset):
        for story in queryset:
            moderate_story(story_id=story.pk, actor=request.user, status='approved', note='Restored by moderator.')

    actions = ['restore']


class ReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'reported_item', 'reason', 'status', 'reporter', 'created_at', 'reviewer', 'resolution')
    list_filter = ('status', 'reason', ('created_at', admin.DateFieldListFilter), 'reporter')
    search_fields = ('details', 'reporter__username')
    readonly_fields = ('reporter', 'reason', 'details', 'status', 'created_at', 'reviewer', 'reviewed_at', 'resolution')
    actions = ['dismiss_reports', 'remove_items', 'resolve_after_action']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def reported_item(self, obj):
        return obj.story if isinstance(obj, StoryReport) else obj.opportunity

    def _resolve(self, request, queryset, action):
        count = 0
        for report in queryset.filter(status='open'):
            try:
                resolve_report(report=report, actor=request.user, action=action, note=report.moderator_note)
            except ValidationError as exc:
                self.message_user(request, f'Report {report.pk}: {"; ".join(exc.messages)}', level=messages.ERROR)
            else:
                count += 1
        self.message_user(request, f'{count} reports resolved; reviewer and time recorded.')

    @admin.action(description='Dismiss selected open reports')
    def dismiss_reports(self, request, queryset):
        self._resolve(request, queryset, 'dismiss')

    @admin.action(description='Remove / suppress reported items and resolve selected reports (no deletion)')
    def remove_items(self, request, queryset):
        self._resolve(request, queryset, 'remove')

    @admin.action(description='Resolve after action recorded in each moderator note')
    def resolve_after_action(self, request, queryset):
        self._resolve(request, queryset, 'resolve')


@admin.register(StoryReport)
class StoryReportAdmin(ReportAdmin):
    list_filter = ReportAdmin.list_filter + ('story',)
    readonly_fields = ReportAdmin.readonly_fields + ('story',)
    list_select_related = ('story', 'reporter', 'reviewer')


@admin.register(OpportunityReport)
class OpportunityReportAdmin(ReportAdmin):
    list_filter = ReportAdmin.list_filter + ('opportunity',)
    readonly_fields = ReportAdmin.readonly_fields + ('opportunity', 'source_provenance')
    list_select_related = ('opportunity', 'reporter', 'reviewer')

    def source_provenance(self, obj):
        rows = obj.opportunity.source_records.select_related('source', 'current_raw')
        return format_html_join(' ', '<p>{}: {} <a href="{}">Source record</a> <a href="{}">Raw evidence</a></p>', (
            (row.source.name, row.source_external_id,
             reverse('admin:ingestion_opportunitysourcerecord_change', args=[row.pk]),
             reverse('admin:ingestion_rawopportunity_change', args=[row.current_raw_id]) if row.current_raw_id else '#')
            for row in rows))


class InspectOnlyAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StoryModerationEvent)
class StoryEventAdmin(InspectOnlyAdmin):
    list_display = ('story', 'actor', 'from_status', 'to_status', 'created_at')
    list_filter = ('to_status', 'created_at')


@admin.register(OpportunityModerationEvent)
class OpportunityEventAdmin(InspectOnlyAdmin):
    list_display = ('opportunity', 'actor', 'from_status', 'to_status', 'created_at')
    list_filter = ('to_status', 'created_at')


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'stories', 'opportunities', 'deadlines', 'applications', 'interviews', 'career_intelligence')
    readonly_fields = ('updated_at',)
    raw_id_fields = ('user',)


@admin.register(Notification)
class NotificationAdmin(InspectOnlyAdmin):
    list_display = ('user', 'title', 'category', 'is_read', 'created_at')
    list_filter = ('category', 'is_read')


@admin.register(CareerMarketSnapshot)
class MarketSnapshotAdmin(InspectOnlyAdmin):
    list_display = ('occupation', 'geographic_scope', 'observation_end', 'opportunity_count', 'is_stale', 'generated_at')
    list_filter = ('is_stale', 'geographic_scope', 'aggregation_version', 'observation_end')
    list_select_related = ('occupation__release',)
    search_fields = ('occupation__preferred_label',)


@admin.register(CareerMarketSnapshotOpportunity)
class SnapshotMemberAdmin(InspectOnlyAdmin):
    list_display = ('id', 'snapshot_id', 'opportunity_id')
    list_filter = ('snapshot',)
