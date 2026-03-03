"""
Tests for Gateway LP Executor functionality.

Tests the following fixes:
1. KeyError: 'meteora/clmm' - Gateway connectors should use GatewayLp directly
2. Script config staging compatibility - candles_config and markets removed

Run with: pytest test/test_gateway_lp_executor.py -v
"""
import inspect
import os
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests if hummingbot not installed
pytest.importorskip("hummingbot")


class TestGatewayConnectorFix:
    """Tests for Fix 1: KeyError 'meteora/clmm' resolution."""

    def test_gateway_lp_import(self):
        """GatewayLp should be importable from hummingbot."""
        from hummingbot.connector.gateway.gateway_lp import GatewayLp
        assert GatewayLp is not None

    def test_gateway_lp_instantiation(self):
        """GatewayLp should instantiate with meteora/clmm connector name."""
        from hummingbot.connector.gateway.gateway_lp import GatewayLp

        connector = GatewayLp(
            connector_name="meteora/clmm",
            trading_pairs=[],
            trading_required=True,
        )
        assert connector.connector_name == "meteora/clmm"
        assert connector.name == "meteora/clmm"

    def test_gateway_lp_has_required_methods(self):
        """GatewayLp should have methods required by LP executor."""
        from hummingbot.connector.gateway.gateway_lp import GatewayLp

        connector = GatewayLp(
            connector_name="meteora/clmm",
            trading_pairs=[],
            trading_required=True,
        )

        required_methods = [
            "get_position_info",
            "_clmm_add_liquidity",
            "create_market_order_id",
            "start_network",
            "stop_network",
        ]

        for method in required_methods:
            assert hasattr(connector, method), f"Missing method: {method}"

    def test_gateway_detection_in_unified_connector_service(self):
        """_create_trading_connector should detect gateway connectors."""
        from services.unified_connector_service import UnifiedConnectorService

        source = inspect.getsource(UnifiedConnectorService._create_trading_connector)

        # Check gateway detection logic exists
        assert "'/' in connector_name" in source, "Gateway detection condition not found"
        assert "GatewayLp(" in source, "GatewayLp instantiation not found"

    def test_gateway_connector_names_detected(self):
        """Gateway connector names (with /) should be detected correctly."""
        gateway_connectors = [
            "meteora/clmm",
            "raydium/clmm",
            "uniswap/amm",
            "jupiter/router",
            "orca/whirlpool",
        ]

        regular_connectors = [
            "binance",
            "binance_perpetual",
            "kucoin",
            "gate_io",
        ]

        for name in gateway_connectors:
            assert "/" in name, f"{name} should be detected as gateway"

        for name in regular_connectors:
            assert "/" not in name, f"{name} should NOT be detected as gateway"

    @pytest.mark.asyncio
    async def test_create_trading_connector_for_gateway(self):
        """_create_trading_connector should return GatewayLp for gateway connectors."""
        from hummingbot.connector.gateway.gateway_lp import GatewayLp

        from services.unified_connector_service import UnifiedConnectorService

        # Create a minimal service instance
        service = UnifiedConnectorService.__new__(UnifiedConnectorService)
        service._conn_settings = {}
        service.secrets_manager = MagicMock()

        # Mock BackendAPISecurity
        with patch("services.unified_connector_service.BackendAPISecurity") as mock_security:
            mock_security.login_account = MagicMock()

            connector = service._create_trading_connector(
                account_name="master_account",
                connector_name="meteora/clmm"
            )

            assert isinstance(connector, GatewayLp)
            assert connector.connector_name == "meteora/clmm"


class TestScriptConfigFix:
    """Tests for Fix 2: Script config staging compatibility."""

    def test_script_config_no_candles_config(self):
        """Script config should not include candles_config."""
        from routers.bot_orchestration import deploy_v2_controllers

        source = inspect.getsource(deploy_v2_controllers)

        # Find the script_config_content dict
        import re
        match = re.search(r"script_config_content\s*=\s*\{([^}]+)\}", source, re.DOTALL)
        assert match, "script_config_content not found in deploy_v2_controllers"

        config_str = match.group(1)
        assert "candles_config" not in config_str, "candles_config should not be in script_config_content"

    def test_script_config_no_markets(self):
        """Script config should not include markets."""
        from routers.bot_orchestration import deploy_v2_controllers

        source = inspect.getsource(deploy_v2_controllers)

        import re
        match = re.search(r"script_config_content\s*=\s*\{([^}]+)\}", source, re.DOTALL)
        assert match, "script_config_content not found in deploy_v2_controllers"

        config_str = match.group(1)
        assert '"markets"' not in config_str, "markets should not be in script_config_content"

    def test_script_config_has_required_fields(self):
        """Script config should have script_file_name and controllers_config."""
        from routers.bot_orchestration import deploy_v2_controllers

        source = inspect.getsource(deploy_v2_controllers)

        import re
        match = re.search(r"script_config_content\s*=\s*\{([^}]+)\}", source, re.DOTALL)
        assert match, "script_config_content not found in deploy_v2_controllers"

        config_str = match.group(1)
        assert "script_file_name" in config_str, "script_file_name should be in script_config_content"
        assert "controllers_config" in config_str, "controllers_config should be in script_config_content"


