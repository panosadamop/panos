"""Voice API tests reuse the authenticated API client fixtures."""

from tests.integration.api.conftest import anon_client, auth, client, desktop, mock_ai

__all__ = ["anon_client", "auth", "client", "desktop", "mock_ai"]
