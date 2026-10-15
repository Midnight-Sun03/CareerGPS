"""Import trusted ESCO CSV releases into CareerGPS's local taxonomy cache.

No matching request calls ESCO over the network. The matcher reads only records
that have already been imported into the CareerGPS database.
"""
from __future__ import annotations

import csv
from pathlib import Path

from django.db import transaction

from core.models import TaxonomyConcept, TaxonomyLabel, TaxonomyRelation, TaxonomyRelease
from .normalization import normalize_term, split_multivalue_labels

ESCO_SOURCE_URL = "https://esco.ec.europa.eu/en/use-esco/download"
ESCO_1_2_1_EXPECTED = {
    "occupation": 3039,
    "skill": 13939,
    "occupation_group": 619,
    "skill_group": 640,
    "occupation_skill_relations": 126051,
}


def _rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _existing_path(directory: Path, filename: str, *, required: bool = False):
    candidates = [filename]
    if "_en." in filename:
        candidates.append(filename.replace("_en.", "."))
    for candidate in candidates:
        path = directory / candidate
        if path.exists():
            return path
    wanted = {candidate.casefold() for candidate in candidates}
    for path in directory.iterdir() if directory.exists() else ():
        if path.is_file() and path.name.casefold() in wanted:
            return path
    if required:
        raise FileNotFoundError(f"Required ESCO file not found in {directory}: {filename}")
    return None


def _validate_csv_headers(path: Path, required_columns, *, label: str):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        headers = next(reader, None)
    if headers is None:
        raise ValueError(f"ESCO {label} file is empty: {path.name}")
    available = {str(header or "").strip() for header in headers}
    missing = [column for column in required_columns if column not in available]
    if missing:
        raise ValueError(
            f"ESCO {label} file {path.name} is missing required column(s): " + ", ".join(missing)
        )


def _clean(value):
    return " ".join(str(value or "").split())


def _dedupe_rows_by_uri(path: Path):
    """Return one canonical row per concept URI plus the union of source labels.

    ESCO 1.2.1 contains a small number of repeated concept URIs with different
    modifiedDate values. Identity is the URI, not the CSV row. We retain the
    newest row and union labels from all representations of that URI.
    """
    payloads = {}
    for row in _rows(path):
        uri = _clean(row.get("conceptUri"))
        label = _clean(row.get("preferredLabel"))
        if not uri or not label:
            continue
        item = payloads.setdefault(uri, {
            "row": row,
            "alternative": [],
            "hidden": [],
        })
        current_date = _clean((item["row"] or {}).get("modifiedDate"))
        candidate_date = _clean(row.get("modifiedDate"))
        if candidate_date >= current_date:
            item["row"] = row
        item["alternative"].extend(split_multivalue_labels(row.get("altLabels")))
        item["hidden"].extend(split_multivalue_labels(row.get("hiddenLabels")))
    return payloads


def _concept_from_payload(external_id, payload, *, release, kind):
    row = payload["row"]
    label = _clean(row.get("preferredLabel"))
    code = _clean(row.get("code") or row.get("iscoGroup"))
    return TaxonomyConcept(
        release=release,
        external_id=external_id,
        kind=kind,
        code=code,
        preferred_label=label,
        normalized_label=normalize_term(label),
        definition=_clean(row.get("description") or row.get("definition")),
        metadata={
            key: row.get(key)
            for key in (
                "conceptType", "skillType", "reuseLevel", "iscoGroup",
                "status", "modifiedDate", "naceCode",
            )
            if row.get(key) not in (None, "")
        },
    )


def _load_concepts(path, release, kind):
    payloads = _dedupe_rows_by_uri(path)
    concepts = [
        _concept_from_payload(uri, payload, release=release, kind=kind)
        for uri, payload in payloads.items()
    ]
    return concepts, payloads


def _deduped_labels(preferred, alternatives, hidden):
    seen = set()
    for label, label_type in (
        [(preferred, "preferred")]
        + [(value, "alternative") for value in alternatives]
        + [(value, "hidden") for value in hidden]
    ):
        normalized = normalize_term(label)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        yield label, normalized, label_type


