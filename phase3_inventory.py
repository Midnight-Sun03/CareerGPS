"""Phase 3 step 1: read-only catalogue occupation inventory (dev DB).

Groups READY opportunities by cleaned title tokens to find occupational
clusters with multiple jobs, so target choices rest on catalogue evidence.
Also reports the actuarial cluster specifically (earlier symptom).
"""
import io
import os
import re
import sys
from collections import Counter, defaultdict

os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

import django
django.setup()

from core.models import Opportunity  # noqa: E402

STOP = {'i', 'ii', 'iii', 'iv', 'senior', 'junior', 'lead', 'head',
        'manager', 'director', 'specialist', 'engineer', 'analyst',
        'assistant', 'officer', 'executive', 'associate', 'principal'}

qs = Opportunity.objects.professionally_ready(
    include_obvious_senior=True).select_related(
    'active_clean_revision__independent_occupation_resolution')

rows = []
for opp in qs:
    rev = opp.active_clean_revision
    if rev is None:
        continue
    title = (rev.cleaned_title or '').strip()
    if title:
        rows.append((opp.pk, title, rev.pk))

print(f'READY opportunities with clean title: {len(rows)}')

# Token -> jobs index (drop generic seniority words).
token_jobs = defaultdict(set)
for pk, title, rev in rows:
    for tok in re.findall(r"[A-Za-z][A-Za-z\-]+", title.lower()):
        tok = tok.strip('-')
        if len(tok) < 4 or tok in STOP:
            continue
        token_jobs[tok].add(pk)

# Occupational-ish clusters: tokens appearing in >= 3 jobs.
clusters = {t: j for t, j in token_jobs.items() if len(j) >= 3}
print(f'\ntokens in >=3 jobs: {len(clusters)}')
for tok in sorted(clusters, key=lambda t: -len(clusters[t]))[:40]:
    titles = sorted({t for pk, t, _ in rows if pk in clusters[tok]})
    print(f'  {tok}: {len(clusters[tok])} jobs -> {titles[:6]}')

print('\n--- actuarial cluster (earlier symptom) ---')
act = [(pk, t) for pk, t, _ in rows if 'actuar' in t.lower()]
for pk, t in sorted(act):
    print(f'  #{pk} {t}')
if not act:
    print('  (no actuarial titles in catalogue)')

print('\n--- sample of other multi-job occupational titles ---')
title_counts = Counter(t for _, t, _ in rows)
for title, n in title_counts.most_common(30):
    if n >= 2:
        print(f'  x{n} {title}')

print('\nPHASE3_INVENTORY_DONE')
