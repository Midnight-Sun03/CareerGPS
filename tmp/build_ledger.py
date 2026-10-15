#!/usr/bin/env python3
"""Phase 3 Evidence Ledger builder (read-only w.r.t. the app; writes only report artifacts)."""
import json, csv, glob, os
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
LED_DIR = os.path.join(BASE, 'led')
OUT = r'D:\CG'
GEN = '2026-10-09'
STATUSES = ["IMPLEMENTED", "IMPLEMENTED BUT INCOMPLETE", "PARTIALLY IMPLEMENTED",
            "PLANNED / DOCUMENTED", "NOT FOUND", "UNKNOWN"]

def load():
    entries = []
    for p in sorted(glob.glob(os.path.join(LED_DIR, '*.json'))):
        try:
            with open(p, encoding='utf-8') as f:
                entries.extend(json.load(f))
        except Exception as e:
            print('WARN skip', os.path.basename(p), e)
    d = {}
    for e in entries:
        d[e['ledger_id']] = e
    return [d[k] for k in sorted(d)]

def w(name, text):
    with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
        f.write(text)
    print('wrote', name, len(text), 'chars')

entries = load()
print('entries loaded:', len(entries))
by_area = defaultdict(list)
for e in entries:
    by_area[e['area']].append(e)

# ---- Deliverable 1: JSON ----
w('EVIDENCE_LEDGER.json', json.dumps({
    'schema_version': '1.0', 'generated': GEN,
    'dimensions': ['intention', 'implementation', 'verification'],
    'statuses': STATUSES, 'count': len(entries), 'entries': entries},
    indent=2, ensure_ascii=False))

# ---- Deliverable 1: CSV ----
cols = ['ledger_id', 'area', 'capability', 'impl_status', 'impl_evidence',
        'exec_path', 'intention_source', 'verification_status', 'known_gaps',
        'confidence', 'uncertainties']
with open(os.path.join(OUT, 'EVIDENCE_LEDGER.csv'), 'w', newline='', encoding='utf-8') as f:
    wr = csv.writer(f)
    wr.writerow(cols)
    for e in entries:
        i = e.get('intention', {}); m = e.get('implementation', {}); v = e.get('verification', {})
        wr.writerow([e['ledger_id'], e['area'], e['capability'], m.get('status', ''),
                     m.get('evidence', ''), m.get('execution_path', ''), i.get('source', ''),
                     v.get('status', ''), e.get('known_gaps', ''), e.get('confidence', ''),
                     e.get('uncertainties', '')])
print('wrote EVIDENCE_LEDGER.csv')

# ---- Deliverable 1: Markdown ----
L = ['# CareerGPS — Evidence Ledger (Phase 3)', '',
     f'Generated {GEN} · {len(entries)} entries · three separate dimensions per entry: '
     '**Intention / Implementation / Verification** (implementation status is distinct from verification status).', '',
     'Statuses: ' + ', '.join(f'`{s}`' for s in STATUSES), '']
for area in sorted(by_area):
    L.append(f'\n## {area}\n')
    for e in by_area[area]:
        i = e.get('intention', {}); m = e.get('implementation', {}); v = e.get('verification', {})
        L.append(f"### {e['ledger_id']} — {e['capability']}")
        L.append(f"- **Implementation status:** {m.get('status','')} · **Confidence:** {e.get('confidence','')}")
        L.append(f"- **Intention:** {i.get('behavior','')}  \n  _Source:_ {i.get('source','')}")
        L.append(f"- **Implementation evidence:** {m.get('evidence','')}")
        L.append(f"- **Execution path:** {m.get('execution_path','')}")
        L.append(f"- **Verification ({v.get('status','')}):** {v.get('detail','')}")
        L.append(f"- **Known gaps:** {e.get('known_gaps','')}")
        L.append(f"- **Uncertainties:** {e.get('uncertainties','')}")
        rel = e.get('related_entries') or []
        if rel:
            L.append(f"- **Related:** {', '.join(rel)}")
        L.append('')
w('EVIDENCE_LEDGER.md', '\n'.join(L))

# ---- Deliverable 2: Evidence Index ----
idx = ['# Evidence Index (Phase 3)', '', '## Intention sources (authoritative documents cited by ledger entries)', '']
seen = set()
for e in entries:
    for s in (e.get('intention', {}).get('source', '') or '').split(';'):
        s = s.strip()
        if s and s.lower() not in ('none', 'n/a', '') and s not in seen:
            seen.add(s); idx.append(f'- {s}')
idx += ['', '## Phase 2 runtime evidence (read-only probes actually executed)', '',
        '- `REPOSITORY_TRUTH_AUDIT.md` — Phase 2 audit (332 lines).',
        '- `tmp/cg_db_audit.txt`, `tmp/cg_db_audit2.txt`, `tmp/cg_db_audit3.txt` — read-only SQLite probe outputs (SELECT/COUNT/GROUP BY only).',
        '- `tmp/cg_audit_ro2.py`, `tmp/cg_audit_ro3.py`, `tmp/db_probe.py` — probe scripts (no writes/migrations).',
        '- Phase 2 §10 test executions: `core.test_clean_architecture` (22 OK), `core.test_community.OpportunityIntegrityTests` (8 OK, 1 skipped), `core.test_feed_v2` (FAILED: 2 failures, 3 skipped).',
        '- `makemigrations --check` aborted on the `DEBUG=release` env quirk (see G-entries); not a model/migration inconsistency.']
w('EVIDENCE_INDEX.md', '\n'.join(idx))

# ---- Deliverable 4: Coverage & Confidence ----
impl = Counter(e.get('implementation', {}).get('status', '') for e in entries)
ver = Counter(e.get('verification', {}).get('status', '') for e in entries)
conf = Counter(e.get('confidence', '') for e in entries)
c = ['# Coverage & Confidence Report (Phase 3)', '', f'Total capability entries: **{len(entries)}**', '',
     '## By implementation status', '', '| Status | Count |', '|---|---|']
for s in STATUSES:
    c.append(f'| {s} | {impl.get(s, 0)} |')
c += ['', '## By verification status', '', '| Verification status | Count |', '|---|---|']
for k, v in ver.most_common():
    c.append(f'| {k} | {v} |')
c += ['', '## By confidence', '', '| Confidence | Count |', '|---|---|']
for k in ['High', 'Medium', 'Low']:
    if conf.get(k):
        c.append(f'| {k} | {conf.get(k, 0)} |')
c += ['', '## By area', '', '| Area | Count |', '|---|---|']
for k, v in sorted(by_area.items()):
    c.append(f'| {k} | {v} |')
c += ['', '> Counts measure the audit\'s recorded findings, not overall product quality.']
w('COVERAGE_CONFIDENCE.md', '\n'.join(c))
print('DONE core artifacts')
