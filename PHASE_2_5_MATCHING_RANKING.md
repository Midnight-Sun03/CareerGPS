# Phase 2.5 — CHAMP Evidence-Aware Matching and Ranking

## Core invariant

CareerGPS separates **known-fit percentage** from **recommendation quality**.

A result can truthfully be `1/1 = 100%` over the employer criteria CareerGPS can safely compare and still be a weak recommendation.

The admission invariant is:

> Positive evidence earns relevance. Uncertainty limits confidence. Confirmed blockers downgrade relevant jobs. Neither uncertainty nor blockers may manufacture relevance.

Ranking therefore happens in two stages:

1. admit the opportunity to recommendation Band A-F;
2. order opportunities inside that band using the logic appropriate to that band.

There is no single universal magic ranking score.

## Criterion matching preserved

- FOUND = 1.0
- RELATED = 0.5
- NOT_FOUND = 0.0
- UNKNOWN is excluded from fit denominators
- required criteria outweigh preferred criteria
- supporting behavioural evidence carries less weight than crucial/core evidence
- location, work arrangement and other preferences never create employer fit
- target career affects ranking/relevance, not employer eligibility
- deterministic facts and READY AI facts remain additive

## Important evidence categories

Important evidence means CRUCIAL or CORE evidence in these meaningful areas:

- qualification / NQF
- experience
- job-specific/core skills
- certification / licence
- language
- work eligibility

Supporting behavioural evidence remains separate.

## Admission sequence

### Step 1 — positive-support provisional band

Ignore confirmed major blockers for one moment and ask how much positive relevance the opportunity has actually earned.

### A — Strong supported match

Accept only when:

- evidence strength is High;
- direct FOUND important evidence spans at least 2 meaningful categories;
- required-important coverage is at least 80%.

RELATED-only breadth cannot create A.

### B — Good supported match

Accept only when:

- evidence strength is Medium or High;
- FOUND/RELATED important evidence spans at least 2 meaningful categories;
- at least 1 important category contains direct FOUND evidence;
- required-important coverage is at least 60%.

### C — Relevant / limited evidence

Accept when any one is true:

1. exact target career and there are no safely-comparable important employer criteria; or
2. exact/related target career and there is at least 1 positive important match; or
3. no career alignment, but positive important evidence spans at least 2 categories and required-important coverage is at least 30%.

C means there is a genuine reason to care, but CareerGPS cannot support A/B.

### D — Thin / weak support

Accept when A/B/C were not earned, but at least one weak signal exists:

- an important FOUND/RELATED match; or
- supporting evidence FOUND; or
- target-career alignment.

This is the natural home for sparse `1/1 = 100%` results.

### F — Low relevance / poor fit

If none of A-D is earned, provisional band is F.

A job does not become more relevant merely because CareerGPS found a missing requirement.

## Step 2 — confidence caps

Confidence caps never create failure.

If provisional band is A/B:

- `partial_text` source -> maximum Band C;
- any required CRUCIAL criterion still UNKNOWN -> maximum Band C.

UNKNOWN is uncertainty, not NOT_FOUND.

## Step 3 — blocker override

Confirmed major gaps are required CRUCIAL criteria with status NOT_FOUND.

If a confirmed major gap exists:

- capped provisional A/B/C -> final Band E;
- capped provisional D/F -> final Band F.

### E — Relevant but blocked

E therefore means:

> the opportunity had enough positive relevance to deserve A/B/C, but a confirmed important requirement currently blocks a stronger recommendation.

A blocker never promotes an unrelated or weakly-supported job into E.

## Major-gap types

### Gate-like gaps

- required certification / licence / registration
- required language
- explicit work eligibility

### Foundation gaps

- required qualification / field / NQF
- required experience

Ordinary missing technical skills do not individually trigger Band E; they reduce important coverage instead.

Preferred requirements never create a major gap.

UNKNOWN never creates a major gap.

## Alternative requirements

Employer alternatives must remain one requirement where the employer framed them as alternatives, e.g.:

`BSc Computer Science OR BEng Software Engineering`

Satisfying one alternative satisfies that criterion. CareerGPS must not count the unchosen alternative as a separate failure.

## Internal ordering

Band always outranks the internal comparator: A > B > C > D > E > F.

### A / B

1. breadth of required meaningful categories matched
2. required-important coverage
3. total matched important breadth
4. evidence strength
5. career direction
6. preferred-important coverage
7. preferences / feasibility
8. known-fit percentage
9. scope tie-break

### C

1. substantive matched breadth
2. required-important coverage
3. career direction
4. evidence strength
5. preferred-important coverage
6. preferences
7. known-fit percentage
8. scope tie-break

### D

1. career direction
2. strongest thin evidence: crucial, then core, then supporting-only
3. required-important coverage
4. matched breadth
5. preferences
6. known-fit percentage
7. scope tie-break

### E

1. fewer gate-like gaps
2. fewer foundation gaps
3. career direction
4. surviving required matched breadth
5. required-important coverage
6. total matched breadth
7. evidence strength
8. preferred-important coverage
9. preferences
10. known-fit percentage
11. scope tie-break

### F

F may contain completely unrelated jobs and D-level jobs further downgraded by blockers. Keep it simple:

1. residual career relation
2. residual substantive evidence
3. fewer confirmed gaps
4. preferences
5. stable scope/score tie-breaks

## Diagnostics

`show_profile_ranking` prints the admission route:

`provisional > confidence-capped > final`

Examples:

- `B>B>B` — earned and retained Good supported
- `B>C>C` — supported evidence, but confidence capped
- `C>C>E` — relevant, then blocked by a confirmed major requirement
- `D>D>F` — only thin relevance existed, and a blocker downgraded it to poor fit

It also prints critical UNKNOWN count and gate/foundation gap counts.