def _create_labels(release, label_payloads):
    concept_by_external = {
        concept.external_id: concept
        for concept in TaxonomyConcept.objects.filter(release=release).only("id", "external_id", "preferred_label")
    }
    objects = []
    for external_id, payload in label_payloads.items():
        concept = concept_by_external.get(external_id)
        if not concept:
            continue
        for label, normalized, label_type in _deduped_labels(
            concept.preferred_label,
            payload["alternative"],
            payload["hidden"],
        ):
            objects.append(TaxonomyLabel(
                concept=concept,
                label=label,
                normalized_label=normalized,
                label_type=label_type,
            ))
    TaxonomyLabel.objects.bulk_create(objects, batch_size=2000, ignore_conflicts=True)
    return concept_by_external


def _create_broader_relations(path, concept_by_external, relations):
    for row in _rows(path):
        subject = concept_by_external.get(_clean(row.get("conceptUri")))
        broader = concept_by_external.get(_clean(row.get("broaderUri")))
        if subject and broader and subject.pk != broader.pk:
            relations.append(TaxonomyRelation(
                subject=subject,
                relation_type="broader",
                object=broader,
                metadata={"relationship": "source_broader"},
            ))


def _create_occupation_skill_relations(path, concept_by_external, relations):
    count = 0
    for row in _rows(path):
        occupation = concept_by_external.get(_clean(row.get("occupationUri")))
        skill = concept_by_external.get(_clean(row.get("skillUri")))
        relation_type = normalize_term(row.get("relationType")).replace(" ", "_")
        if relation_type not in {"essential", "optional"}:
            continue
        if not occupation or not skill:
            raise ValueError("ESCO occupation-skill relation references a concept not present in the imported bundle.")
        relations.append(TaxonomyRelation(
            subject=occupation,
            relation_type=relation_type,
            object=skill,
            metadata={
                "relationship": "occupation_skill",
                "skill_type": _clean(row.get("skillType")),
            },
        ))
        count += 1
    return count


def _audit_expected(version, counts):
    if str(version) != "1.2.1":
        return
    for key, expected in ESCO_1_2_1_EXPECTED.items():
        if key not in counts:
            continue
        actual = counts[key]
        if actual != expected:
            raise ValueError(f"ESCO 1.2.1 audit failed for {key}: expected {expected}, found {actual}.")


