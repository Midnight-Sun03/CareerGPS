# CareerGPS Phase 2.3 handoff — audited build

This build contains the corrected no-AI Phase 2.3 taxonomy-matching foundation and a database already populated with audited OFO 2021 and ESCO 1.2.1 releases.

## Corrected after real-data audit

- Active Django package remains `CareerGPS_Rebuild`.
- The old OFO parser bug is removed. It now reads the official `Occupations`, `Specialisations`, and `Alternate Title` sheets explicitly.
- OFO 2021 import is fail-closed at **1,511 canonical occupations**.
- OFO specialisations are supporting `RELATED` labels; official alternate titles can establish identity.
- Invalid/non-canonical OFO alias rows are rejected.
- ESCO concepts are deduplicated by canonical `conceptUri`, not CSV row count.
- ESCO 1.2.1 is fail-closed against audited concept and occupation-skill relationship counts.
- ESCO `hiddenLabels` are preserved.
- ESCO `occupationSkillRelations_en.csv` is imported as `essential`/`optional` trusted knowledge.
- Matching uses only **direct one-hop** broader relationships.
- Same ISCO unit group is not `FOUND` or `RELATED`; it is mapping-candidate metadata only.
- `RELATED` remains visible/explainable but is **score-neutral** until Phase 2.5 calibration.
- Hard Phase 2.2 failures cannot be rescued by taxonomy relationships.
- Work titles do not become skills, and occupation-skill knowledge does not fabricate candidate skills.

## Current taxonomy state in bundled `db.sqlite3`

```text
ESCO 1.2.1 [ACTIVE] concepts=18237 labels=135895 relations=151766
OFO 2021  [ACTIVE] concepts=1511  labels=7659   relations=0
```

The previous bad OFO release contained only 97 concepts. It has been replaced. Non-taxonomy table counts were checked before/after and did not change.

## Verification completed here

```text
manage.py check                         PASS
makemigrations --check --dry-run       PASS
core tests                             97/97 PASS
taxonomy import + matching tests       31/31 PASS
real OFO 2021 import                   PASS
real ESCO 1.2.1 import                 PASS
```

The full project suite currently discovers **173 tests**. In this Linux sandbox the uploaded `.venv` is a Windows virtual environment, so its compiled Windows `lxml` binary cannot be loaded by Linux; ingestion tests that require BeautifulSoup's `lxml` parser therefore cannot be meaningfully executed here. This is an environment limitation, not a taxonomy failure. Run the full suite in the project's Windows environment.

## Windows final verification

From the `CareerGPS-changes` folder:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py taxonomy_status
```

Expected taxonomy status:

```text
ESCO 1.2.1 [ACTIVE] concepts=18237 labels=135895 relations=151766
OFO 2021 [ACTIVE] concepts=1511 labels=7659 relations=0
```

## Refreshing taxonomy data later

OFO:

```powershell
.\.venv\Scripts\python.exe manage.py import_ofo_taxonomy "C:\path\to\OFO.xls" --taxonomy-version 2021
```

ESCO:

```powershell
.\.venv\Scripts\python.exe manage.py import_esco_taxonomy "C:\path\to\extracted-esco-folder" --taxonomy-version 1.2.1
```

## Safe fallback

If a term cannot be resolved safely, an active taxonomy is absent, or only a multi-hop/ISCO-family connection exists, Phase 2.3 does not guess. CareerGPS keeps the original Phase 2.2 result.
