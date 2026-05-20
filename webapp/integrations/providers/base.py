from abc import ABC, abstractmethod

from django.shortcuts import redirect
from django.urls import reverse


class BaseProvider(ABC):
    """Abstract base class for all integration providers."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Unique provider key, e.g. 'facebook'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'Facebook'."""

    @property
    @abstractmethod
    def category(self) -> str:
        """Provider category matching IntegrationConnection.ProviderCategory values."""

    @property
    @abstractmethod
    def icon_svg(self) -> str:
        """Inline SVG markup for the provider icon."""

    @property
    def has_account_selection(self) -> bool:
        """Whether the provider needs a page/account selection step after OAuth."""
        return False

    def get_callback_url(self, request):
        """Build the absolute callback URL for this provider."""
        path = reverse('integrations:integration_callback', kwargs={'provider': self.key})
        return request.build_absolute_uri(path)

    def get_authorize_redirect(self, request, oauth_client):
        """
        Return an HTTP redirect response to the provider's authorization page.
        Override for providers with non-standard OAuth parameters.
        """
        redirect_uri = self.get_callback_url(request)
        return oauth_client.authorize_redirect(request, redirect_uri)

    @abstractmethod
    def handle_callback(self, request):
        """
        Exchange the authorization code for tokens.
        Returns a dict of token data.
        """

    @abstractmethod
    def list_accounts(self, token_data: dict) -> list[dict]:
        """
        Return a list of selectable accounts / pages / destinations.
        Each dict should have at least 'id' and 'name'.
        """

    @abstractmethod
    def save_connection(self, user, selected_account: dict, token_data: dict, project=None):
        """
        Persist the selected account as an IntegrationConnection.
        Returns the created IntegrationConnection instance.
        """
