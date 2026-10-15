"""Read-only inspection of one user's target-change pipeline state."""
import json
import sqlite3

con = sqlite3.connect('file:d:/CG/db.sqlite3?mode=ro', uri=True)
con.row_factory = sqlite3.Row


def one(sql, params=()):
    row = con.execute(sql, params).fetchone()
    return dict(row) if row else None


def short(value, limit=160):
    text = str(value or '')
    return text if len(text) <= limit else text[:limit] + '...'


user = one("SELECT id, username, email, is_active, date_joined FROM auth_user WHERE email = ?",
           ('tebogoashley13@gmail.com',))
print('user:', user)
if not user:
    raise SystemExit('no such user in this db')

profile = one("SELECT id, user_id FROM core_profile WHERE user_id = ?", (user['id'],))
print('profile:', profile)
if not profile:
    raise SystemExit('user has no profile row')
pid = profile['id']

pref = one("SELECT id, target_roles, updated_at FROM core_careerpreference WHERE profile_id = ?",
           (pid,))
print('preference:', {'id': (pref or {}).get('id'),
                      'target_roles': short((pref or {}).get('target_roles')),
                      'updated_at': (pref or {}).get('updated_at')})

state = one("SELECT state, pending_reason, last_error, active_generation_id, updated_at "
            "FROM core_profilefeedstate WHERE profile_id = ?", (pid,))
print('feed_state:', {k: short(v, 200) for k, v in (state or {}).items()})

print('latest generations:')
for row in con.execute(
        "SELECT id, state, build_reason, evaluation_count, started_at, completed_at, "
        "substr(intent_dependency_hash,1,12) AS intent, substr(dependency_hash,1,12) AS dep "
        "FROM core_profileevaluationgeneration WHERE profile_id = ? "
        "ORDER BY id DESC LIMIT 3", (pid,)):
    print(' ', dict(row))

print('latest tasks:')
for row in con.execute(
        "SELECT id, task_version, state, reason, created_at, completed_at, attempts, "
        "substr(dependency_hash,1,12) AS dep "
        "FROM core_evaluationrecomputetask WHERE profile_id = ? "
        "ORDER BY id DESC LIMIT 8", (pid,)):
    print(' ', dict(row))

targets = con.execute(
    "SELECT id, ordinal, state, authored_text FROM core_careertargetresolution "
    "WHERE profile_id = ? ORDER BY ordinal, id", (pid,)).fetchall()
print('target containers:', len(targets))
for row in targets:
    row = dict(row)
    row['authored_text'] = short(row['authored_text'], 80)
    print(' ', row)

gen = (state or {}).get('active_generation_id')
if gen:
    diag = one("SELECT diagnostics FROM core_profileevaluationgeneration WHERE id = ?", (gen,))
    try:
        diagnostics = json.loads(diag['diagnostics'] or '{}')
    except (TypeError, ValueError):
        diagnostics = {}
    keep = {k: diagnostics.get(k) for k in (
        'candidate_revision_count' if False else 'candidate_revision_count',
        'eligible_revision_count', 'skipped_unrelated_revision_count',
        'assessed_revision_count', 'assessments_reused', 'assessments_recomputed',
        'gap_resolved_revision_count', 'admitted_evaluation_count') if k in diagnostics}
    print('active generation diagnostics:', keep)
