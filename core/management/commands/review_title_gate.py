import json
import re

from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import OpportunityTitleGateDecision


class Command(BaseCommand):
    help = 'Review new title-gate blocks for implementation mistakes without changing policy.'

    def handle(self, *args, **options):
        newly_blocked = OpportunityTitleGateDecision.objects.filter(
            decision='BLOCK').exclude(revision__seniority_label='OBVIOUS_SENIOR')
        reasons = {}
        for reason in newly_blocked.values_list('reason_code', flat=True).distinct():
            rows = newly_blocked.filter(reason_code=reason).order_by(
                'revision__cleaned_title', 'pk')
            reasons[reason] = {
                'count': rows.count(),
                'samples': list(rows.values_list(
                    'revision__cleaned_title', flat=True)[:10]),
            }
        assistant_pattern = re.compile(r'(?i)\bassistant\W+manager\b')
        assistant_manager_only_errors = []
        blocked_substring_errors = []
        blocked_tokens = {
            'senior', 'sr', 'lead', 'principal', 'director', 'head',
            'chief', 'vp', 'manager',
        }
        for row in newly_blocked.select_related('revision'):
            title = row.revision.cleaned_title
            tokens = re.findall(r'[a-z0-9]+', title.casefold())
            if (row.reason_code == 'MANAGER' and assistant_pattern.search(title)
                    and all(token != 'manager' or index > 0 and tokens[index - 1] == 'assistant'
                            for index, token in enumerate(tokens))):
                assistant_manager_only_errors.append(title)
            if row.reason_code in {
                    'SENIOR', 'SENIOR_ABBREVIATION', 'LEAD', 'PRINCIPAL',
                    'DIRECTOR', 'HEAD', 'CHIEF', 'VICE_PRESIDENT_ABBREVIATION',
                    'MANAGER'} and not blocked_tokens.intersection(tokens):
                blocked_substring_errors.append(title)
        allowed_executives = OpportunityTitleGateDecision.objects.filter(
            decision='ALLOW', reason_code='NON_AUTHORITY_EXECUTIVE').order_by(
                'revision__cleaned_title')
        report = {
            'newly_blocked_total': newly_blocked.count(),
            'by_reason': reasons,
            'assistant_manager_exception_errors': assistant_manager_only_errors,
            'substring_tokenization_errors': blocked_substring_errors,
            'allowed_non_authority_executive_count': allowed_executives.count(),
            'allowed_non_authority_executive_samples': list(
                allowed_executives.values_list(
                    'revision__cleaned_title', flat=True)[:20]),
        }
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
