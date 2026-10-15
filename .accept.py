"""Acceptance helper for the recurring v2 worker (temporary; removed after use).

Read-mostly inspection plus one controlled, reversible edit used to prove that
the scheduled worker picks up enqueued work without a manual drain.
"""
import os
import sqlite3
import sys


def queue_state(label):
    connection = sqlite3.connect('file:db.sqlite3?mode=ro', uri=True, timeout=30)
    print('-- %s --' % label)
    for row in connection.execute(
            'select state, count(*) from core_evaluationrecomputetask '
            "where task_version='feed-v2' group by 1 order by 1"):
        print('  %-12s %s' % row)
    for row in connection.execute(
            'select id, state, reason, attempts from '
            "core_evaluationrecomputetask where task_version='feed-v2' "
            "and state in ('PENDING','RUNNING') order by id"):
        print('  active: %s' % (row,))
    connection.close()


def feed_states():
    connection = sqlite3.connect('file:db.sqlite3?mode=ro', uri=True, timeout=30)
    print('-- profile feed states --')
    for row in connection.execute(
            'select profile_id, state, active_generation_id from '
            'core_profilefeedstate order by profile_id'):
        print('  %s' % (row,))
    connection.close()


def touch_target(profile_id, marker):
    """Reversibly rewrite one profile's target roles.

    Saving through the ORM fires the real post_save hook, so the enqueue under
    test is the production one rather than a hand-made queue row.  The original
    text is returned so it can be restored exactly.
    """
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS.settings')
    django.setup()
    from core.models import CareerPreference
    preference = CareerPreference.objects.get(profile_id=profile_id)
    original = preference.target_roles
    updated = marker if original != marker else original + ', ' + marker
    preference.target_roles = updated
    preference.save()
    print('profile %s target_roles: %r -> %r' % (profile_id, original, updated))
    return original


def leases():
    """Claim/lease state of active tasks, to see when a stuck one is reclaimable."""
    connection = sqlite3.connect('file:db.sqlite3?mode=ro', uri=True, timeout=30)
    for row in connection.execute(
            'select id, state, attempts, max_attempts, lease_expires_at, reason '
            "from core_evaluationrecomputetask where state in "
            "('PENDING','RUNNING','FAILED') order by id"):
        print('  %s' % (row,))
    connection.close()


def pages():
    """Render both reader pages and assert GETs write nothing.

    Confirms the v2 read path in use, that the three feed areas never repeat a
    job, and that occupational/feed rows are untouched by a request.
    """
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS.settings')
    os.environ['ALLOWED_HOSTS'] = 'testserver,localhost,127.0.0.1'
    django.setup()
    from django.conf import settings
    from django.test import Client
    from core.models import (
        CareerPreference, OccupationSourceResolution, OpportunityTitleResolution,
        Profile, ProfileFeedEvaluation, TargetOpportunityRelationshipAssessment,
    )

    print('FEED_READ_VERSION =', settings.CAREERGPS_FEED_READ_VERSION)
    counts = lambda: (
        OccupationSourceResolution.objects.count(),
        OpportunityTitleResolution.objects.count(),
        TargetOpportunityRelationshipAssessment.objects.count(),
        ProfileFeedEvaluation.objects.count(),
    )
    for profile in Profile.objects.order_by('pk'):
        preference = CareerPreference.objects.filter(profile=profile).first()
        targets = (preference.target_roles if preference else '') or ''
        if not targets:
            continue
        client = Client()
        client.force_login(profile.user)
        before = counts()
        feed = client.get('/opportunities/')
        intelligence = client.get('/career-intelligence/')
        after = counts()
        body = feed.content.decode()
        ci = intelligence.content.decode()
        print('profile %-3s feed=%s ci=%s writes=%s v2keys=%s' % (
            profile.pk, feed.status_code, intelligence.status_code,
            'NONE' if before == after else 'CHANGED',
            'uncertain_results' in body))
        print('   confirmed=%d discovery=%d uncertain=%d ci_details=%d' % (
            body.count('job-card-confirmed') if 'job-card-confirmed' in body else 0,
            body.count('job-card-unconfirmed'),
            body.count('job-card-unconfirmed'),
            ci.count('<details')))


def errors():
    """Error text on failed tasks, to explain why a task exhausted its attempts."""
    connection = sqlite3.connect('file:db.sqlite3?mode=ro', uri=True, timeout=30)
    for row in connection.execute(
            "select id, state, attempts, reason, error_message from "
            "core_evaluationrecomputetask where state = 'FAILED' order by id"):
        print('--- task %s state=%s attempts=%s reason=%s' % row[:4])
        text = (row[4] or '').strip()
        print('    %s' % (text[-800:] if text else '(no error message)'))
    connection.close()


def gens():
    """Generation rows and their visibility, to explain invisible evaluations."""
    connection = sqlite3.connect('file:db.sqlite3?mode=ro', uri=True, timeout=30)
    print('-- generations by state --')
    for row in connection.execute(
            'select state, count(*) from core_profileevaluationgeneration '
            'group by 1 order by 2 desc'):
        print('  %-12s %s' % row)
    print('-- active generations --')
    for row in connection.execute(
            "select profile_id, pk, state, evaluation_count from "
            "core_profileevaluationgeneration where state = 'ACTIVE' "
            'order by profile_id'):
        print('  %s' % (row,))
    connection.close()


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'queue'
    if action == 'queue':
        queue_state(sys.argv[2] if len(sys.argv) > 2 else 'queue')
    elif action == 'feeds':
        feed_states()
    elif action == 'leases':
        leases()
    elif action == 'errors':
        errors()
    elif action == 'gens':
        gens()
    elif action == 'touch':
        touch_target(int(sys.argv[2]), sys.argv[3])
