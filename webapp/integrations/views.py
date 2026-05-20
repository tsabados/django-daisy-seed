import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import IntegrationConnection
from .oauth import oauth
from .providers import registry

logger = logging.getLogger(__name__)


def _get_provider_or_404(provider_key):
    provider = registry.get_provider(provider_key)
    if provider is None:
        raise Http404(f'Unknown provider: {provider_key}')
    return provider


@login_required
def integration_list(request):
    providers = registry.get_all_providers()
    connections = IntegrationConnection.objects.filter(project=request.project)

    # Build a set of connected provider+account combos for quick lookup
    connected_keys = set()
    for conn in connections:
        connected_keys.add(f'{conn.provider}:{conn.external_account_id}')

    return render(request, 'integrations/integration_list.html', {
        'providers': providers,
        'connections': connections,
        'connected_keys': connected_keys,
    })


@login_required
def integration_connect(request, provider):
    prov = _get_provider_or_404(provider)
    client = oauth.create_client(prov.key)
    return prov.get_authorize_redirect(request, client)


@login_required
def integration_callback(request, provider):
    prov = _get_provider_or_404(provider)

    try:
        token_data = prov.handle_callback(request)
    except Exception:
        logger.exception('OAuth callback failed for provider %s', provider)
        messages.error(request, f'Failed to connect {prov.display_name}. Please try again.')
        return redirect('integrations:integration_list')

    if prov.has_account_selection:
        # Store token data in session for the selection step
        request.session[f'integration_token_{provider}'] = json.dumps(token_data, default=str)
        return redirect('integrations:integration_select_account', provider=provider)

    # No selection needed — save directly
    try:
        accounts = prov.list_accounts(token_data)
        if accounts:
            prov.save_connection(request.user, accounts[0], token_data, project=request.project)
            messages.success(request, f'{prov.display_name} connected successfully.')
        else:
            messages.warning(request, f'No accounts found for {prov.display_name}.')
    except Exception:
        logger.exception('Failed to save connection for provider %s', provider)
        messages.error(request, f'Failed to save {prov.display_name} connection.')

    return redirect('integrations:integration_list')


@login_required
def integration_select_account(request, provider):
    prov = _get_provider_or_404(provider)
    session_key = f'integration_token_{provider}'
    token_json = request.session.get(session_key)

    if not token_json:
        messages.error(request, 'Session expired. Please connect again.')
        return redirect('integrations:integration_list')

    token_data = json.loads(token_json)

    if request.method == 'POST':
        account_id = request.POST.get('account_id')
        if not account_id:
            messages.error(request, 'Please select an account.')
            return redirect('integrations:integration_select_account', provider=provider)

        try:
            accounts = prov.list_accounts(token_data)
            selected = next((a for a in accounts if str(a['id']) == str(account_id)), None)

            if selected is None:
                messages.error(request, 'Selected account not found. Please try again.')
                return redirect('integrations:integration_select_account', provider=provider)

            prov.save_connection(request.user, selected, token_data, project=request.project)
            # Clean up session
            del request.session[session_key]
            messages.success(request, f'{prov.display_name} connected successfully.')
        except Exception:
            logger.exception('Failed to save connection for provider %s', provider)
            messages.error(request, f'Failed to save {prov.display_name} connection.')

        return redirect('integrations:integration_list')

    # GET — list accounts
    try:
        accounts = prov.list_accounts(token_data)
    except Exception:
        logger.exception('Failed to list accounts for provider %s', provider)
        messages.error(request, f'Could not load accounts from {prov.display_name}.')
        return redirect('integrations:integration_list')

    return render(request, 'integrations/select_account.html', {
        'provider': prov,
        'accounts': accounts,
    })


@require_POST
@login_required
def integration_disconnect(request, pk):
    connection = get_object_or_404(IntegrationConnection, pk=pk, project=request.project)
    provider_name = connection.provider
    prov = registry.get_provider(provider_name)
    display_name = prov.display_name if prov else provider_name
    account_name = connection.external_account_name or connection.external_account_id
    connection.delete()
    messages.success(request, f'Disconnected {display_name} ({account_name}).')
    return redirect('integrations:integration_list')
