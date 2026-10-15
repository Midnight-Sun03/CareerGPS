# CareerGPS Phase 2.3 — Audited Trusted Taxonomy Matching

## Objective

Phase 2.3 improves Phase 2.2 when profile evidence and opportunity criteria use different wording but a trusted taxonomy establishes safe structure.

The rules are deliberately conservative:

- Phase 2.2 remains the hard-truth layer.
- No Gemini, LLM, embeddings, vector search, reranker, or AI classifier is used in matching.
- Trusted identity labels may produce `FOUND`.
- A **direct** trusted relationship may produce `RELATED` only where the evidence policy permits it.
- Phase 2.3 does not chain two-hop/three-hop relationships.
- Sharing an ISCO group is **not matching evidence**. It is only a candidate-discovery/cross-taxonomy bridge.
- `RELATED` is score-neutral in Phase 2.3. Phase 2.5 will decide whether it deserves calibrated numeric weight.
- Missing or ambiguous knowledge leaves the Phase 2.2 result unchanged.
- Hard failures such as insufficient dated experience cannot be rescued by taxonomy relationships.

## Trusted sources now loaded

### South African OFO 2021

The importer now reads the real official workbook structure explicitly:

- `Occupations` = canonical OFO concepts
- `Specialisations` = conservative supporting labels
- `Alternate Title` = identity-strength alternate labels

Audited import in the bundled database:

- **1,511 canonical occupations**
- **7,659 persisted labels**
- the invalid `2021-000000 / Test` alternate-title row is rejected
- specialisation rows pointing to non-canonical/deleted codes are rejected rather than guessed

Each OFO occupation stores its four-digit ISCO-aligned unit-group code as metadata. That metadata may help candidate discovery, but it never creates `FOUND` or `RELATED` by itself.

### ESCO 1.2.1

The importer uses canonical `conceptUri` identity and deduplicates repeated CSV rows for the same URI before persistence.

Required files:

- `skills_en.csv`
- `skillGroups_en.csv`
- `occupations_en.csv`
- `ISCOGroups_en.csv`
- `broaderRelationsSkillPillar_en.csv`
- `broaderRelationsOccPillar_en.csv`
- `occupationSkillRelations_en.csv`

Audited import in the bundled database:

- **13,939 skills/knowledge concepts**
- **640 skill groups**
- **3,039 occupations**
- **619 ISCO groups**
- **18,237 total ESCO concepts**
- **135,895 persisted labels**
- **126,051 occupation-skill relationships**
  - 67,600 essential
  - 58,451 optional
- **151,766 total ESCO relationships** after direct hierarchy/ISCO links are included

ESCO hidden labels are preserved as trusted identity labels.

## What matching uses now

| Dimension | Phase 2.3 behaviour |
| --- | --- |
| Skills / knowledge | Exact profile skill, trusted ESCO alias identity, or one direct narrower → broader relation |
| Target-role preferences | OFO/ESCO identity labels, OFO specialisation as `RELATED`, or one direct occupation hierarchy edge |
| Contextual experience | Dated work-title evidence may support one direct trusted occupation relationship after Phase 2.2 duration logic permits semantic enrichment |
| Qualification | Phase 2.2 only |
| NQF | Deterministic numeric comparison |
| Location | Deterministic Phase 2.2 |
| Work arrangement | Deterministic Phase 2.2 |
| Opportunity kind | Deterministic Phase 2.2 |
| Eligibility | `UNKNOWN` unless explicit structured profile evidence exists |

## Evidence states

```text
FOUND
  Direct structured evidence or trusted canonical identity.

RELATED
  Direct trusted support that is meaningful but not proof.
  It does not add numeric score in Phase 2.3.

NOT_FOUND_ON_PROFILE
  Safely comparable requirement is not supported by profile evidence.

UNKNOWN
  CareerGPS cannot compare safely.
```

## Skills

```text
Profile: MS Excel
Requirement: Microsoft Excel
trusted ESCO labels identify one concept
→ FOUND

Profile: Python
Requirement: Programming
Python --direct broader--> Programming
→ RELATED

Profile: Python
Requirement: Technical skills
Python -> Programming -> Technical skills
(two hops)
→ NOT promoted by Phase 2.3

Profile: Programming
Requirement: Python
→ broader evidence does not prove a specific skill
```

Only the user's structured **Skills** records count as candidate skill evidence. ESCO occupation-skill links are stored as knowledge, but an occupation title does not fabricate skills on the user's profile.

## Target-role preferences

```text
Preference: official alias of Software Developer
Opportunity: Software Developer
→ FOUND

Preference: OFO specialisation label
Opportunity: canonical OFO occupation
→ RELATED

Preference and opportunity merely share ISCO group 2512
→ no matching promotion
```

ISCO is therefore a bridge for finding mapping candidates, not an equality/relatedness rule.

## Contextual experience

Phase 2.2 duration truth remains first.

```text
Requirement: 3 years in a similar role
Profile: 2 total dated years
→ NOT_FOUND_ON_PROFILE
```

Taxonomy cannot rescue it.

When duration is otherwise sufficient, one direct trusted occupation relation can support `RELATED` using only dated work records whose titles actually resolve to that relationship. Unrelated years are never borrowed.

## ESCO occupation-skill relationships

CareerGPS now persists ESCO's direct `essential` and `optional` occupation-skill assertions. They are useful structured knowledge for explanations and later calibrated matching, but Phase 2.3 intentionally does **not** infer:

```text
"User worked as Software Developer"
therefore
"User has every essential Software Developer skill"
```

That would convert occupational context into unsupported candidate evidence.

## Runtime and performance

Matching never calls ESCO or DHET over the network. Taxonomies are preloaded locally.

For a match run CareerGPS:

1. Collects the skill/occupation terms needed for the run.
2. Resolves exact labels in bulk.
3. Loads only one direct broader relationship neighbourhood.
4. Reuses the same in-memory context across opportunities.

There are no taxonomy queries per opportunity.

## Bundled database state

The previous bundled database contained only:

```text
OFO 2021: 97 concepts / 97 labels
```

That bad release has been replaced. Current state:

```text
ESCO 1.2.1 [ACTIVE] concepts=18237 labels=135895 relations=151766
OFO 2021  [ACTIVE] concepts=1511  labels=7659   relations=0
```

Non-taxonomy table row counts were verified unchanged during replacement.

## Commands

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py taxonomy_status
```

The bundled SQLite database is already populated. Re-import only if refreshing the taxonomy release:

```powershell
.\.venv\Scripts\python.exe manage.py import_ofo_taxonomy "C:\path\to\OFO.xls" --taxonomy-version 2021
.\.venv\Scripts\python.exe manage.py import_esco_taxonomy "C:\path\to\esco-csv-folder" --taxonomy-version 1.2.1
```

## Safe fallback

If taxonomy data is missing, ambiguous, unsupported, or outside the admitted direct relationship rules, CareerGPS abstains and preserves Phase 2.2 behaviour.
