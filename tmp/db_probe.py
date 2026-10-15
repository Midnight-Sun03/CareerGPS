import os
import sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "CareerGPS_Rebuild.settings")
sys.path.insert(0, "D:\\CG")
import django
django.setup()
from core import models as c
from employers import models as e
from interview import models as i
from ingestion import models as g
from django.db import connection
from django.db.models import Count
from collections import Counter

def dist(model, field):
    try:
        rows = (model.objects
                .values_list(field).annotate(n=Count('id'))
                .order_by(field))
        out = {}
        for row in rows:
            out[str(row[0])] = row[1]
        return out
    except Exception as ex:
        return {"ERR: "+str(ex)}

print("=== COUNTS (per app) ===")
models = [
    ("core", [c.Opportunity, c.Profile, c.ProfileOpportunityEvaluation,
        c.ProfileFeedEvaluation, c.OpportunityCleanRevision,
        c.CleanOpportunityCriterion, c.TaxonomyConcept, c.TaxonomyRelease,
        c.OpportunityOccupationResolution, c.OccupationRelationshipPolicy,
        c.OccupationRelationshipRule, c.TargetOpportunityRelationshipAssessment,
        c.CareerTargetResolution, c.SavedOpportunity,
        c.CareerMarketSnapshot]),
    ("employers", [e.EmployerVacancy, e.Organization, e.OrganizationVerification,
        e.HiringAssignment, e.TalentPool, e.SavedCandidate]),
    ("interview", [i.Interview, i.InterviewQuestion]),
    ("ingestion", [g.RawOpportunity, g.IngestionRun, g.IngestionSource,
        g.OpportunitySourceRecord, g.OpportunityExtraction]),
    ("core-extra", [c.Story, c.Notification]),

]
for app, ms in models:
    print("---- " + app + " ----")
    for cls in ms:
        try:
            print(cls.__name__ + "=" + str(cls.objects.count()))
        except Exception as ex:
            print(cls.__name__ + "=ERR" + str(ex))

print("=== vetoes/status/category dists ===")
print("Opportunity lifecycle_status:", dist(c.Opportunity, "lifecycle_status"))
print("ProfileOpportunityEvaluation discovery_category:", dist(c.ProfileOpportunityEvaluation, "discovery_category"))
print("ProfileFeedEvaluation discovery_category:", dist(c.ProfileFeedEvaluation, "discovery_category"))
print("TargetOpportunityRelationshipAssessment state:", dist(c.TargetOpportunityRelationshipAssessment, "state"))
print("CareerTargetResolution state:", dist(c.CareerTargetResolution, "state"))
print("OpportunityCleanRevision status:", dist(c.OpportunityCleanRevision, "status"))
print("OrganizationVerification verification_status:", dist(c.OrganizationVerification, "verification_status"))
print("EmployerVacancy vacancy_status:", dist(c.EmployerVacancy, "vacancy_status"))

