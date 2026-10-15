"""Read-only inspection of backfill progress. Writes nothing."""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "CareerGPS_Rebuild.settings")
django.setup()

from django.db import connections
from django.db.models import Count, F

from core.models import (
    EvaluationRecomputeTask,
    OccupationSourceResolution,
    Opportunity,
    OpportunityCleanRevision,
    OpportunityTitleGateDecision,
    TargetOpportunityRelationshipAssessment,
)
from core.professional_policies import TITLE_GATE_POLICY_VERSION

print("engine", connections["default"].settings_dict["ENGINE"])
print("db", connections["default"].settings_dict["NAME"])
print("TITLE_GATE_POLICY_VERSION", TITLE_GATE_POLICY_VERSION)

gate = OpportunityTitleGateDecision.objects
print("gate_rows_total", gate.count())
print("gate_by_decision", dict(
    gate.values_list("decision").annotate(c=Count("pk")).order_by("decision")))
print("gate_by_policy", dict(
    gate.values_list("policy_version").annotate(c=Count("pk")).order_by(
        "policy_version")))

active = OpportunityCleanRevision.objects.filter(
    status="active", opportunity__active_clean_revision_id=F("pk"))
print("active_revisions", active.count())

print("source_resolutions", dict(
    OccupationSourceResolution.objects.values_list("state").annotate(
        c=Count("pk")).order_by("state")))
print("assessments", TargetOpportunityRelationshipAssessment.objects.count())
print("recompute_tasks", dict(
    EvaluationRecomputeTask.objects.values_list("state").annotate(
        c=Count("pk")).order_by("state")))

base = Opportunity.objects.professionally_ready(include_obvious_senior=False)
covered = base.filter(
    active_clean_revision__title_gate_decision__decision="ALLOW",
    active_clean_revision__title_gate_decision__policy_version=(
        TITLE_GATE_POLICY_VERSION),
)
print("base_universe", base.count())
print("gate_covered_allow", covered.count())
print("senior_gate_ready_now", bool(covered.exists()) or not base.exists())
