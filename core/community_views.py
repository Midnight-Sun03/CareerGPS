"""Reporting and snapshot-driven career pages in the existing authenticated shell."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.models import Story, Opportunity, Profile
from core.forms import StoryReportForm, OpportunityReportForm
from core.services.moderation import submit_report
from core.services.career_intent import profile_careers
from core.services.career_analysis import build_career_analysis


def _report_page(request, target, form_class, return_route):
    form = form_class(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            submit_report(reporter=request.user, target=target, **form.cleaned_data)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'An open report already exists.')
        else:
            messages.success(request, 'Your report was submitted privately for moderator review. Reporting does not automatically remove this item.')
            return redirect(return_route)
    return render(request, 'core/report_item.html', {'form': form, 'target': target, 'return_route': return_route})


@login_required
@require_http_methods(['GET', 'POST'])
def report_story(request, story_id):
    story = get_object_or_404(Story.objects.filter(status='approved').exclude(user=request.user), pk=story_id)
    return _report_page(request, story, StoryReportForm, 'stories')


@login_required
@require_http_methods(['GET', 'POST'])
def report_opportunity(request, opportunity_id):
    permitted = Opportunity.objects.filter(Q(pk__in=Opportunity.objects.professionally_ready().values('pk')) |
        Q(savedopportunity__user=request.user) | Q(applications__user=request.user)).distinct()
    opportunity = get_object_or_404(permitted, pk=opportunity_id)
    return _report_page(request, opportunity, OpportunityReportForm, 'opportunities')


@login_required
@require_http_methods(['GET'])
def career_intelligence(request):
    profile = get_object_or_404(Profile, user=request.user)
    roles = profile_careers(profile)
    analysis = build_career_analysis(profile)
    if request.GET.get('target') is not None:
        for panel in analysis['authored_targets']:
            if str(panel['ordinal']) == request.GET['target']:
                analysis['selected'] = panel
                break
    return render(request, 'core/career_intelligence.html', {
        'direction_roles': roles, 'analysis': analysis,
    })
