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


class TestGatewayWalletState:
    """Gateway wallet balances must not be overwritten by the Gateway connector's own view."""

    @pytest.fixture
    def accounts_service(self):
        from services.accounts_service import AccountsService

        service = AccountsService.__new__(AccountsService)
        service.accounts_state = {}
        service._connector_service = MagicMock()
        return service

    @pytest.mark.asyncio
    async def test_gateway_connector_does_not_overwrite_wallet_balances(self, accounts_service):
        """A Gateway connector must not replace the full wallet balances with its own tokens."""
        wallet_balances = [
            {"token": "SOL", "units": 0.35, "price": 0.0, "value": 0.0, "available_units": 0.35},
            {"token": "USDC", "units": 1.31, "price": 1.0, "value": 1.31, "available_units": 1.31},
            {"token": "TRUMP", "units": 0.15, "price": 0.0, "value": 0.0, "available_units": 0.15},
        ]

        gateway_connector = MagicMock()
        accounts_service._connector_service.get_all_trading_connectors.return_value = {
            "master_account": {"solana-mainnet-beta": gateway_connector}
        }
        accounts_service._connector_service.is_gateway_connector.return_value = True

        async def fake_gateway_update(chain_networks=None):
            accounts_service.accounts_state["master_account"]["solana-mainnet-beta"] = wallet_balances

        accounts_service._update_gateway_balances = fake_gateway_update
        accounts_service._get_connector_tokens_info = AsyncMock(
            return_value=[{"token": "SOL", "units": 0.35}]
        )

        await accounts_service.update_account_state(connector_names=["solana-mainnet-beta"])

        accounts_service._get_connector_tokens_info.assert_not_called()
        assert accounts_service.accounts_state["master_account"]["solana-mainnet-beta"] == wallet_balances

    @pytest.mark.asyncio
    async def test_gateway_state_mirrored_to_other_accounts(self, accounts_service):
        """Non-master accounts with a Gateway connector get the same wallet balances."""
        wallet_balances = [{"token": "TRUMP", "units": 0.15, "price": 0.0, "value": 0.0}]

        accounts_service._connector_service.get_all_trading_connectors.return_value = {
            "trader_account": {"solana-mainnet-beta": MagicMock()}
        }
        accounts_service._connector_service.is_gateway_connector.return_value = True

        async def fake_gateway_update(chain_networks=None):
            accounts_service.accounts_state.setdefault("master_account", {})
            accounts_service.accounts_state["master_account"]["solana-mainnet-beta"] = wallet_balances

        accounts_service._update_gateway_balances = fake_gateway_update

        await accounts_service.update_account_state()

        assert accounts_service.accounts_state["trader_account"]["solana-mainnet-beta"] == wallet_balances


class TestSharedWalletIdentity:
    """A unified exchange account is recognised by the connectors' account identity (issue #250)."""

    @staticmethod
    def _service(connectors):
        from services.accounts_service import AccountsService
        from services.portfolio_analytics_service import PortfolioAnalyticsService

        service = AccountsService.__new__(AccountsService)
        service._connector_service = MagicMock()
        service._connector_service.get_all_trading_connectors.return_value = {"master_account": connectors}
        service.portfolio_analytics_service = PortfolioAnalyticsService()
        row = {"token": "USDT", "units": 100.0, "price": 1.0, "value": 100.0, "available_units": 100.0}
        service.accounts_state = {"master_account": {name: [dict(row)] for name in connectors}}
        return service

    @staticmethod
    def _connector(account_group_id):
        # spec'd, so that a connector without the attribute behaves like one (no auto-created mock attribute)
        connector = MagicMock(spec=["account_group_id"] if account_group_id is not ... else [])
        if account_group_id is not ...:
            connector.account_group_id = account_group_id
        return connector

    def test_connectors_of_one_unified_account_share_a_group(self):
        service = self._service({
            "bitget": self._connector("bitget_uta:abc"),
            "bitget_perpetual": self._connector("bitget_uta:abc"),
            "binance": self._connector(None),
            "kraken": self._connector(...),
        })

        assert service.get_account_groups() == {
            "master_account": {"bitget": "bitget_uta:abc", "bitget_perpetual": "bitget_uta:abc"}
        }
        assert service.get_account_distribution()["total_portfolio_value"] == 300.0
        assert service.get_portfolio_distribution()["total_portfolio_value"] == 300.0

    def test_separate_wallets_with_equal_balances_are_both_counted(self):
        # Same balances, but the connectors do not report one account: a Classic account, two API keys of
        # different accounts, or a connector that predates account_group_id
        for spot, perp in [(None, None), ("bitget_uta:abc", "bitget_uta:xyz"), (..., ...), (MagicMock(), MagicMock())]:
            service = self._service({"bitget": self._connector(spot), "bitget_perpetual": self._connector(perp)})

            groups = service.get_account_groups().get("master_account", {})
            assert groups in ({}, {"bitget": "bitget_uta:abc", "bitget_perpetual": "bitget_uta:xyz"})
            assert service.get_account_distribution()["total_portfolio_value"] == 200.0
            assert service.get_portfolio_distribution()["total_portfolio_value"] == 200.0

    @pytest.mark.asyncio
    async def test_distribution_route_keeps_a_selected_connector_of_a_shared_wallet(self):
        from models.trading import PortfolioDistributionFilterRequest
        from routers.portfolio import get_portfolio_distribution

        service = self._service({
            "bitget": self._connector("bitget_uta:abc"),
            "bitget_perpetual": self._connector("bitget_uta:abc"),
        })

        for selected, expected in [(["bitget_perpetual"], 100.0), (["bitget"], 100.0),
                                   (["bitget", "bitget_perpetual"], 100.0), (None, 100.0)]:
            request = PortfolioDistributionFilterRequest(connector_names=selected)
            result = await get_portfolio_distribution(request, service)

            assert result["total_portfolio_value"] == expected
            connectors = result["distribution"][0]["accounts"]["master_account"]["connectors"]
            assert list(connectors) == [selected[0] if selected else "bitget"]
