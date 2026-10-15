"""Database counters shared by workers; no LocMem security boundary."""
from functools import wraps
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import EmployerAbuseCounter

DEFAULT_LIMITS = {'organization_create':(5,86400), 'verification_submit':(6,86400),
    'vacancy_publish':(30,3600), 'contact_reveal':(30,3600), 'discovery':(120,60)}


@transaction.atomic
def consume(action, subject):
    limit, seconds = getattr(settings,'EMPLOYER_ABUSE_LIMITS',{}).get(action,DEFAULT_LIMITS[action])
    now = timezone.now()
    key = f'{action}:{subject}'
    row,_ = EmployerAbuseCounter.objects.get_or_create(key=key,defaults={'window_started_at':now})
    EmployerAbuseCounter.objects.filter(pk=row.pk,window_started_at__lte=now-timedelta(seconds=seconds)).update(window_started_at=now,attempts=0)
    accepted = EmployerAbuseCounter.objects.filter(pk=row.pk,attempts__lt=limit).update(attempts=F('attempts')+1)
    if not accepted:
        raise PermissionDenied('Too many employer requests. Please try again after the cooldown.')


def limited(action, capability=None):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            user=kwargs['user']
            if not user.is_authenticated or not user.is_active:
                raise PermissionDenied
            if capability:
                from .permissions import membership_for
                membership_for(user,kwargs['organization_id'],capability)
                subject='org:'+str(kwargs['organization_id'])
            else:
                subject='user:'+str(user.pk)
            # This commits before the service's transaction, so invalid attempts count.
            consume(action,subject)
            return function(*args,**kwargs)
        return wrapped
    return decorator


def discovery_request(function):
    @wraps(function)
    def wrapped(request, organization_id, *args, **kwargs):
        if request.method == 'GET':
            from .permissions import discovery_membership
            discovery_membership(request.user,organization_id)
            consume('discovery','org:'+str(organization_id))
        return function(request,organization_id,*args,**kwargs)
    return wrapped
