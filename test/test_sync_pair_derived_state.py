"""Tests for UnifiedConnectorService.sync_pair_derived_state (issues #207 / #208).

Connectors are created with trading_pairs=[] and pairs registered dynamically, but
throttler rate limits and per-pair trading rules are built from that list at init.
The helper must re-sync all of it, idempotently, on every registration.
"""
import asyncio
from decimal import Decimal

from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.api_throttler.data_types import RateLimit

from services.unified_connector_service import UnifiedConnectorService


class FakePerPairRulesConnector:
    """Mimics a connector (bybit-style throttler + XRPL-style per-pair rules)."""

    def __init__(self, trading_pairs=None, trading_required=True):
        self._trading_pairs = trading_pairs
        self._throttler = AsyncThrottler(rate_limits=self._build_limits(trading_pairs or []))
        self._trading_rules = {}
        self._trading_required = trading_required
        self.rules_fetch_count = 0

    @property
    def is_trading_required(self):
        return self._trading_required

    @staticmethod
    def _build_limits(trading_pairs):
        limits = [RateLimit(limit_id="global", limit=10, time_interval=1)]
        for pair in trading_pairs:
            limits.append(RateLimit(limit_id=f"order/create-{pair}", limit=5, time_interval=1))
        return limits

    @property
    def rate_limits_rules(self):
        return self._build_limits(self._trading_pairs or [])

    @property
    def trading_rules(self):
        return self._trading_rules

    async def _update_trading_rules(self):
        """XRPL-style: builds rules only for pairs currently in _trading_pairs."""
        self.rules_fetch_count += 1
        for pair in self._trading_pairs or []:
            self._trading_rules[pair] = {"min_order_size": Decimal("1")}


def _run(coro):
    return asyncio.run(coro)


def test_pair_appended_and_rules_built():
    connector = FakePerPairRulesConnector(trading_pairs=[])
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))

    assert connector._trading_pairs == ["USDC-XRP"]
    assert "USDC-XRP" in connector.trading_rules  # 208: rules refreshed
    assert connector.rules_fetch_count == 1


def test_throttler_learns_pair_scoped_limit_in_place():
    connector = FakePerPairRulesConnector(trading_pairs=[])
    # WebAssistantsFactory captures the throttler instance at init — the fix must
    # mutate that same object, not replace it.
    original_throttler = connector._throttler

    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))

    assert connector._throttler is original_throttler
    limit_ids = {limit.limit_id for limit in original_throttler._rate_limits}
    assert "order/create-BTC-USDT" in limit_ids  # 207: limit added dynamically


def test_idempotent_no_refetch_when_rule_exists():
    connector = FakePerPairRulesConnector(trading_pairs=[])
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))

    assert connector._trading_pairs == ["USDC-XRP"]
    assert connector.rules_fetch_count == 1  # rule present -> no second on-chain fetch
    limit_ids = [limit.limit_id for limit in connector._throttler._rate_limits]
    assert limit_ids.count("order/create-USDC-XRP") == 1


def test_none_trading_pairs_initialized():
    connector = FakePerPairRulesConnector(trading_pairs=None)
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "RLUSD-XRP"))
    assert connector._trading_pairs == ["RLUSD-XRP"]


def test_data_connector_skips_rules_but_syncs_throttler():
    """Data connectors (trading_required=False) never place orders — no rules
    fetch (possibly on-chain, would add latency to order-book bootstrap), but
    their REST fetches share the throttler, so limits must still sync."""
    connector = FakePerPairRulesConnector(trading_pairs=[], trading_required=False)
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))

    assert connector._trading_pairs == ["BTC-USDT"]
    assert connector.rules_fetch_count == 0
    limit_ids = {limit.limit_id for limit in connector._throttler._rate_limits}
    assert "order/create-BTC-USDT" in limit_ids


def test_minimal_connector_does_not_raise():
    class Minimal:
        pass

    minimal = Minimal()
    _run(UnifiedConnectorService.sync_pair_derived_state(minimal, "BTC-USDT"))
    assert minimal._trading_pairs == ["BTC-USDT"]


def test_set_position_mode_refuses_empty_pairs_and_registers_provided_pair():
    """Position-mode implementations iterate connector.trading_pairs and silently
    no-op when the list is empty — the service must refuse loudly instead of
    reporting success, and must register a provided pair before switching."""
    from fastapi import HTTPException
    from hummingbot.core.data_type.common import PositionMode

    from services.perpetual_trading_service import PerpetualTradingService

    class FakePerpConnector(FakePerPairRulesConnector):
        def __init__(self):
            super().__init__(trading_pairs=[])
            self.mode_set = None

        @property
        def trading_pairs(self):
            return self._trading_pairs or []

        def supported_position_modes(self):
            return [PositionMode.HEDGE, PositionMode.ONEWAY]

        def set_position_mode(self, mode):
            self.mode_set = mode

    connector = FakePerpConnector()

    async def provider(account_name, connector_name):
        return connector

    service = PerpetualTradingService(provider)

    # No pair provided and none registered -> loud 400, exchange never touched
    try:
        _run(service.set_position_mode("master", "bybit_perpetual", PositionMode.HEDGE))
        raise AssertionError("expected HTTPException for empty trading_pairs")
    except HTTPException as e:
        assert e.status_code == 400
    assert connector.mode_set is None

    # Pair provided -> registered (with rules/throttler synced), then switched
    result = _run(service.set_position_mode(
        "master", "bybit_perpetual", PositionMode.HEDGE, trading_pair="BTC-USDT"))
    assert connector._trading_pairs == ["BTC-USDT"]
    assert connector.mode_set == PositionMode.HEDGE
    assert result["status"] == "success"


def test_rules_fetch_failure_is_swallowed():
    connector = FakePerPairRulesConnector(trading_pairs=[])

    async def broken_update():
        connector.rules_fetch_count += 1
        raise ConnectionError("node unreachable")

    connector._update_trading_rules = broken_update
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))

    assert connector._trading_pairs == ["USDC-XRP"]  # registration still happened
    assert connector.rules_fetch_count == 1
