"""Import South Africa's OFO occupations into the local trusted taxonomy cache."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from django.db import transaction

from core.models import TaxonomyConcept, TaxonomyLabel, TaxonomyRelation, TaxonomyRelease
from .normalization import normalize_term, split_multivalue_labels

OFO_2021_OFFICIAL_URL = (
    "https://www.dhet.gov.za/Skills%20Development/Updated%20FINAL%20OFO%20VERSION%202021.xls"
)
EXPECTED_OFO_2021_OCCUPATIONS = 1511


def _clean_code(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = " ".join(str(value or "").split())
    text = re.sub(r"\.0$", "", text)
    match = re.search(r"(?:20\d{2}[- /])?(\d{6})$", text)
    return match.group(1) if match else ""


def _clean_group_code(value):
    text = _clean_text(value)
    text = re.sub(r"\.0$", "", text)
    match = re.search(r"(\d{1,4})$", text)
    return match.group(1) if match else ""


def _clean_text(value):
    return " ".join(str(value or "").split())


def _truthy_marker(value):
    text = normalize_term(value)
    return bool(text and text not in {"no", "n", "false", "0"})


def _iter_csv_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def _find_header_row(sheet, required):
    required = {normalize_term(item) for item in required}
    for row_index in range(min(sheet.nrows, 30)):
        headers = [_clean_text(sheet.cell_value(row_index, col)) for col in range(sheet.ncols)]
        normalized = {normalize_term(value) for value in headers if value}
        if required.issubset(normalized):
            return row_index, headers
    return None, None


def _iter_sheet_rows(book, sheet_name, required_headers):
    try:
        sheet = book.sheet_by_name(sheet_name)
    except Exception as exc:
        raise ValueError(f"Official OFO workbook is missing required sheet: {sheet_name}") from exc
    header_index, headers = _find_header_row(sheet, required_headers)
    if header_index is None:
        raise ValueError(
            f"OFO sheet {sheet_name!r} does not contain the expected headers: "
            + ", ".join(required_headers)
        )
    for row_index in range(header_index + 1, sheet.nrows):
        values = [sheet.cell_value(row_index, col) for col in range(sheet.ncols)]
        if not any(_clean_text(value) for value in values):
            continue
        yield {
            headers[index]: values[index]
            for index in range(len(headers))
            if headers[index]
        }


def _normalized_row(row):
    return {normalize_term(key): value for key, value in row.items()}


def _get(row, name):
    return _normalized_row(row).get(normalize_term(name), "")


def _read_official_xls(path, version):
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover - dependency is in project requirements
        raise RuntimeError(
            "Reading the official OFO .xls file requires xlrd. Install project requirements first."
        ) from exc

    try:
        workbook_bytes = Path(path).read_bytes()
        book = xlrd.open_workbook(file_contents=workbook_bytes, on_demand=True)
    except Exception as exc:
        raise ValueError(
            f"Could not read the OFO workbook {Path(path).name}. Confirm that it is the official Excel .xls file."
        ) from exc

    try:
        payloads = {}
        for row in _iter_sheet_rows(book, "Occupations", ("OFO Code", "Description")):
            code = _clean_code(_get(row, "OFO Code"))
            occupation = _clean_text(_get(row, "Description"))
            if not code or not occupation:
                continue
            if code in payloads and normalize_term(payloads[code]["occupation"]) != normalize_term(occupation):
                raise ValueError(f"OFO code {code} has conflicting canonical occupation titles.")
            payloads[code] = {
                "occupation": occupation,
                "alternative": [],
                "specialisation": [],
                "metadata": {
                    "ofo_year": version,
                    "isco_group": code[:4],
                    "isco_bridge_strength": "unit_group_only",
                    "trade": _truthy_marker(_get(row, "Trade")),
                    "green_occupation": _truthy_marker(_get(row, "Green Occupation")),
                    "green_skill": _truthy_marker(_get(row, "Green Skill")),
                },
            }

        if str(version) == "2021" and len(payloads) != EXPECTED_OFO_2021_OCCUPATIONS:
            raise ValueError(
                f"OFO 2021 audit failed: expected {EXPECTED_OFO_2021_OCCUPATIONS} canonical occupations, found {len(payloads)}."
            )

        specialisation_rows = 0
        for row in _iter_sheet_rows(book, "Specialisations", ("OFO Code", "Description")):
            code = _clean_code(_get(row, "OFO Code"))
            label = _clean_text(_get(row, "Description"))
            if code in payloads and label:
                payloads[code]["specialisation"].append(label)
                specialisation_rows += 1

        alternate_rows = 0
        rejected_alternate_rows = 0
        for row in _iter_sheet_rows(book, "Alternate Title", ("OFO Code", "Description")):
            code = _clean_code(_get(row, "OFO Code"))
            label = _clean_text(_get(row, "Description"))
            if not code or not label:
                continue
            if code not in payloads:
                rejected_alternate_rows += 1
                continue
            payloads[code]["alternative"].append(label)
            alternate_rows += 1

        groups = {}
        for level, sheet_name in enumerate(
            ('Major Groups', 'Sub Major Groups', 'Minor Groups', 'Unit Groups'), start=1
        ):
            for row in _iter_sheet_rows(book, sheet_name, ('OFO Code', 'Description')):
                code = _clean_group_code(_get(row, 'OFO Code'))
                label = _clean_text(_get(row, 'Description'))
                if code and label:
                    groups[code] = {'label': label, 'level': level}

        return payloads, {
            "canonical_occupations": len(payloads),
            "hierarchy_groups": len(groups),
            "specialisation_rows": specialisation_rows,
            "alternate_title_rows": alternate_rows,
            "rejected_alternate_title_rows": rejected_alternate_rows,
        }, groups
    finally:
        book.release_resources()


def _read_csv_export(path, version):
    """Conservative fallback for a flat CSV export.

    A CSV cannot represent all official workbook sheets unless the exporter has
    already flattened them, so this path accepts canonical occupation rows and
    any explicit alias columns present in that file only.
    """
    payloads = {}
    for row in _iter_csv_rows(path):
        normalized = _normalized_row(row)
        code = _clean_code(
            normalized.get("ofo code") or normalized.get("ofo_code") or normalized.get("code")
        )
        occupation = _clean_text(
            normalized.get("occupation")
            or normalized.get("occupation title")
            or normalized.get("ofo occupation")
            or normalized.get("description")
        )
        if not code or not occupation:
            continue
        payload = payloads.setdefault(code, {
            "occupation": occupation,
            "alternative": [],
            "specialisation": [],
            "metadata": {
                "ofo_year": version,
                "isco_group": code[:4],
                "isco_bridge_strength": "unit_group_only",
            },
        })
        for column in (
            "alternative title", "alternative titles",
        ):
            for label in split_multivalue_labels(normalized.get(column)):
                payload["alternative"].append(label)
        for column in (
            "specialisation/alternative title", "specialisation / alternative title",
            "specialisation", "specialisations",
        ):
            for label in split_multivalue_labels(normalized.get(column)):
                payload["specialisation"].append(label)

    if not payloads:
        raise ValueError("No valid six-digit OFO occupation rows were found in the supplied CSV.")
    if str(version) == "2021" and len(payloads) != EXPECTED_OFO_2021_OCCUPATIONS:
        raise ValueError(
            f"OFO 2021 audit failed: expected {EXPECTED_OFO_2021_OCCUPATIONS} canonical occupations, found {len(payloads)}."
        )
    return payloads, {"canonical_occupations": len(payloads)}, {}


def _read_payloads(path, version):
    suffix = Path(path).suffix.casefold()
    if suffix == ".xls":
        return _read_official_xls(path, version)
    if suffix == ".csv":
        return _read_csv_export(path, version)
    raise ValueError("OFO import supports the official .xls file or a CSV export.")


def _dedupe_labels(payload):
    """Deduplicate within each official label type while preserving provenance."""
    result = []
    preferred = payload["occupation"]
    preferred_norm = normalize_term(preferred)
    if preferred_norm:
        result.append((preferred, preferred_norm, "preferred"))
    for label_type in ("alternative", "specialisation"):
        seen = set()
        for label in payload[label_type]:
            normalized = normalize_term(label)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append((label, normalized, label_type))
    return result


def import_ofo_file(path, *, version="2021", activate=True, replace=True, source_url=OFO_2021_OFFICIAL_URL):
    """Import audited OFO concepts and labels from the official workbook."""
    payloads, audit, groups = _read_payloads(path, version)

    with transaction.atomic():
        existing = TaxonomyRelease.objects.filter(source="ofo", version=version).first()
        if existing and not replace and existing.concepts.exists():
            if activate and not existing.is_active:
                TaxonomyRelease.objects.filter(source="ofo", is_active=True).exclude(pk=existing.pk).update(is_active=False)
                existing.is_active = True
                existing.save(update_fields=["is_active"])
            return existing
        if replace:
            TaxonomyRelease.objects.filter(source="ofo", version=version).delete()
        release, _ = TaxonomyRelease.objects.get_or_create(
            source="ofo",
            version=version,
            defaults={"source_url": source_url},
        )

        concepts = [
            TaxonomyConcept(
                release=release,
                external_id=f"ofo:{version}:{code}",
                kind="occupation",
                code=code,
                preferred_label=payload["occupation"],
                normalized_label=normalize_term(payload["occupation"]),
                metadata=payload["metadata"],
            )
            for code, payload in payloads.items()
        ] + [
            TaxonomyConcept(
                release=release,
                external_id=f"ofo:{version}:group:{code}",
                kind="occupation_group",
                code=code,
                preferred_label=payload['label'],
                normalized_label=normalize_term(payload['label']),
                metadata={'ofo_year': version, 'level': payload['level']},
            ) for code, payload in groups.items()
        ]
        TaxonomyConcept.objects.bulk_create(concepts, batch_size=1000)
        concept_by_code = {
            concept.code: concept
            for concept in TaxonomyConcept.objects.filter(release=release).only("id", "code")
        }
        persisted_occupations = sum(
            concept.kind == 'occupation' for concept in TaxonomyConcept.objects.filter(release=release))
        if persisted_occupations != len(payloads):
            raise ValueError(
                f"OFO persistence audit failed: parsed {len(payloads)} occupations but persisted {persisted_occupations}."
            )

        label_objects = []
        for code, payload in payloads.items():
            concept = concept_by_code[code]
            for label, normalized, label_type in _dedupe_labels(payload):
                label_objects.append(TaxonomyLabel(
                    concept=concept,
                    label=label,
                    normalized_label=normalized,
                    label_type=label_type,
                ))
        for code, payload in groups.items():
            concept = concept_by_code[code]
            label_objects.append(TaxonomyLabel(
                concept=concept, label=payload['label'],
                normalized_label=normalize_term(payload['label']),
                label_type='preferred'))
        TaxonomyLabel.objects.bulk_create(label_objects, batch_size=2000, ignore_conflicts=True)

        relations = []
        for code, payload in groups.items():
            if payload['level'] == 1:
                continue
            parent = concept_by_code.get(code[:-1])
            if parent:
                relations.append(TaxonomyRelation(
                    subject=concept_by_code[code], object=parent,
                    relation_type='broader',
                    metadata={'relationship': 'ofo_hierarchy', 'release': version}))
        for code in payloads:
            parent = concept_by_code.get(code[:4])
            if parent:
                relations.append(TaxonomyRelation(
                    subject=concept_by_code[code], object=parent,
                    relation_type='broader',
                    metadata={'relationship': 'ofo_hierarchy', 'release': version}))
        TaxonomyRelation.objects.bulk_create(relations, batch_size=2000, ignore_conflicts=True)

        audit.update({
            "persisted_concepts": len(concept_by_code),
            "persisted_hierarchy_relations": len(relations),
            "persisted_labels": TaxonomyLabel.objects.filter(concept__release=release).count(),
        })
        release.notes = "OFO audited import: " + ", ".join(f"{key}={value}" for key, value in sorted(audit.items()))
        release.save(update_fields=["notes"])

        if activate:
            TaxonomyRelease.objects.filter(source="ofo", is_active=True).exclude(pk=release.pk).update(is_active=False)
            release.is_active = True
            release.save(update_fields=["is_active", "imported_at"])

    return release
