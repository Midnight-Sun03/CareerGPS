from .base import (
    CompanyScraper, evidence_provenance, experience_years, find_section,
    geographic_scope_evidence, heading_sections, labelled_text, minimum_nqf,
    property_value, soup_for, successfactors_fingerprint_material, table_fields,
    title_from_page,
)


class NedbankScraper(CompanyScraper):
    code = "nedbank"
    company = "Nedbank"
    listing_urls = ("https://jobs.nedbank.co.za/search/",)
    detail_path_markers = ("/job/", "/jobs/")
    parser_version = "nedbank-2026-2"
    official_domains = ("jobs.nedbank.co.za",)
    use_detail_page_as_application_gateway = True

    fingerprint_material = staticmethod(successfactors_fingerprint_material)

    def _is_detail_url(self, url):
        return super()._is_detail_url(url) and url.rstrip("/") not in {item.rstrip("/") for item in self.listing_urls}

    @staticmethod
    def _stage_signals(category, leadership_pipeline):
        signals = []
        category_lower = str(category or "").lower()
        if "young professional" in category_lower:
            signals.append({
                "value": "early_career", "source_field": "listing category",
                "confidence": "high", "raw": category,
            })
        elif "experienced professional" in category_lower:
            signals.append({
                "value": "experienced", "source_field": "listing category",
                "confidence": "high", "raw": category,
            })
        pipeline_lower = str(leadership_pipeline or "").lower()
        if any(value in pipeline_lower for value in (
            "manage managers", "manage others", "senior leadership", "executive leadership",
        )):
            signals.append({
                "value": "experienced", "source_field": "Leadership Pipeline",
                "confidence": "high", "raw": leadership_pipeline,
            })
        return signals

    def finalize_record(self, record, listing):
        category = listing.get("source_native_category") or ""
        if category:
            record.fields["source_native_category"] = category
            record.fields.setdefault("evidence_provenance", {})["source_native_category"] = evidence_provenance(
                "listing category", source_text=category
            )
        leadership = record.fields.get("source_native_career_level", "")
        record.fields["source_career_stage_signal"] = self._stage_signals(category, leadership)
        return record

    def parse_detail(self, url, html):
        soup = soup_for(html)
        values, sections = table_fields(soup), heading_sections(soup)
        title = property_value(soup, "title") or title_from_page(soup)
        if title.startswith("Title:"):
            title = title[len("Title:"):].strip()
        location = property_value(soup, "location", "streetAddress") or labelled_text(soup, "Job Location", "Location")
        job_family = find_section(sections, "Job Family")
        career_stream = find_section(sections, "Career Stream")
        leadership_pipeline = find_section(sections, "Leadership Pipeline")
        minimum_requirements = find_section(sections, "Minimum Requirements")
        essential_qualification = " ".join(filter(None, [
            find_section(sections, "Essential Qualifications NQF Level"),
            find_section(sections, "Essential Qualification"),
            find_section(sections, "Essential Qualifications"),
        ]))
        preferred_qualification = " ".join(filter(None, [
            find_section(sections, "Preferred Qualification"),
            find_section(sections, "Preferred Qualifications"),
        ]))
        qualification = " ".join(filter(None, [minimum_requirements, essential_qualification]))
        experience = find_section(sections, "Minimum Experience Level")
        experience_min, experience_max = experience_years(experience)
        responsibilities = " ".join(filter(None, [
            find_section(sections, "Job Responsibilities"),
            find_section(sections, "Job Responsibilities Continue"),
        ]))
        technical_knowledge = find_section(sections, "Technical Professional Knowledge")
        behavioural_competencies = find_section(sections, "Behavioural Competencies")
        people_specification = find_section(sections, "People Specification")
        preferred_certifications = find_section(sections, "Preferred Certifications")
        reference = property_value(soup, "customfield1") or labelled_text(
            soup, "Requisition ID", "Req ID", "Requisition Number"
        )
        posted_at = property_value(soup, "datePosted", "date") or labelled_text(soup, "Date", "Posting Date")
        closing_date = property_value(soup, "validThrough") or labelled_text(soup, "Closing Date")
        geography = geographic_scope_evidence(
            country="South Africa",
            location=location,
            country_source_field="Nedbank South Africa source configuration",
            location_source_field="Location",
        )
        structured = {
            "job_family": job_family,
            "career_stream": career_stream,
            "leadership_pipeline": leadership_pipeline,
            "job_purpose": find_section(sections, "Job Purpose"),
            "job_responsibilities": responsibilities,
            "minimum_experience_level": experience,
            "minimum_requirements": minimum_requirements,
            "essential_qualifications": essential_qualification,
            "preferred_qualifications": preferred_qualification,
            "technical_professional_knowledge": technical_knowledge,
            "behavioural_competencies": behavioural_competencies,
            "people_specification": people_specification,
            "requisition_id": reference,
            "geography": geography,
        }
        fields = {
            "title": title, "company": self.company, "location_text": location,
            "country": geography["country"], "posted_at": posted_at,
            "geographic_scope": geography["status"],
            "geographic_scope_provenance": geography,
            "closing_date": closing_date,
            "reference_number": reference,
            "department": job_family, "business_unit": find_section(sections, "Location and Cluster"),
            "sector": career_stream, "description": find_section(sections, "Job Purpose"),
            "responsibilities": responsibilities,
            "qualification_text": qualification,
            "required_qualification": minimum_requirements or essential_qualification,
            "minimum_nqf": minimum_nqf(qualification),
            "experience_text": experience, "experience_min_years": experience_min,
            "experience_max_years": experience_max,
            "required_experience_text": experience,
            "required_experience_min": experience_min,
            "required_experience_max": experience_max,
            "qualification_minimum": qualification,
            "qualification_preferred": preferred_qualification,
            "source_native_career_level": leadership_pipeline,
            "skills_text": " ".join(filter(None, [technical_knowledge, behavioural_competencies])),
            "source_specific_evidence": structured,
            "source_career_stage_signal": self._stage_signals("", leadership_pipeline),
            "evidence_provenance": {
                "experience_text": evidence_provenance("Minimum Experience Level", source_text=experience),
                "qualification_text": evidence_provenance(
                    "Minimum Requirements / Essential Qualifications - NQF Level",
                    source_text=qualification,
                ),
                "source_native_career_level": evidence_provenance(
                    "Leadership Pipeline", source_text=leadership_pipeline
                ),
                "sector": evidence_provenance("Career Stream", source_text=career_stream),
                "geography": geography,
            },
            "eligibility_text": " ".join(filter(None, [
                preferred_certifications, technical_knowledge, behavioural_competencies, people_specification,
            ])),
            "publishable": geography["status"] != "confirmed_outside_south_africa",
        }
        return self.build_record(
            url, html, fields, {"labels": values, "sections": sections, "structured": structured}
        )
