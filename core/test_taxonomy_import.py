import csv
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from .models import TaxonomyConcept, TaxonomyLabel, TaxonomyRelation, TaxonomyRelease
from .taxonomies.esco import import_esco_csv_bundle
from .taxonomies.ofo import _clean_code, import_ofo_file
from .taxonomies.resolver import build_taxonomy_context


class TaxonomyImportTests(TestCase):
    def write_csv(self, directory, name, fieldnames, rows):
        path = Path(directory) / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def write_minimal_esco_bundle(
        self,
        directory,
        *,
        skills=None,
        skill_groups=None,
        occupations=None,
        isco_groups=None,
        skill_relations=None,
        occupation_relations=None,
        occupation_skill_relations=None,
    ):
        self.write_csv(
            directory,
            "skills_en.csv",
            ["conceptUri", "preferredLabel", "altLabels", "description", "skillType"],
            skills if skills is not None else [
                {
                    "conceptUri": "skill:x",
                    "preferredLabel": "Skill X",
                    "altLabels": "",
                    "description": "",
                    "skillType": "skill/competence",
                }
            ],
        )
        self.write_csv(
            directory,
            "skillGroups_en.csv",
            ["conceptUri", "preferredLabel", "altLabels", "code"],
            skill_groups if skill_groups is not None else [
                {
                    "conceptUri": "group:x",
                    "preferredLabel": "Skill Group X",
                    "altLabels": "",
                    "code": "S1",
                }
            ],
        )
        self.write_csv(
            directory,
            "occupations_en.csv",
            ["conceptUri", "preferredLabel", "altLabels", "iscoGroup", "code"],
            occupations if occupations is not None else [
                {
                    "conceptUri": "occ:x",
                    "preferredLabel": "Occupation X",
                    "altLabels": "",
                    "iscoGroup": "2512",
                    "code": "",
                }
            ],
        )
        self.write_csv(
            directory,
            "ISCOGroups_en.csv",
            ["conceptUri", "preferredLabel", "altLabels", "code"],
            isco_groups if isco_groups is not None else [
                {
                    "conceptUri": "isco:2512",
                    "preferredLabel": "Software developers",
                    "altLabels": "",
                    "code": "2512",
                }
            ],
        )
        self.write_csv(
            directory,
            "broaderRelationsSkillPillar_en.csv",
            ["conceptUri", "broaderUri"],
            skill_relations if skill_relations is not None else [],
        )
        self.write_csv(
            directory,
            "broaderRelationsOccPillar_en.csv",
            ["conceptUri", "broaderUri"],
            occupation_relations if occupation_relations is not None else [],
        )
        self.write_csv(
            directory,
            "occupationSkillRelations_en.csv",
            ["occupationUri", "occupationLabel", "relationType", "skillType", "skillUri", "skillLabel"],
            occupation_skill_relations if occupation_skill_relations is not None else [],
        )

    def test_esco_bundle_imports_aliases_and_trusted_hierarchy(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_minimal_esco_bundle(
                directory,
                skills=[{
                    "conceptUri": "skill:python",
                    "preferredLabel": "Python",
                    "altLabels": "Python programming language;Python language",
                    "description": "Python knowledge",
                    "skillType": "knowledge",
                }],
                skill_groups=[{
                    "conceptUri": "group:programming",
                    "preferredLabel": "Programming",
                    "altLabels": "Computer programming",
                    "code": "S1",
                }],
                occupations=[{
                    "conceptUri": "occ:developer",
                    "preferredLabel": "Software Developer",
                    "altLabels": "Software Engineer",
                    "iscoGroup": "2512",
                    "code": "",
                }],
                skill_relations=[{
                    "conceptUri": "skill:python",
                    "broaderUri": "group:programming",
                }],
                occupation_skill_relations=[{
                    "occupationUri": "occ:developer",
                    "occupationLabel": "Software Developer",
                    "relationType": "essential",
                    "skillType": "knowledge",
                    "skillUri": "skill:python",
                    "skillLabel": "Python",
                }],
            )
            release = import_esco_csv_bundle(directory, version="test-1")

        self.assertTrue(release.is_active)
        self.assertEqual(TaxonomyConcept.objects.filter(release=release).count(), 4)
        self.assertTrue(
            TaxonomyLabel.objects.filter(
                concept__release=release,
                normalized_label="software engineer",
            ).exists()
        )
        python = TaxonomyConcept.objects.get(release=release, external_id="skill:python")
        programming = TaxonomyConcept.objects.get(
            release=release, external_id="group:programming"
        )
        developer = TaxonomyConcept.objects.get(
            release=release, external_id="occ:developer"
        )
        isco = TaxonomyConcept.objects.get(release=release, external_id="isco:2512")
        self.assertTrue(
            TaxonomyRelation.objects.filter(
                subject=python, relation_type="broader", object=programming
            ).exists()
        )
        self.assertTrue(
            TaxonomyRelation.objects.filter(
                subject=developer, relation_type="broader", object=isco
            ).exists()
        )
        self.assertTrue(
            TaxonomyRelation.objects.filter(
                subject=developer, relation_type="essential", object=python
            ).exists()
        )

    def test_new_active_release_deactivates_previous_release_of_same_source(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_minimal_esco_bundle(directory)
            first = import_esco_csv_bundle(directory, version="v1")
            second = import_esco_csv_bundle(directory, version="v2")

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)

    def test_esco_import_fails_closed_when_required_relationship_file_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_minimal_esco_bundle(directory)
            (Path(directory) / "broaderRelationsSkillPillar_en.csv").unlink()
            with self.assertRaises(FileNotFoundError):
                import_esco_csv_bundle(directory, version="incomplete")

        self.assertFalse(TaxonomyRelease.objects.filter(version="incomplete").exists())

    def test_esco_import_fails_closed_when_relationship_schema_is_wrong(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_minimal_esco_bundle(directory)
            self.write_csv(
                directory,
                "broaderRelationsSkillPillar_en.csv",
                ["child", "parent"],
                [{"child": "skill:x", "parent": "group:x"}],
            )
            with self.assertRaises(ValueError):
                import_esco_csv_bundle(directory, version="bad-schema")

        self.assertFalse(TaxonomyRelease.objects.filter(version="bad-schema").exists())

    def test_esco_import_fails_closed_when_required_concept_file_has_no_valid_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_minimal_esco_bundle(directory, skills=[])
            with self.assertRaises(ValueError):
                import_esco_csv_bundle(directory, version="empty-skills")

        self.assertFalse(TaxonomyRelease.objects.filter(version="empty-skills").exists())

    def test_ofo_csv_imports_occupation_and_specialisation_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_csv(
                directory,
                "ofo.csv",
                ["OFO Code", "Occupation", "Specialisation/Alternative Title"],
                [{
                    "OFO Code": "2021-251201",
                    "Occupation": "Software Developer",
                    "Specialisation/Alternative Title": (
                        "Software Engineer;Applications Developer"
                    ),
                }],
            )
            release = import_ofo_file(path, version="test-2021")

        concept = TaxonomyConcept.objects.get(release=release)
        self.assertEqual(concept.code, "251201")
        self.assertEqual(concept.metadata["isco_group"], "2512")
        labels = set(
            TaxonomyLabel.objects.filter(concept=concept).values_list("label", flat=True)
        )
        self.assertEqual(
            labels,
            {"Software Developer", "Software Engineer", "Applications Developer"},
        )

    def test_ofo_numeric_excel_style_code_is_cleaned_safely(self):
        self.assertEqual(_clean_code(251201.0), "251201")
        self.assertEqual(_clean_code("2021-251201"), "251201")
        self.assertEqual(_clean_code("2512"), "")
        self.assertEqual(_clean_code("25120"), "")
        self.assertEqual(_clean_code("not-a-code"), "")

    def test_ofo_import_fails_closed_when_no_six_digit_occupations_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_csv(
                directory,
                "ofo-groups-only.csv",
                ["OFO Code", "Occupation", "Specialisation/Alternative Title"],
                [{
                    "OFO Code": "2512",
                    "Occupation": "Software developers",
                    "Specialisation/Alternative Title": "",
                }],
            )
            with self.assertRaises(ValueError):
                import_ofo_file(path, version="invalid")

        self.assertFalse(TaxonomyRelease.objects.filter(version="invalid").exists())

    def test_keep_existing_esco_release_does_not_duplicate_concepts(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_minimal_esco_bundle(directory)
            first = import_esco_csv_bundle(directory, version="stable")
            second = import_esco_csv_bundle(
                directory, version="stable", replace=False
            )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TaxonomyConcept.objects.filter(release=first).count(), 4)

    def test_imported_esco_aliases_and_hierarchy_are_resolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_minimal_esco_bundle(
                directory,
                skills=[{
                    "conceptUri": "skill:python",
                    "preferredLabel": "Python",
                    "altLabels": "Python language",
                    "description": "",
                    "skillType": "knowledge",
                }],
                skill_groups=[{
                    "conceptUri": "group:programming",
                    "preferredLabel": "Programming",
                    "altLabels": "Computer programming",
                    "code": "S1",
                }],
                skill_relations=[{
                    "conceptUri": "skill:python",
                    "broaderUri": "group:programming",
                }],
            )
            import_esco_csv_bundle(directory, version="test-resolver")

        context = build_taxonomy_context({
            "skill": ("Python language",),
            "skill_group": ("Computer programming",),
        })
        python = context.resolve("Python language", ("skill",))
        programming = context.resolve("Computer programming", ("skill_group",))
        self.assertEqual(len(python), 1)
        self.assertEqual(len(programming), 1)
        self.assertTrue(context.is_ancestor(python[0].id, programming[0].id))

    def test_esco_management_command_accepts_taxonomy_version_option(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_minimal_esco_bundle(directory)
            call_command(
                "import_esco_taxonomy",
                directory,
                taxonomy_version="cli-esco",
                verbosity=0,
            )

        self.assertTrue(
            TaxonomyRelease.objects.filter(
                source="esco", version="cli-esco", is_active=True
            ).exists()
        )

    def test_ofo_management_command_accepts_taxonomy_version_option(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_csv(
                directory,
                "ofo.csv",
                ["OFO Code", "Occupation", "Specialisation/Alternative Title"],
                [{
                    "OFO Code": "2021-251201",
                    "Occupation": "Software Developer",
                    "Specialisation/Alternative Title": "Backend Developer",
                }],
            )
            call_command(
                "import_ofo_taxonomy",
                str(path),
                taxonomy_version="cli-ofo",
                verbosity=0,
            )

        self.assertTrue(
            TaxonomyRelease.objects.filter(
                source="ofo", version="cli-ofo", is_active=True
            ).exists()
        )

