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

    def __init__(self, trading_pairs=None):
        self._trading_pairs = trading_pairs
        self._throttler = AsyncThrottler(rate_limits=self._build_limits(trading_pairs or []))
        self._trading_rules = {}
        self.rules_fetch_count = 0

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


def test_minimal_connector_does_not_raise():
    class Minimal:
        pass

    minimal = Minimal()
    _run(UnifiedConnectorService.sync_pair_derived_state(minimal, "BTC-USDT"))
    assert minimal._trading_pairs == ["BTC-USDT"]


def test_rules_fetch_failure_is_swallowed():
    connector = FakePerPairRulesConnector(trading_pairs=[])

    async def broken_update():
        connector.rules_fetch_count += 1
        raise ConnectionError("node unreachable")

    connector._update_trading_rules = broken_update
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))

    assert connector._trading_pairs == ["USDC-XRP"]  # registration still happened
    assert connector.rules_fetch_count == 1
