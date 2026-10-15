"""Read-only audit probe #2 (fills gaps from probe #1). SELECT/COUNT only."""
import os
os.environ['DEBUG'] = '1'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
import django
django.setup()

from django.db import connection
from collections import Counter
from django.db.migrations.recorder import MigrationRecorder

applied = MigrationRecorder(connection).applied_migrations()
c = Counter(app for (app, name) in applied.keys())
print('=== APPLIED MIGRATIONS PER APP ===')
for app in sorted(c):
    print(app, c[app])
print('TOTAL_APPLIED', len(applied))

from core import models as m

print('=== OPPORTUNITY STATUS DIST ===')
for fld in ('lifecycle_status', 'is_active', 'scope_status', 'listing_kind'):
    try:
        print(fld, dict(Counter(m.Opportunity.objects.values_list(fld, flat=True))))
    except Exception as e:
        print(fld, 'ERR', type(e).__name__)
try:
    print('professionally_ready', m.Opportunity.objects.professionally_ready().count())
except Exception as e:
    print('prof_ready ERR', type(e).__name__, e)
print('with_active_clean_rev', m.Opportunity.objects.filter(active_clean_revision__isnull=False).count())

print('=== RELATIONSHIP ASSESSMENT final_class ===')
print(dict(Counter(m.TargetOpportunityRelationshipAssessment.objects.values_list('final_class', flat=True))))

print('=== PROFILE FEED EVALUATION (v2) discovery_category ===')
try:
    print(dict(Counter(m.ProfileFeedEvaluation.objects.values_list('discovery_category', flat=True))))
except Exception as e:
    print('ERR', type(e).__name__, 'fields=', [f.name for f in m.ProfileFeedEvaluation._meta.fields])

print('=== LEGACY ProfileOpportunityEvaluation discovery_category ===')
print(dict(Counter(m.ProfileOpportunityEvaluation.objects.values_list('discovery_category', flat=True))))

print('=== CAREER TARGET RESOLUTION state ===')
print(dict(Counter(m.CareerTargetResolution.objects.values_list('state', flat=True))))

print('=== GENERATION state (ProfileEvaluationGeneration) ===')
print(dict(Counter(m.ProfileEvaluationGeneration.objects.values_list('state', flat=True))))

print('=== PROFILEFEEDSTATE state ===')
print(dict(Counter(m.ProfileFeedState.objects.values_list('state', flat=True))))

print('DONE2')
