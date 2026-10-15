"""Event-based employer analytics. No inferred transitions or generated prose."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from statistics import mean, median
from django.db.models import Count
from django.utils import timezone
from .models import HiringApplication, EmployerAuditEvent

STAGES = dict(HiringApplication.STAGES)
CONVERSIONS = [('received', 'screening'), ('screening', 'shortlisted'),
    ('shortlisted', 'interview'), ('interview', 'offer'), ('offer', 'hired')]
ACTIVITY = {'vacancy_published', 'vacancy_reopened', 'vacancy_closed', 'application_received',
    'application_screening', 'application_shortlisted', 'application_assessment', 'application_interview',
    'application_final_review', 'application_offer', 'application_hired', 'application_rejected',
    'application_withdrawn', 'task_assigned', 'task_reassigned', 'task_completed', 'task_cancelled',
    'responsibility_assigned', 'responsibility_removed', 'talent_pool_created', 'talent_pool_candidate_added',
    'talent_pool_candidate_removed', 'talent_pool_archived'}


def activity_rows(member):
    # Do not render raw audit context, actors, private notes, contact reveals or searches.
    from .permissions import CAPABILITIES
    allowed = set(ACTIVITY)
    if 'talent' not in CAPABILITIES[member.role]:
        allowed = {key for key in allowed if not key.startswith('talent_')}
    if member.role == 'interviewer':
        allowed = {key for key in allowed if not key.startswith(('task_', 'responsibility_'))}
    return EmployerAuditEvent.objects.filter(organization=member.organization, event_type__in=allowed).values('event_type', 'created_at').order_by('-created_at', '-pk')


def activity_projection(rows):
    return [{'label': row['event_type'].replace('_', ' ').capitalize(), 'created_at': row['created_at']} for row in rows]


def days(delta):
    return delta.total_seconds() / 86400


def metric(values):
    return {'count': len(values), 'mean': round(mean(values), 2) if values else None,
        'median': round(median(values), 2) if values else None}


def funnel(member, vacancy=None, now=None):
    from .permissions import membership_for
    member=membership_for(member.user,member.organization_id,'analytics')
    now = now or timezone.now()
    rows = HiringApplication.objects.filter(vacancy__organization=member.organization)
    if vacancy:
        rows = rows.filter(vacancy=vacancy)
    current = Counter(dict(rows.filter(withdrawn_at__isnull=True).values('stage').annotate(n=Count('pk')).values_list('stage', 'n')))
    withdrawn = rows.filter(withdrawn_at__isnull=False).count()
    reached = Counter()
    converted = Counter()
    times = defaultdict(list)
    dwell = defaultdict(list)
    ages = defaultdict(list)
    for hiring in rows.only('id', 'stage', 'submitted_at', 'withdrawn_at').prefetch_related('stage_events').iterator(chunk_size=100):
        end = min(now, hiring.withdrawn_at) if hiring.withdrawn_at else now
        events = [e for e in hiring.stage_events.all() if hiring.submitted_at <= e.created_at <= end]
        first = {}
        first_order = {}
        for index, event in enumerate(events):
            first.setdefault(event.to_stage, event.created_at)
            first_order.setdefault(event.to_stage, index)
        reached.update(first.keys())
        for source, target in CONVERSIONS:
            if source in first and any(e.to_stage == target and index > first_order[source] for index,e in enumerate(events)):
                converted[(source, target)] += 1
        review = [e.created_at for e in events if e.to_stage != 'received']
        if review:
            times['first_review'].append(days(min(review) - hiring.submitted_at))
        for stage in ['shortlisted', 'interview', 'offer', 'hired']:
            if stage in first:
                times[stage].append(days(first[stage] - hiring.submitted_at))
        for entry, following in zip(events, events[1:]):
            if following.from_stage == entry.to_stage and entry.to_stage not in {'hired', 'rejected'}:
                dwell[entry.to_stage].append(days(following.created_at - entry.created_at))
        if events and events[-1].to_stage == hiring.stage and hiring.stage not in {'hired', 'rejected'}:
            if hiring.withdrawn_at:
                dwell[hiring.stage].append(days(end - events[-1].created_at))
            else:
                ages[hiring.stage].append(days(now - events[-1].created_at))
    source_ids = EmployerAuditEvent.objects.filter(organization=member.organization,
        event_type='application_received', object_type='hiringapplication', object_id__in=rows.values('pk')).values('object_id').distinct().count()
    total = rows.count()
    return {'total': total, 'current_total': total - withdrawn,
        'stages': [{'label': label, 'count': current[key], 'reached': reached[key]} for key, label in STAGES.items()],
        'withdrawn': withdrawn,
        'conversions': [{'label': STAGES[a] + ' to ' + STAGES[b], 'denominator': reached[a], 'numerator': converted[(a,b)],
            'rate': round(100 * converted[(a,b)] / reached[a], 1) if reached[a] else None} for a,b in CONVERSIONS],
        'times': [{'label': label, **metric(times[key])} for key, label in [('first_review', 'First recorded review'),
            ('shortlisted', 'Shortlist'), ('interview', 'Interview'), ('offer', 'Offer'), ('hired', 'Hire')]],
        'dwell': [{'label': label, **metric(dwell[key]), 'current': metric(ages[key])} for key, label in STAGES.items()],
        'sources': [('Direct CareerGPS submission (recorded event)', source_ids), ('Unavailable source history', total-source_ids)]}


def vacancy_time(vacancy, now=None):
    now = now or timezone.now()
    events = EmployerAuditEvent.objects.filter(organization=vacancy.organization,
        object_type='employervacancy', object_id=vacancy.pk,
        event_type__in=['vacancy_published', 'vacancy_reopened', 'vacancy_closed']).order_by('created_at', 'pk')
    start = None
    intervals = []
    known = False
    for event in events:
        if event.created_at > now:
            continue
        if event.event_type in {'vacancy_published', 'vacancy_reopened'}:
            known = True
            if start is None:
                start = event.created_at
        elif start:
            intervals.append((start, event.created_at))
            start = None
    if start and vacancy.status == 'published':
        end = now
        if vacancy.closing_date:
            expiry = timezone.make_aware(datetime.combine(vacancy.closing_date + timedelta(days=1), time.min))
            end = min(end, expiry)
        intervals.append((start, max(start, end)))
    elif start:
        # A mutable closed status without a close event cannot supply a timestamp.
        known = False
    return {'age': round(days(now-vacancy.created_at), 2),
        'days_open': round(sum(days(b-a) for a,b in intervals), 2) if known else None}


def application_stage_age(hiring, now=None):
    if hiring.withdrawn_at or hiring.stage in {'hired', 'rejected'}:
        return None
    event = hiring.stage_events.order_by('-created_at', '-pk').first()
    now = now or timezone.now()
    if not event or event.to_stage != hiring.stage or not hiring.submitted_at <= event.created_at <= now:
        return None
    return round(days(now-event.created_at), 2)