def import_esco_csv_bundle(directory, *, version, activate=True, replace=True):
    """Import an audited English ESCO CSV classification bundle."""
    directory = Path(directory)
    skills_path = _existing_path(directory, "skills_en.csv", required=True)
    skill_groups_path = _existing_path(directory, "skillGroups_en.csv", required=True)
    occupations_path = _existing_path(directory, "occupations_en.csv", required=True)
    isco_groups_path = _existing_path(directory, "ISCOGroups_en.csv", required=True)
    skill_broader_path = _existing_path(directory, "broaderRelationsSkillPillar_en.csv", required=True)
    occupation_broader_path = _existing_path(directory, "broaderRelationsOccPillar_en.csv", required=True)
    occupation_skill_path = _existing_path(directory, "occupationSkillRelations_en.csv", required=True)

    for path, columns, label in (
        (skills_path, ("conceptUri", "preferredLabel"), "skills"),
        (skill_groups_path, ("conceptUri", "preferredLabel"), "skill groups"),
        (occupations_path, ("conceptUri", "preferredLabel", "iscoGroup"), "occupations"),
        (isco_groups_path, ("conceptUri", "preferredLabel", "code"), "ISCO groups"),
        (skill_broader_path, ("conceptUri", "broaderUri"), "skill broader relations"),
        (occupation_broader_path, ("conceptUri", "broaderUri"), "occupation broader relations"),
        (occupation_skill_path, ("occupationUri", "relationType", "skillUri"), "occupation-skill relations"),
    ):
        _validate_csv_headers(path, columns, label=label)

    with transaction.atomic():
        existing = TaxonomyRelease.objects.filter(source="esco", version=version).first()
        if existing and not replace and existing.concepts.exists():
            if activate and not existing.is_active:
                TaxonomyRelease.objects.filter(source="esco", is_active=True).exclude(pk=existing.pk).update(is_active=False)
                existing.is_active = True
                existing.save(update_fields=["is_active"])
            return existing
        if replace:
            TaxonomyRelease.objects.filter(source="esco", version=version).delete()
        release, _ = TaxonomyRelease.objects.get_or_create(
            source="esco", version=version, defaults={"source_url": ESCO_SOURCE_URL}
        )

        all_concepts = []
        all_labels = {}
        counts = {}
        for path, kind in (
            (skills_path, "skill"),
            (skill_groups_path, "skill_group"),
            (occupations_path, "occupation"),
            (isco_groups_path, "occupation_group"),
        ):
            concepts, payloads = _load_concepts(path, release, kind)
            counts[kind] = len(concepts)
            all_concepts.extend(concepts)
            all_labels.update(payloads)

        missing_kinds = [kind for kind in ("skill", "skill_group", "occupation", "occupation_group") if not counts.get(kind)]
        if missing_kinds:
            raise ValueError("ESCO bundle contained no valid concepts for: " + ", ".join(missing_kinds))
        _audit_expected(version, counts)
        TaxonomyConcept.objects.bulk_create(all_concepts, batch_size=1000)
        concept_by_external = _create_labels(release, all_labels)
        if len(concept_by_external) != len(all_concepts):
            raise ValueError(
                f"ESCO persistence audit failed: parsed {len(all_concepts)} concepts but persisted {len(concept_by_external)}."
            )

        relations = []
        _create_broader_relations(skill_broader_path, concept_by_external, relations)
        _create_broader_relations(occupation_broader_path, concept_by_external, relations)

        # ESCO's occupation file explicitly states each parent ISCO group. Store
        # it as a direct source relationship; do not infer cross-taxonomy identity.
        isco_groups = {
            concept.code: concept
            for concept in concept_by_external.values()
            if concept.kind == "occupation_group" and concept.code
        }
        for concept in concept_by_external.values():
            if concept.kind != "occupation":
                continue
            isco_code = _clean((concept.metadata or {}).get("iscoGroup"))
            parent = isco_groups.get(isco_code)
            if parent:
                relations.append(TaxonomyRelation(
                    subject=concept,
                    relation_type="broader",
                    object=parent,
                    metadata={"relationship": "isco_group"},
                ))

        occupation_skill_count = _create_occupation_skill_relations(
            occupation_skill_path, concept_by_external, relations
        )
        counts["occupation_skill_relations"] = occupation_skill_count
        _audit_expected(version, counts)

        # The same semantic edge may be asserted both by the ESCO source
        # hierarchy and its ISCO group mapping. Merge provenance before the
        # unique edge is inserted so ignore_conflicts cannot discard a source.
        merged_relations = {}
        for relation in relations:
            key = (relation.subject_id, relation.relation_type, relation.object_id)
            relationship = relation.metadata.get('relationship')
            if key not in merged_relations:
                relation.metadata = {
                    **relation.metadata,
                    'provenance_types': [relationship] if relationship else [],
                }
                merged_relations[key] = relation
            elif relationship:
                values = merged_relations[key].metadata.setdefault('provenance_types', [])
                if relationship not in values:
                    values.append(relationship)
        TaxonomyRelation.objects.bulk_create(
            merged_relations.values(), batch_size=2000, ignore_conflicts=True)

        release.notes = "ESCO audited import: " + ", ".join(
            f"{key}={value}" for key, value in sorted({
                **counts,
                "persisted_concepts": TaxonomyConcept.objects.filter(release=release).count(),
                "persisted_labels": TaxonomyLabel.objects.filter(concept__release=release).count(),
                "persisted_relations": TaxonomyRelation.objects.filter(subject__release=release).count(),
            }.items())
        )
        release.save(update_fields=["notes"])

        if activate:
            TaxonomyRelease.objects.filter(source="esco", is_active=True).exclude(pk=release.pk).update(is_active=False)
            release.is_active = True
            release.save(update_fields=["is_active", "imported_at"])

    return release
