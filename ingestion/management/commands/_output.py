from django.core.management.base import CommandError


def add_explain_arguments(parser):
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print bounded per-record normalization/classification diagnostics during a dry run.",
    )
    parser.add_argument(
        "--explain-count",
        type=int,
        default=20,
        help="Maximum diagnostic records to print; this never limits source fetching.",
    )


def explain_options(options):
    if options.get("explain") and not options.get("dry_run"):
        raise CommandError("--explain is diagnostic-only and must be used with --dry-run.")
    if options.get("explain_count", 20) < 0:
        raise CommandError("--explain-count cannot be negative.")
    return {
        "explain": bool(options.get("explain")),
        "explain_count": options.get("explain_count", 20),
    }


def _display(value):
    if value in (None, ""):
        return "(not provided)"
    text = " ".join(str(value).split())
    return f"{text[:497]}..." if len(text) > 500 else text


def render_summary(command, source_code, counters, dry_run=False):
    prefix = "DRY RUN " if dry_run else ""
    command.stdout.write(command.style.SUCCESS(
        f"{prefix}{source_code}: found={counters['records_found']} "
        f"raw_created={counters['raw_records_created']} created={counters['opportunities_created']} "
        f"updated={counters['opportunities_updated']} duplicates={counters['duplicates_found']} "
        f"internship={counters.get('internship_count', 0)} "
        f"learnership={counters.get('learnership_count', 0)} "
        f"graduate={counters.get('graduate_count', 0)} "
        f"entry_level={counters.get('entry_level_count', 0)} "
        f"unclassified={counters.get('unclassified_count', 0)} "
        f"out_of_scope={counters['out_of_scope_count']} "
        f"kinds(job={counters.get('job_kind_count', 0)},vacation_work={counters.get('vacation_work_kind_count', 0)},unknown={counters.get('unknown_kind_count', 0)}) "
        f"stages(early={counters.get('early_career_count', 0)},experienced={counters.get('experienced_count', 0)},unknown={counters.get('unknown_career_stage_count', 0)}) "
        f"scope(in={counters.get('in_scope_count', 0)},unclassified={counters.get('scope_unclassified_count', 0)},out={counters.get('out_of_scope_count', 0)}) "
        f"pages={counters.get('pages_traversed', 0)} "
        f"details={counters.get('detail_pages_fetched', 0)} "
        f"valid_apply_urls={counters.get('valid_application_links', 0)} "
        f"parsing_errors={counters.get('parsing_errors', 0)} "
        f"evidence_ambiguities={counters.get('evidence_ambiguities_count', 0)} "
        f"invalid_experience_parses={counters.get('invalid_experience_parses_count', 0)} "
        f"title_only_exclusions={counters.get('title_only_exclusions_count', 0)} "
        f"numeric_only_exclusions={counters.get('numeric_only_exclusions_count', 0)} "
        f"discovery_qualification_duration_as_experience={counters.get('discovery_qualification_duration_as_experience_count', 0)} "
        f"woolworths_qualification_duration_as_experience={counters.get('woolworths_qualification_duration_as_experience_count', 0)} "
        f"greenhouse_office_as_business_unit={counters.get('greenhouse_office_as_business_unit_count', 0)} "
        f"greenhouse_education_config_as_qualification={counters.get('greenhouse_education_config_as_qualification_count', 0)} "
        f"application_question_as_eligibility={counters.get('application_question_as_eligibility_count', 0)} "
        f"invalid_application_routes={counters.get('invalid_application_routes_count', 0)} "
        f"link_errors={counters.get('link_errors', 0)} errors={counters['errors_count']}"
    ))
    platform_keys = (
        "non_south_africa_postings", "duplicate_listings", "detail_failures",
    )
    if any(counters.get(key, 0) for key in platform_keys):
        command.stdout.write(
            f"platform discovered={counters.get('listings_discovered', 0)} "
            f"country_excluded={counters.get('non_south_africa_postings', 0)} "
            f"duplicate_listings={counters.get('duplicate_listings', 0)} "
            f"detail_failures={counters.get('detail_failures', 0)}"
        )
    for error in counters.get("errors", []):
        command.stderr.write(command.style.WARNING(f"{source_code}: {error.get('error', error)}"))
    for index, item in enumerate(counters.get("diagnostics", []), start=1):
        command.stdout.write(f"\nEXPLAIN {source_code} #{index}")
        for label, key in (
            ("title", "title"),
            ("source", "source"),
            ("normalized opportunity type", "opportunity_type"),
            ("opportunity kind", "opportunity_kind"),
            ("career stage", "career_stage"),
            ("scope status", "scope_status"),
            ("status", "status"),
            ("classification reason", "classification_reason"),
            ("classification evidence", "classification_evidence"),
            ("evidence provenance", "evidence_provenance"),
            ("experience_text", "experience_text"),
            ("experience_min_years", "experience_min_years"),
            ("qualification_text", "qualification_text"),
            ("minimum_nqf", "minimum_nqf"),
            ("listing_kind", "listing_kind"),
            ("listing_url", "listing_url"),
            ("detail/source_url", "source_url"),
            ("apply_url", "apply_url"),
            ("detail HTTP status", "detail_http_status"),
            ("application link valid", "application_link_valid"),
        ):
            command.stdout.write(f"{label}: {_display(item.get(key))}")