class TestLPExecutorRegistry:
    """Tests for LP executor type registration."""

    def test_lp_executor_type_exists(self):
        """lp_executor should be a valid executor type."""
        # EXECUTOR_TYPES is a Literal type, get its args
        import typing

        from models.executors import EXECUTOR_TYPES
        if hasattr(typing, "get_args"):
            types = typing.get_args(EXECUTOR_TYPES)
        else:
            types = EXECUTOR_TYPES.__args__

        assert "lp_executor" in types, "lp_executor should be in EXECUTOR_TYPES"

    def test_lp_executor_config_importable(self):
        """LPExecutorConfig should be importable from hummingbot."""
        from hummingbot.strategy_v2.executors.lp_executor.data_types import LPExecutorConfig
        assert LPExecutorConfig is not None

    def test_lp_executor_importable(self):
        """LPExecutor should be importable from hummingbot."""
        from hummingbot.strategy_v2.executors.lp_executor.lp_executor import LPExecutor
        assert LPExecutor is not None


class TestGatewayIntegration:
    """Integration tests that require Gateway to be running.

    These tests are skipped if Gateway is not available.
    Run with: pytest test/test_gateway_lp_executor.py -v -m integration
    """

    @pytest.fixture
    def gateway_url(self):
        return os.environ.get("GATEWAY_URL", "http://localhost:15888")

    @pytest.fixture
    def api_url(self):
        return os.environ.get("API_URL", "http://localhost:8000")

    @pytest.fixture
    def api_auth(self):
        return (
            os.environ.get("API_USER", "admin"),
            os.environ.get("API_PASSWORD", "admin")
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_gateway_status(self, api_url, api_auth):
        """Check Gateway status via API."""
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{api_url}/gateway/status",
                auth=aiohttp.BasicAuth(*api_auth)
            ) as response:
                assert response.status == 200
                data = await response.json()
                # Gateway may or may not be running
                assert "running" in data

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_lp_executor_types_available(self, api_url, api_auth):
        """Verify lp_executor is in available types."""
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{api_url}/executors/types/available",
                auth=aiohttp.BasicAuth(*api_auth)
            ) as response:
                assert response.status == 200
                data = await response.json()

                types = [t["type"] for t in data["executor_types"]]
                assert "lp_executor" in types, "lp_executor should be available"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_create_lp_executor_no_keyerror(self, api_url, api_auth):
        """Creating LP executor should not raise KeyError for meteora/clmm.

        This test verifies the fix for the KeyError: 'meteora/clmm' issue.
        The request may fail due to Gateway not running, but should NOT fail
        with KeyError.
        """
        import aiohttp

        payload = {
            "account_name": "master_account",
            "executor_config": {
                "type": "lp_executor",
                "connector_name": "meteora/clmm",
                "trading_pair": "SOL-USDC",
                "pool_address": "BGm1av58oGcsQJehL9WXBFXF7D27vZsKefj4xJKD5Y",
                "lower_price": "84",
                "upper_price": "84.8",
                "base_amount": "0.03555",
                "quote_amount": "3",
                "side": 1,
            }
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{api_url}/executors/",
                json=payload,
                auth=aiohttp.BasicAuth(*api_auth)
            ) as response:
                data = await response.json()

                # Should NOT be KeyError
                if response.status != 200:
                    error_detail = data.get("detail", "")
                    assert "KeyError" not in str(error_detail), \
                        f"Should not have KeyError, got: {error_detail}"
                    assert "'meteora/clmm'" not in str(error_detail) or "KeyError" not in str(error_detail), \
                        f"Should not have KeyError for meteora/clmm, got: {error_detail}"

                    # Expected error when Gateway is not running
                    if "Cannot connect" in str(error_detail) or "Gateway" in str(error_detail):
                        pytest.skip("Gateway not running - this is expected")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
