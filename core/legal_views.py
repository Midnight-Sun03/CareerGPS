"""Public legal information and private, owner-scoped privacy requests."""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from . import data_rights
from .forms import AccountClosurePasswordForm, PrivacyRequestForm
from .legal import current_document, public_operator_facts
from .models import DataRightsEvent, PrivacyRequest


@require_GET
def terms(request):
    return render(request, 'core/legal_document.html', {
        'document': current_document('terms'), 'operator_facts': public_operator_facts(),
    })


@require_GET
def privacy(request):
    return render(request, 'core/legal_document.html', {
        'document': current_document('privacy'), 'operator_facts': public_operator_facts(),
    })


@require_GET
def about(request):
    return render(request, 'core/about.html')


@require_GET
def accessibility(request):
    return render(request, 'core/accessibility.html')


@never_cache
@require_http_methods(['GET', 'POST'])
def contact(request):
    if request.method == 'POST' and not request.user.is_authenticated:
        return redirect('login')
    initial = {}
    if request.GET.get('category') in PrivacyRequest.Category.values:
        initial['category'] = request.GET['category']
    form = PrivacyRequestForm(request.POST or None, initial=initial)
    valid_post = request.method == 'POST' and form.is_valid()
    if valid_post:
        recent_count = PrivacyRequest.objects.filter(
            requester=request.user,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent_count >= 5:
            form.add_error(None, 'Please wait before sending another privacy request.')
            valid_post = False
    if valid_post:
        privacy_request = form.save(commit=False)
        privacy_request.requester = request.user
        privacy_request.contact_email = request.user.email
        privacy_request.save()
        messages.success(request, 'Your privacy request has been received.')
        return redirect('privacy_request_detail', pk=privacy_request.pk)
    history = (PrivacyRequest.objects.filter(requester=request.user)
               if request.user.is_authenticated else None)
    return render(request, 'core/contact.html', {
        'form': form, 'history': history,
        'operator_facts': public_operator_facts(),
        'contact_email': settings.CAREERGPS_OPERATOR.get('contact_email'),
        'privacy_email': settings.CAREERGPS_OPERATOR.get('privacy_email'),
    })


@never_cache
@login_required
@require_GET
def privacy_request_detail(request, pk):
    item = get_object_or_404(PrivacyRequest, pk=pk, requester=request.user)
    return render(request, 'core/privacy_request_detail.html', {'item': item})


# ---------------------------------------------------------------------------
# Self-service data rights: export and account closure
# ---------------------------------------------------------------------------

@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def data_export(request):
    """Stream a complete ZIP of the requester's own CareerGPS data."""

    if request.method != 'POST':
        return redirect('settings')
    if data_rights.export_is_throttled(request.user):
        messages.error(request, 'You have reached the data export limit for now. '
            'Please try again later.')
        return redirect('settings')

    spool, manifest = data_rights.open_export_spool(request.user)
    data_rights.record_event(request.user, DataRightsEvent.Action.EXPORT_CREATED, {
        'sections': len(manifest['sections']),
        'files': len(manifest['files']),
        'missing_files': len(manifest['missing_files']),
    })
    response = FileResponse(
        spool,
        as_attachment=True,
        filename=f'careergps-data-export-{timezone.localdate().isoformat()}.zip',
        content_type='application/zip',
    )
    response['Cache-Control'] = 'no-store'
    return response


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def account_closure_request(request):
    """Step one: verify the password before anything destructive is offered."""

    blockers = data_rights.closure_blockers(request.user)
    if blockers:
        data_rights.record_event(request.user, DataRightsEvent.Action.CLOSURE_BLOCKED,
            {'reason': 'employer_organisation'})
        messages.error(request, blockers[0])
        return redirect('settings')

    form = AccountClosurePasswordForm(request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            if data_rights.password_failure_is_throttled(request.user):
                form.add_error(None, data_rights.STATUS_MESSAGES['password_throttled'])
            elif request.user.check_password(form.cleaned_data['password']):
                data_rights.issue_closure_token(request)
                return redirect('account_closure_confirm')
            else:
                data_rights.record_password_failure(request.user)
                form.add_error(None, data_rights.STATUS_MESSAGES['password_failed'])
    else:
        # A page load can never inherit an earlier confirmation step.
        data_rights.clear_closure_token(request)
    return render(request, 'core/account_closure_password.html', {'form': form})


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def account_closure_confirm(request):
    """Step two: the final confirmation and the only destructive request."""

    if not data_rights.closure_token_is_valid(request):
        messages.error(request, data_rights.STATUS_MESSAGES['token_required'])
        return redirect('account_closure')

    blockers = data_rights.closure_blockers(request.user)
    if blockers:
        data_rights.clear_closure_token(request)
        messages.error(request, blockers[0])
        return redirect('settings')

    if request.method != 'POST':
        return render(request, 'core/account_closure_confirm.html')

    data_rights.record_event(request.user, DataRightsEvent.Action.CLOSURE_CONFIRMED)
    report = data_rights.close_account(request.user)
    data_rights.clear_closure_token(request)

    logout(request)
    if report.email_sent:
        messages.success(request, 'Your CareerGPS account has been deleted. '
            'A confirmation has been emailed to you.')
    else:
        messages.success(request, 'Your CareerGPS account has been deleted.')
    return redirect('landing')
