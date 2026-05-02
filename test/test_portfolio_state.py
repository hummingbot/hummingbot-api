"""
Tests for Portfolio State refresh behavior.

Run with: pytest test/test_portfolio_state.py -v
"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("hummingbot")


class TestPortfolioStateRefresh:
    """Tests for portfolio state refresh behavior."""

    @pytest.mark.asyncio
    async def test_refresh_true_calls_update_account_state(self):
        """refresh=True should call update_account_state."""
        from models.trading import PortfolioStateFilterRequest
        from routers.portfolio import get_portfolio_state

        mock_service = MagicMock()
        mock_service.update_account_state = AsyncMock()
        mock_service.get_accounts_state.return_value = {}

        request = PortfolioStateFilterRequest(refresh=True)
        await get_portfolio_state(request, mock_service)

        mock_service.update_account_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_false_does_not_call_update_account_state(self):
        """refresh=False should NOT call update_account_state."""
        from models.trading import PortfolioStateFilterRequest
        from routers.portfolio import get_portfolio_state

        mock_service = MagicMock()
        mock_service.update_account_state = AsyncMock()
        mock_service.get_accounts_state.return_value = {}

        request = PortfolioStateFilterRequest(refresh=False)
        await get_portfolio_state(request, mock_service)

        mock_service.update_account_state.assert_not_called()


class TestBalanceRefresh:
    """Tests for _get_connector_tokens_info balance refresh."""

    @pytest.fixture
    def accounts_service(self):
        """Create AccountsService with mocked dependencies."""
        from services.accounts_service import AccountsService

        service = AccountsService.__new__(AccountsService)
        service._market_data_service = MagicMock()
        service._market_data_service.get_rate.return_value = Decimal("1")
        return service

    @pytest.fixture
    def mock_connector(self):
        """Create a mock connector."""
        connector = MagicMock()
        connector._update_balances = AsyncMock()
        connector.get_all_balances.return_value = {"USDT": Decimal("1000")}
        connector.get_available_balance.return_value = Decimal("1000")
        return connector

    @pytest.mark.asyncio
    async def test_calls_update_balances(self, accounts_service, mock_connector):
        """_get_connector_tokens_info should call _update_balances."""
        await accounts_service._get_connector_tokens_info(mock_connector, "okx")

        mock_connector._update_balances.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_update_balances_when_requested(self, accounts_service, mock_connector):
        """skip_balance_refresh=True should skip _update_balances."""
        await accounts_service._get_connector_tokens_info(
            mock_connector, "okx", skip_balance_refresh=True
        )

        mock_connector._update_balances.assert_not_called()

    @pytest.mark.asyncio
    async def test_balance_failure_preserves_stale_data(self, accounts_service, mock_connector):
        """_update_balances failure should preserve stale cached data."""
        mock_connector._update_balances = AsyncMock(side_effect=Exception("API error"))
        mock_connector.get_all_balances.return_value = {"USDT": Decimal("500")}

        result = await accounts_service._get_connector_tokens_info(mock_connector, "okx")

        # Should still return data from get_all_balances (stale cache)
        assert len(result) == 1
        assert result[0]["token"] == "USDT"
        assert result[0]["units"] == 500.0
