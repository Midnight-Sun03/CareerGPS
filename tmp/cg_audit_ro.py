"""Read-only audit probe. SELECT/COUNT only; no writes, no migrations."""
import os
os.environ['DEBUG'] = '1'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
import django
django.setup()

from django.db import connection
from collections import Counter

sd = connection.settings_dict
print('=== DB IDENTITY ===')
print('ENGINE:', sd.get('ENGINE'))
print('NAME:', sd.get('NAME'))
print('HOST:', sd.get('HOST') or '(local)')

from django.db.migrations.recorder import MigrationRecorder
applied = MigrationRecorder(connection).applied_migrations()
c = Counter(app for (app, name) in applied.keys())
print('=== APPLIED MIGRATIONS PER APP ===')
for app in sorted(c):
    print(app, c[app])
print('TOTAL_APPLIED', len(applied))


def count(model):
    try:
        return model.objects.count()
    except Exception as e:
        return 'ERR:' + type(e).__name__


from core import models as m
from ingestion import models as im
from applications import models as am
from interview import models as ivm
from employers import models as em

print('=== MODEL COUNTS ===')
for label, model in [
    ('core.Profile', m.Profile),
    ('core.CareerPreference', m.CareerPreference),
    ('core.Opportunity', m.Opportunity),
    ('core.SavedOpportunity', m.SavedOpportunity),
    ('core.Notification', m.Notification),
    ('core.Story', m.Story),
    ('core.TaxonomyRelease', m.TaxonomyRelease),
    ('core.TaxonomyConcept', m.TaxonomyConcept),
    ('core.TaxonomyLabel', m.TaxonomyLabel),
    ('core.TaxonomyRelation', m.TaxonomyRelation),
    ('core.TaxonomyCrosswalk', m.TaxonomyCrosswalk),
    ('core.OpportunityCleanAttempt', m.OpportunityCleanAttempt),
    ('core.OpportunityCleanRevision', m.OpportunityCleanRevision),
    ('core.CleanOpportunityCriterion', m.CleanOpportunityCriterion),
    ('core.OpportunityOccupationResolution', m.OpportunityOccupationResolution),
    ('core.CareerTargetResolution', m.CareerTargetResolution),
    ('core.TargetOpportunityRelationshipAssessment', m.TargetOpportunityRelationshipAssessment),
    ('core.OccupationRelationshipPolicy', m.OccupationRelationshipPolicy),
    ('core.OccupationRelationshipRule', m.OccupationRelationshipRule),
    ('core.ProfileOpportunityEvaluation', m.ProfileOpportunityEvaluation),
    ('core.ProfileFeedEvaluation', m.ProfileFeedEvaluation),
    ('core.EvaluationRecomputeTask', m.EvaluationRecomputeTask),
    ('core.ProfileEvaluationGeneration', m.ProfileEvaluationGeneration),
    ('core.ProfileFeedState', m.ProfileFeedState),
    ('ingestion.IngestionSource', im.IngestionSource),
    ('ingestion.RawOpportunity', im.RawOpportunity),
    ('ingestion.OpportunitySourceRecord', im.OpportunitySourceRecord),
    ('ingestion.OpportunityExtraction', im.OpportunityExtraction),
    ('ingestion.IngestionRun', im.IngestionRun),
    ('ingestion.GreenhouseBoard', im.GreenhouseBoard),
    ('ingestion.SmartRecruitersCompany', im.SmartRecruitersCompany),
    ('ingestion.WorkdayBoard', im.WorkdayBoard),
    ('applications.Application', am.Application),
    ('applications.GeneratedDocument', am.GeneratedDocument),
    ('interview.Interview', ivm.Interview),
    ('interview.InterviewQuestion', ivm.InterviewQuestion),
    ('employers.Organization', em.Organization),
    ('employers.EmployerVacancy', em.EmployerVacancy),
    ('employers.HiringApplication', em.HiringApplication),
    ('employers.TalentPool', em.TalentPool),
    ('employers.OrganizationVerification', em.OrganizationVerification),
]:
    print(label, count(model))

print('=== TAXONOMY RELEASES ===')
try:
    for r in m.TaxonomyRelease.objects.all().values('source', 'version', 'is_active'):
        print(dict(r))
except Exception as e:
    print('ERR', type(e).__name__, 'fields=', [f.name for f in m.TaxonomyRelease._meta.fields])

print('=== OPPORTUNITY STATUS DIST ===')
try:
    print(dict(Counter(m.Opportunity.objects.values_list('status', flat=True))))
except Exception as e:
    print('status ERR', type(e).__name__, 'fields=', [f.name for f in m.Opportunity._meta.fields])
try:
    print('professionally_ready', m.Opportunity.objects.professionally_ready().count())
except Exception as e:
    print('prof_ready ERR', type(e).__name__)
try:
    print('with_active_clean_rev', m.Opportunity.objects.filter(active_clean_revision__isnull=False).count())
except Exception as e:
    print('active_rev ERR', type(e).__name__)

print('=== CLEAN REVISION STATUS ===')
try:
    print(dict(Counter(m.OpportunityCleanRevision.objects.values_list('status', flat=True))))
except Exception as e:
    print('ERR', type(e).__name__)

print('=== RELATIONSHIP ASSESSMENT discovery_category ===')
try:
    print(dict(Counter(m.TargetOpportunityRelationshipAssessment.objects.values_list('discovery_category', flat=True))))
except Exception as e:
    print('ERR', type(e).__name__, 'fields=', [f.name for f in m.TargetOpportunityRelationshipAssessment._meta.fields])

print('=== EVAL QUEUE STATE ===')
try:
    print(dict(Counter(m.EvaluationRecomputeTask.objects.values_list('state', flat=True))))
except Exception as e:
    print('ERR', type(e).__name__, 'fields=', [f.name for f in m.EvaluationRecomputeTask._meta.fields])

print('=== FEED READ VERSION ===')
from django.conf import settings
print('CAREERGPS_FEED_READ_VERSION', getattr(settings, 'CAREERGPS_FEED_READ_VERSION', '?'))
print('CAREERGPS_V2_WORKER_SCHEDULED', getattr(settings, 'CAREERGPS_V2_WORKER_SCHEDULED', '?'))
print('DONE')
