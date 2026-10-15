import re

f = "d:/CG/core/clean_jobs/contracts.py"
lines = open(f).read().split("\n")

# Find 'class Job' and print through end of 'class JobProvenance' (the block right after Evidence/SourceReference)
starts = {}
for i, l in enumerate(lines):
    for name in ("class Job", "class JobProvenance", "class CleanJobPayload", "class Source", "class Cleaner", "class Evidence", "class SourceReference", "class Location", "class Salary", "class Criterion", "class CleanCriterionEvidence", "class OpportunityCleanAttempt", "class OpportunityCleanRevision", "def _activate_valid_contract", "def _revision_values", "class VacancyRequirement"):
        if l.startswith(name):
            starts.setdefault(name, i)

for name in sorted(starts, key=starts.get):
    print(f"\n===== {name} @ {starts[name]} =====")
    if name == "class VacancyRequirement":
        # print models.py section instead
        g = "d:/CG/employers/models.py"
        ll = open(g).read().split("\n")
        s = next(i for i, l in enumerate(ll) if l.startswith("class VacancyRequirement"))
        e = next(i for i, l in enumerate(ll) if i > s and l.startswith("class ") and "Vacancy" not in l)
        print("\n".join(ll[s:e]))
    elif name == "class OpportunityCleanAttempt" or name == "class OpportunityCleanRevision":
        g = "d:/CG/core/models.py"
        ll = open(g).read().split("\n")
        s = starts["class OpportunityCleanAttempt"]
        e = starts["class OpportunityOccupationResolution"]
        print("\n".join(ll[s:e]))

for key in ("class Job", "class JobProvenance", "class CleanJobPayload"):
    if key in starts:
        s = starts[key]
        e = None
        for k in ("class Cleaner", "class Source", "class CleanCriterionEvidence", "class OpportunityCleanRevision"):
            if k in starts and starts[k] > s:
                e = starts[k]
                break
        print(f"\n===== {key} @ {s} =====")
        print("\n".join(lines[s:e]))
