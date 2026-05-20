import secrets
from urllib.parse import urlencode, quote

import requests as http_requests
from datetime import timedelta

from django.conf import settings as django_settings
from django.shortcuts import redirect
from django.utils import timezone

from ..models import IntegrationConnection
from .base import BaseProvider

TIKTOK_API_BASE = 'https://open.tiktokapis.com/v2'


class TikTokProvider(BaseProvider):
    """
    TikTok integration using Login Kit (OAuth v2) and Content Posting API.
    Single account per auth — no account selection step needed.
    """

    key = 'tiktok'
    display_name = 'TikTok'
    category = 'social_media'

    icon_svg = (
        '<svg class="size-6" viewBox="0 0 24 24" fill="currentColor">'
        '<path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 '
        '2.89 0 01-2.88 2.88 2.89 2.89 0 01-2.88-2.88 2.89 2.89 0 '
        '012.88-2.88c.28 0 .56.04.82.1v-3.49a6.37 6.37 0 00-.82-.05A6.34 '
        '6.34 0 003.15 15.7 6.34 6.34 0 009.49 22a6.34 6.34 0 '
        '006.34-6.34V9.05a8.27 8.27 0 004.84 1.56V7.12a4.83 4.83 0 '
        '01-1.08-.43z"/></svg>'
    )

    has_account_selection = False

    def get_authorize_redirect(self, request, oauth_client):
        """
        TikTok uses 'client_key' instead of the standard 'client_id' parameter.
        Build the authorization URL manually.
        """
        client_config = django_settings.AUTHLIB_OAUTH_CLIENTS.get('tiktok', {})
        state = secrets.token_urlsafe(32)
        request.session['tiktok_oauth_state'] = state

        params = {
            'client_key': client_config.get('client_id', ''),
            'response_type': 'code',
            'scope': client_config.get('client_kwargs', {}).get('scope', ''),
            'redirect_uri': self.get_callback_url(request),
            'state': state,
        }
        authorize_url = f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params, quote_via=quote)}"
        return redirect(authorize_url)

    def handle_callback(self, request):
        """
        Exchange the authorization code for access and refresh tokens
        using TikTok's v2 token endpoint.
        """
        code = request.GET.get('code', '')
        client_config = django_settings.AUTHLIB_OAUTH_CLIENTS.get('tiktok', {})

        resp = http_requests.post(
            f'{TIKTOK_API_BASE}/oauth/token/',
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            data={
                'client_key': client_config.get('client_id', ''),
                'client_secret': client_config.get('client_secret', ''),
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': self.get_callback_url(request),
            },
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()
        return token_data

    def list_accounts(self, token_data):
        """Fetch the authenticated TikTok user's basic info."""
        access_token = token_data.get('access_token', '')
        open_id = token_data.get('open_id', '')

        # Try to fetch user profile; fall back to open_id if it fails
        display_name = ''
        username = ''
        avatar_url = ''
        try:
            resp = http_requests.get(
                f'{TIKTOK_API_BASE}/user/info/',
                headers={'Authorization': f'Bearer {access_token}'},
                params={'fields': 'open_id,display_name,avatar_url'},
                timeout=15,
            )
            if resp.ok:
                data = resp.json().get('data', {}).get('user', {})
                display_name = data.get('display_name', '')
                username = data.get('username', '')
                avatar_url = data.get('avatar_url', '')
        except Exception:
            pass

        return [{
            'id': open_id,
            'name': display_name or f"TikTok ({open_id[:8]}…)",
            'username': username,
            'avatar_url': avatar_url,
        }]

    def save_connection(self, user, selected_account, token_data, project=None):
        expires_in = token_data.get('expires_in')
        token_expires_at = timezone.now() + timedelta(seconds=expires_in) if expires_in else None

        conn, _created = IntegrationConnection.objects.update_or_create(
            project=project,
            provider=self.key,
            external_account_id=selected_account['id'],
            defaults={
                'user': user,
                'provider_category': self.category,
                'external_account_name': selected_account['name'],
                'access_token': token_data.get('access_token', ''),
                'refresh_token': token_data.get('refresh_token', ''),
                'token_expires_at': token_expires_at,
                'scopes': token_data.get('scope', ''),
                'status': IntegrationConnection.ConnectionStatus.ACTIVE,
                'metadata': {
                    'username': selected_account.get('username', ''),
                    'avatar_url': selected_account.get('avatar_url', ''),
                    'open_id': selected_account['id'],
                },
            },
        )
        return conn
