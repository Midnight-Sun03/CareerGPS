from django.contrib import admin

from .models import (
    GreenhouseBoard, IngestionRun, IngestionSource, OpportunitySourceRecord,
    RawOpportunity, OpportunityExtraction, OpportunityCriterion,
    OpportunityCriterionTaxonomyMatch, SmartRecruitersCompany, WorkdayBoard,
)


@admin.register(IngestionSource)
class IngestionSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "source_type", "active", "priority", "last_checked_at", "last_success_at", "health")
    list_filter = ("source_type", "active")
    search_fields = ("name", "code", "company", "base_url")
    readonly_fields = ("last_checked_at", "last_success_at", "last_error", "created_at", "updated_at")

    @admin.display(description="Health")
    def health(self, obj):
        return "Error" if obj.last_error else "Healthy" if obj.last_success_at else "Not run"


@admin.register(GreenhouseBoard)
class GreenhouseBoardAdmin(admin.ModelAdmin):
    list_display = ("company_name", "board_token", "active", "last_checked_at", "last_success_at", "health")
    list_filter = ("active",)
    search_fields = ("company_name", "board_token", "careers_url")
    readonly_fields = ("last_checked_at", "last_success_at", "last_error", "created_at", "updated_at")

    @admin.display(description="Health")
    def health(self, obj):
        return "Error" if obj.last_error else "Healthy" if obj.last_success_at else "Not run"


@admin.register(SmartRecruitersCompany)
class SmartRecruitersCompanyAdmin(admin.ModelAdmin):
    list_display = ("company_name", "company_identifier", "active", "last_checked_at", "last_success_at", "health")
    list_filter = ("active",)
    search_fields = ("company_name", "company_identifier", "careers_url")
    readonly_fields = ("last_checked_at", "last_success_at", "last_error", "created_at", "updated_at")

    @admin.display(description="Health")
    def health(self, obj):
        return "Error" if obj.last_error else "Healthy" if obj.last_success_at else "Not run"


@admin.register(WorkdayBoard)
class WorkdayBoardAdmin(admin.ModelAdmin):
    list_display = ("company_name", "tenant", "site", "active", "last_checked_at", "last_success_at", "health")
    list_filter = ("active",)
    search_fields = ("company_name", "tenant", "site", "careers_url")
    readonly_fields = ("last_checked_at", "last_success_at", "last_error", "created_at", "updated_at")

    @admin.display(description="Health")
    def health(self, obj):
        return "Error" if obj.last_error else "Healthy" if obj.last_success_at else "Not run"


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = (
        "source", "started_at", "finished_at", "status", "records_found",
        "opportunities_created", "opportunities_updated", "duplicates_found",
        "unclassified_count", "out_of_scope_count", "errors_count",
    )
    list_filter = ("status", "source")
    search_fields = ("source__name", "source__code", "error_summary")
    date_hierarchy = "started_at"
    readonly_fields = tuple(field.name for field in IngestionRun._meta.fields)

    def has_add_permission(self, request):
        return False


@admin.register(RawOpportunity)
class RawOpportunityAdmin(admin.ModelAdmin):
    list_display = (
        "source", "source_external_id", "opportunity_kind", "career_stage",
        "scope_status", "normalization_status", "classified_type", "fetched_at", "parser_version",
    )
    list_filter = (
        "source", "opportunity_kind", "career_stage", "scope_status",
        "normalization_status", "classified_type", "parser_version",
    )
    search_fields = ("source_external_id", "listing_url", "source_url", "apply_url", "content_hash")
    date_hierarchy = "fetched_at"
    readonly_fields = tuple(field.name for field in RawOpportunity._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OpportunitySourceRecord)
class OpportunitySourceRecordAdmin(admin.ModelAdmin):
    list_display = ("opportunity", "source", "source_external_id", "reference_number", "active_at_source", "first_seen_at", "last_seen_at", "missing_since")
    list_filter = ("source", "active_at_source", "missing_since")
    search_fields = (
        "opportunity__title", "opportunity__company", "source_external_id",
        "reference_number", "listing_url", "source_url", "apply_url",
    )
    readonly_fields = ("first_seen_at", "last_seen_at")
    autocomplete_fields = ("opportunity", "current_raw")


class OpportunityCriterionInline(admin.TabularInline):
    model = OpportunityCriterion
    extra = 0
    readonly_fields = (
        "criterion_type", "requirement_level", "raw_value", "normalized_value",
        "evidence_text", "source_section", "minimum_years", "maximum_years", "metadata",
    )
    can_delete = False


@admin.register(OpportunityExtraction)
class OpportunityExtractionAdmin(admin.ModelAdmin):
    list_display = (
        "opportunity", "status", "source_completeness", "model_name",
        "schema_version", "processed_at",
    )
    list_filter = ("status", "source_completeness", "model_name", "schema_version")
    search_fields = ("opportunity__title", "opportunity__company", "input_hash")
    readonly_fields = tuple(field.name for field in OpportunityExtraction._meta.fields)
    inlines = (OpportunityCriterionInline,)

    def has_add_permission(self, request):
        return False


@admin.register(OpportunityCriterionTaxonomyMatch)
class OpportunityCriterionTaxonomyMatchAdmin(admin.ModelAdmin):
    list_display = ("criterion", "concept", "match_type")
    list_filter = ("match_type", "concept__release__source")
    search_fields = ("criterion__raw_value", "concept__preferred_label", "concept__code")
    readonly_fields = tuple(field.name for field in OpportunityCriterionTaxonomyMatch._meta.fields)

    def has_add_permission(self, request):
        return False
