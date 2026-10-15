import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
os.environ['DEBUG'] = '1'
django.setup()
from collections import Counter
from core import models as m
from ingestion import models as im
from employers import models as em
from applications import models as am
from interview import models as ivm

def cnt(qs):
    return qs.count()

print('=== TAXONOMY ===')
for name in ['TaxonomyRelease','TaxonomyConcept','TaxonomyLabel','TaxonomyRelation','TaxonomyCrosswalk','TaxonomyReleaseSet','TaxonomyEmbedding','TaxonomyOccupationContext']:
    print(name, cnt(getattr(m,name).objects.all()))
print('releases:', list(m.TaxonomyRelease.objects.values_list('source','is_active','version')[:10]) if hasattr(m.TaxonomyRelease.objects,'values_list') else 'n/a')

print('=== CLEAN JOBS ===')
print('OpportunityCleanAttempt', cnt(m.OpportunityCleanAttempt.objects.all()))
print('OpportunityCleanRevision', cnt(m.OpportunityCleanRevision.objects.all()))
print('CleanOpportunityCriterion', cnt(m.CleanOpportunityCriterion.objects.all()))
print('CleanCriterionEvidence', cnt(m.CleanCriterionEvidence.objects.all()))

print('=== OCCUPATIONAL INTELLIGENCE ===')
for name in ['OpportunityOccupationResolution','OpportunityOccupationIdentity','CareerTargetResolution','CareerTargetIdentity','OccupationSourceResolution','OccupationRelationshipPolicy','OccupationRelationshipRule','TargetOpportunityRelationshipAssessment','OccupationDefiningCriterion']:
    print(name, cnt(getattr(m,name).objects.all()))

print('=== INGESTION ===')
for name in ['IngestionSource','GreenhouseBoard','SmartRecruitersCompany','WorkdayBoard','IngestionRun','RawOpportunity','OpportunitySourceRecord','OpportunityExtraction']:
    print(name, cnt(getattr(im,name).objects.all()))
print('RawOpportunity normalization_status', Counter(im.RawOpportunity.objects.values_list('normalization_status', flat=True)))
print('IngestionRun status', Counter(im.IngestionRun.objects.values_list('status', flat=True)))
print('OpportunityExtraction status', Counter(im.OpportunityExtraction.objects.values_list('status', flat=True)) if hasattr(im.OpportunityExtraction, 'status') else 'n/a')

print('=== CANDIDATE ===')
for name in ['Profile','CareerPreference','ProfileSkill','WorkExperience','Education','ProjectCapability']:
    print(name, cnt(getattr(m,name).objects.all()))

print('=== EMPLOYER ===')
for name in ['Organization','EmployerVacancy','HiringApplication','TalentPool','SavedCandidate','TalentInvitation','OrganizationVerification']:
    print(name, cnt(getattr(em,name).objects.all()))

print('=== APPLICATIONS/DOCS ===')
print('Application', cnt(am.Application.objects.all()))
print('GeneratedDocument', Counter(am.GeneratedDocument.objects.values_list('document_type', flat=True)))

print('=== INTERVIEW ===')
print('Interview', cnt(ivm.Interview.objects.all()))
print('InterviewQuestion', cnt(ivm.InterviewQuestion.objects.all()))
print('DONE3')
