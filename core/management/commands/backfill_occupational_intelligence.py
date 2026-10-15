"""Build and report Block 1 occupational intelligence without feed cutover."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F, Q
from django.utils import timezone

from core.models import (
    CareerPreference,
    EvaluationRecomputeTask,
    OccupationSourceResolution,
    OpportunityCleanRevision,
    OpportunityTitleGateDecision,
    TargetOpportunityRelationshipAssessment,
    TaxonomyConcept,
    TaxonomyCrosswalk,
    TaxonomyLabel,
    TaxonomyRelation,
)
from core.services.occupational_resolution import (
    maintain_target_resolution_containers,
    resolve_target_sources,
    resolve_vacancy_sources,
)
from core.services.relationship_aggregation import assess_target_vacancy
from core.services.title_gate import (
    READ_ONLY_SENIORITY_POLICY_VERSION, evaluate_revision,
)
from core.taxonomies.normalization import normalize_term


UNSAFE_PAIRS = (
    ('Internal Auditor', 'accountant'),
    ('QA Engineer', 'application engineer'),
    ('Demand Planner', 'purchaser'),
    ('Corporate Counsel', 'lawyer'),
    ('Marketing Manager', 'chief marketing officer'),
)


def _immutable_fingerprint(revisions):
    rows = revisions.order_by('pk').values_list(
        'pk', 'source_input_hash', 'payload_hash', 'cleaned_title', 'canonical_payload')
    return sha256(json.dumps(
        list(rows), sort_keys=True, separators=(',', ':'), default=str,
    ).encode()).hexdigest()


def _source_coverage(rows):
    by_parent = {}
    for row in rows:
        parent = ('target', row.target_resolution_id) if row.target_resolution_id else (
            'vacancy', row.opportunity_resolution_id)
        by_parent.setdefault(parent, {})[row.source] = row.state
    counts = Counter()
    for sources in by_parent.values():
        ofo = sources.get('ofo', 'MISSING')
        esco = sources.get('esco', 'MISSING')
        counts[f'ofo_{ofo.casefold()}'] += 1
        counts[f'esco_{esco.casefold()}'] += 1
        if ofo == 'RESOLVED' and esco == 'RESOLVED':
            counts['both_resolved'] += 1
        elif ofo == 'RESOLVED' and esco != 'RESOLVED':
            counts['ofo_only_resolved'] += 1
        elif esco == 'RESOLVED' and ofo != 'RESOLVED':
            counts['esco_only_resolved'] += 1
        else:
            counts['neither_resolved'] += 1
    counts['subjects'] = len(by_parent)
    return dict(sorted(counts.items()))


class Command(BaseCommand):
    help = 'Backfill and report shadow Block 1 occupational intelligence.'

    def add_arguments(self, parser):
        parser.add_argument('--limit-revisions', type=int)
        parser.add_argument('--limit-targets', type=int)
        parser.add_argument('--verify-determinism', action='store_true')

    def handle(self, *args, **options):
        revisions = OpportunityCleanRevision.objects.filter(
            status='active', opportunity__active_clean_revision_id=F('pk'),
        ).select_related('opportunity').order_by('pk')
        if options['limit_revisions']:
            revision_ids = list(revisions.values_list(
                'pk', flat=True)[:options['limit_revisions']])
            revisions = OpportunityCleanRevision.objects.filter(
                pk__in=revision_ids).select_related('opportunity').order_by('pk')
        revision_list = list(revisions)
        immutable_before = _immutable_fingerprint(revisions)
        taxonomy_before = {
            'concepts': TaxonomyConcept.objects.count(),
            'labels': TaxonomyLabel.objects.count(),
            'relations': TaxonomyRelation.objects.count(),
            'crosswalks': TaxonomyCrosswalk.objects.count(),
        }
        seniority_rows_before = OpportunityTitleGateDecision.objects.count()

        self.stdout.write(
            f'Applying the read-only seniority check to {len(revision_list)} '
            'active revisions...')
        seniority_counts = Counter({'ALLOW': 0, 'BLOCK': 0})
        seniority_reasons = Counter()
        label_counts = Counter()
        cross_counts = Counter()
        allowed = []
        excluded_revision_ids = []
        for revision in revision_list:
            gate = evaluate_revision(revision)
            seniority_counts[gate.decision] += 1
            label_is_senior = revision.seniority_label == 'OBVIOUS_SENIOR'
            label_counts[
                'cleaner_obvious_senior' if label_is_senior
                else 'other_label'] += 1
            cross_counts[
                f'{gate.decision.casefold()}_vs_'
                f'{"cleaner_obvious_senior" if label_is_senior else "cleaner_other"}'] += 1
            if gate.blocked:
                # Read-only exclusion: nothing is written, and the computed
                # reason is reported so the exclusion stays explainable.
                seniority_reasons[gate.reason_code] += 1
                excluded_revision_ids.append(revision.pk)
                continue
            allowed.append(revision)
            resolve_vacancy_sources(revision)

        self.stdout.write('Maintaining authored target containers and resolving each source...')
        targets = []
        preferences = CareerPreference.objects.select_related('profile').order_by('pk')
        for preference in preferences:
            for target in maintain_target_resolution_containers(preference):
                resolve_target_sources(target)
                targets.append(target)
        if options['limit_targets']:
            targets = targets[:options['limit_targets']]

        self.stdout.write(
            f'Assessing {len(targets)} targets x {len(allowed)} allowed revisions...')
        for target in targets:
            for revision in allowed:
                assess_target_vacancy(target, revision)

        if options['verify_determinism']:
            hashes_before = dict(TargetOpportunityRelationshipAssessment.objects.filter(
                target_resolution__in=targets, revision__in=revision_list,
            ).values_list('pk', 'dependency_hash'))
            for target in targets:
                resolve_target_sources(target)
                for revision in allowed:
                    assess_target_vacancy(target, revision)
            hashes_after = dict(TargetOpportunityRelationshipAssessment.objects.filter(
                pk__in=hashes_before).values_list('pk', 'dependency_hash'))
            if hashes_before != hashes_after:
                raise CommandError('Determinism verification changed assessment dependencies.')

        immutable_after = _immutable_fingerprint(revisions)
        taxonomy_after = {
            'concepts': TaxonomyConcept.objects.count(),
            'labels': TaxonomyLabel.objects.count(),
            'relations': TaxonomyRelation.objects.count(),
            'crosswalks': TaxonomyCrosswalk.objects.count(),
        }
        seniority_rows_after = OpportunityTitleGateDecision.objects.count()
        if immutable_before != immutable_after:
            raise CommandError('An immutable clean revision changed during shadow backfill.')
        if taxonomy_before != taxonomy_after:
            raise CommandError('Raw taxonomy facts changed during shadow backfill.')
        if seniority_rows_before != seniority_rows_after:
            raise CommandError(
                'The occupational backfill wrote a seniority decision row.')

        source_rows = list(OccupationSourceResolution.objects.filter(
            Q(target_resolution__in=targets)
            | Q(opportunity_resolution__revision__in=revision_list)
        ))
        source_rows = list({row.pk: row for row in source_rows}.values())
        coverage = _source_coverage(source_rows)

        assessments = TargetOpportunityRelationshipAssessment.objects.filter(
            target_resolution__in=targets, revision__in=revision_list)
        class_counts = dict(assessments.values_list('final_class').annotate(
            count=Count('pk')).order_by('final_class'))
        tier_counts = {
            f'{row[0]}:{row[1]}': row[2]
            for row in assessments.values_list(
                'final_class', 'evidence_tier').annotate(
                    count=Count('pk')).order_by('final_class', 'evidence_tier')
        }
        source_outcomes = Counter({
            'ofo_esco_exact_agreement': 0,
            'ofo_esco_related_agreement': 0,
            'same_outcome_including_non_admission': 0,
        })
        for assessment in assessments.only('ofo_comparison', 'esco_comparison'):
            ofo_outcome = assessment.ofo_comparison.get('outcome', 'MISSING')
            esco_outcome = assessment.esco_comparison.get('outcome', 'MISSING')
            source_outcomes[f'ofo_{ofo_outcome.casefold()}'] += 1
            source_outcomes[f'esco_{esco_outcome.casefold()}'] += 1
            if ofo_outcome == esco_outcome:
                source_outcomes['same_outcome_including_non_admission'] += 1
            if ofo_outcome == esco_outcome == 'EXACT':
                source_outcomes['ofo_esco_exact_agreement'] += 1
            if ofo_outcome == esco_outcome == 'RELATED':
                source_outcomes['ofo_esco_related_agreement'] += 1

        unsafe = {'observed': 0, 'rejected': 0, 'unexpected_admitted': []}
        target_by_text = {}
        for target in targets:
            target_by_text.setdefault(normalize_term(target.authored_text), []).append(target.pk)
        revision_by_title = {}
        for revision in revision_list:
            revision_by_title.setdefault(normalize_term(revision.cleaned_title), []).append(revision.pk)
        for target_text, title in UNSAFE_PAIRS:
            target_ids = target_by_text.get(normalize_term(target_text), [])
            revision_ids = revision_by_title.get(normalize_term(title), [])
            if not target_ids or not revision_ids:
                continue
            unsafe['observed'] += len(target_ids) * len(revision_ids)
            rows = assessments.filter(
                target_resolution_id__in=target_ids, revision_id__in=revision_ids)
            for row in rows:
                if row.final_class == 'RELATED':
                    unsafe['unexpected_admitted'].append(row.pk)
                else:
                    unsafe['rejected'] += 1

        unresolved_examples = {}
        for source in ('ofo', 'esco'):
            unresolved_examples[source] = list(
                OccupationSourceResolution.objects.filter(
                    pk__in=[row.pk for row in source_rows], source=source,
                    state='UNRESOLVED',
                ).values('normalized_input').annotate(
                    count=Count('pk')).order_by('-count', 'normalized_input')[:10])

        task_states = dict(EvaluationRecomputeTask.objects.values_list(
            'state').annotate(count=Count('pk')).order_by('state'))
        task_reasons = dict(EvaluationRecomputeTask.objects.values_list(
            'reason').annotate(count=Count('pk')).order_by('-count', 'reason'))
        oldest_pending = EvaluationRecomputeTask.objects.filter(
            state='PENDING').order_by('created_at').first()
        retried = EvaluationRecomputeTask.objects.filter(attempts__gt=0).count()
        failed_with_errors = EvaluationRecomputeTask.objects.filter(
            state='FAILED').exclude(error_message='').count()
        queue = {
            'states': task_states,
            'reasons': task_reasons,
            'oldest_pending_at': oldest_pending.created_at if oldest_pending else None,
            'oldest_pending_age_seconds': (
                int((timezone.now() - oldest_pending.created_at).total_seconds())
                if oldest_pending else None),
            'retried_tasks': retried,
            'max_attempts': EvaluationRecomputeTask.objects.order_by(
                '-attempts').values_list('attempts', flat=True).first() or 0,
            'failed_with_error_message': failed_with_errors,
        }

        report = {
            'scope': {
                'active_revisions': len(revision_list),
                'allowed_revisions': len(allowed),
                'authored_targets': len(targets),
                'assessment_pairs': assessments.count(),
                'production_feed_cutover': False,
            },
            'seniority_check': {
                'policy_version': READ_ONLY_SENIORITY_POLICY_VERSION,
                'computed_read_only': True,
                'decisions': dict(sorted(seniority_counts.items())),
                'block_reasons': dict(sorted(seniority_reasons.items())),
                'cleaner_labels': dict(sorted(label_counts.items())),
                'decision_by_cleaner_label': dict(sorted(cross_counts.items())),
                'excluded_revisions': len(excluded_revision_ids),
                'excluded_revision_sample': excluded_revision_ids[:10],
            },
            'resolver_coverage': coverage,
            'assessment_source_outcomes': dict(sorted(source_outcomes.items())),
            'relationship_classes': class_counts,
            'relationship_tiers': tier_counts,
            'known_unsafe_relationships': unsafe,
            'unresolved_examples': unresolved_examples,
            'high_volume_relationship_reasons': list(
                assessments.values('final_class', 'reason_code').annotate(
                    count=Count('pk')).order_by('-count', 'final_class', 'reason_code')[:10]),
            'queue_diagnostics': queue,
            'integrity': {
                'clean_revision_fingerprint_unchanged': immutable_before == immutable_after,
                'raw_taxonomy_counts_unchanged': taxonomy_before == taxonomy_after,
                'raw_taxonomy_counts': taxonomy_after,
                'seniority_decision_rows_written': 0,
                'legacy_seniority_rows_ignored': seniority_rows_after,
                'determinism_verified': bool(options['verify_determinism']),
            },
        }
        self.stdout.write(json.dumps(report, indent=2, default=str, sort_keys=True))
