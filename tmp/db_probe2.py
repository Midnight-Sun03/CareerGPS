import os
import sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "CareerGPS_Rebuild.settings")
sys.path.insert(0, "D:\\CG")
import django
django.setup()
from core import models as c
from employers import models as e
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

print("Travoa final_class:", dist(c.TargetOpportunityRelationshipAssessment, "final_class"))
print("CareerTargetResolution state:", dist(c.CareerTargetResolution, "state"))
print("OpportunityCleanRevision status:", dist(c.OpportunityCleanRevision, "status"))
print("OrganizationVerification state:", dist(e.OrganizationVerification, "state"))
print("EmployerVacancy lifecycle_status:", dist(e.EmployerVacancy, "lifecycle_status"))
print("OpportunityCleanRevision top3 ids:", list(c.OpportunityCleanRevision.objects.values_list("id","status").all()[:3]))
print("OrganizationVerification top3:", list(e.OrganizationVerification.objects.values_list("id","state").all()[:3]))
print("EmployerVacancy top3:", list(e.EmployerVacancy.objects.values_list("id","lifecycle_status").all()[:3]))
