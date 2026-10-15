# CareerGPS Phase 2.6.2 — Ranking Admission Fix

## Problem
A low-evidence, non-career-aligned partial-source opportunity could enter Band C solely because two important categories matched, even when the advert contained only two comparable criteria. Inside Band C, substantive breadth is ranked before career direction. This allowed thin unrelated roles to outrank exact target-role opportunities that had insufficient structured employer criteria.

## Fix
The Phase 2.5 band architecture remains intact. The unrelated Band C admission rule now requires:

- at least two matched important categories;
- at least 30% required important coverage; and
- either High/Medium evidence strength, or at least four comparable important criteria.

Low-evidence unrelated jobs with only two or three comparable important criteria now remain Band D. Exact/related career-direction logic is unchanged.

## Result for Ash test profile
- Software Engineer (Python) - Supply Chain remains first because it has substantial High evidence across two categories.
- Exact Data Analyst / Business Analyst target roles move ahead of thin unrelated Customer Consultant / Sales / YES partial-source jobs.
- Career preferences still affect ranking only; they do not manufacture employer eligibility.

## Validation
- Django system check: pass
- Full test suite: 234/234 pass
- Matching logic regression test added for the two-category partial-source case
- No migration required
