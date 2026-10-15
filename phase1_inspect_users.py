"""Phase 1: inspect real dev DB for a suitable persisted user (read-only)."""
import os
import sys
import io

os.environ['DEBUG'] = 'True'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace', line_buffering=True)

import django
django.setup()

from django.contrib.auth.models import User
from core.models import (
    CareerPreference, CareerTargetResolution, Opportunity,
    ProfileEvaluationGeneration, ProfileFeedState,
)
from core.models import Opportunity as Opp
from django.db.models import Count


def ready_count():
    return Opp.objects.professionally_ready(include_obvious_senior=False).count()


print('READY catalogue size:', ready_count())
print('Total opportunities:', Opp.objects.count())
print()

rows = (CareerPreference.objects
        .exclude(target_roles='')
        .select_related('profile', 'profile__user', 'profile__feed_state')
        .annotate(gen_count=Count('profile__evaluation_generations'))
        .order_by('-profile_id'))

print('Users with non-empty target_roles:')
for cp in rows[:20]:
    p = cp.profile
    st = getattr(p, 'feed_state', None)
    print(f'  profile={p.pk} user={p.user.username!r} target={cp.target_roles!r} '
          f'source={cp.source} updated={cp.updated_at} '
          f'feed_state={st.state if st else None} '
          f'pending={st.pending_reason if st else None} '
          f'active_gen={st.active_generation_id if st else None} '
          f'generations={cp.gen_count}')

print()
print('Sample catalogue titles (first 40 by pk):')
for o in Opp.objects.order_by('pk').values_list('pk', 'active_clean_revision__cleaned_title')[:40]:
    print('  ', o)

print()
print('Distinct cleaned-title word clouds not computed; showing profession ready titles count by keyword:')
for kw in ('Engineer', 'Data', 'Nurse', 'Teacher', 'Account', 'Warehouse', 'Driver', 'Chef', 'Developer', 'Analyst'):
    n = (Opp.objects.professionally_ready(include_obvious_senior=False)
         .filter(active_clean_revision__cleaned_title__icontains=kw).count())
    print(f'  {kw}: {n}')
