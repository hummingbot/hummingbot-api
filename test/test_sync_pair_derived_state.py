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


class FakePairLimitsConnector:
    """Mimics a connector with pair-templated rate limits (bybit-style) and
    per-pair trading rules (XRPL-style)."""

    def __init__(self, trading_pairs=None, trading_required=True):
        self._trading_pairs = trading_pairs
        self._throttler = AsyncThrottler(rate_limits=self._build_limits(trading_pairs or []))
        self._trading_rules = {}
        self._trading_required = trading_required
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
    def is_trading_required(self):
        return self._trading_required

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


def test_pair_appended():
    connector = FakePairLimitsConnector(trading_pairs=[])
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))
    assert connector._trading_pairs == ["BTC-USDT"]


def test_throttler_learns_pair_scoped_limit_in_place():
    connector = FakePairLimitsConnector(trading_pairs=[])
    # WebAssistantsFactory captures the throttler instance at init — the fix must
    # mutate that same object, not replace it.
    original_throttler = connector._throttler

    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))

    assert connector._throttler is original_throttler
    limit_ids = {limit.limit_id for limit in original_throttler._rate_limits}
    assert "order/create-BTC-USDT" in limit_ids


def test_idempotent_no_duplicate_limits():
    connector = FakePairLimitsConnector(trading_pairs=[])
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))

    assert connector._trading_pairs == ["BTC-USDT"]
    limit_ids = [limit.limit_id for limit in connector._throttler._rate_limits]
    assert limit_ids.count("order/create-BTC-USDT") == 1


def test_none_trading_pairs_initialized():
    connector = FakePairLimitsConnector(trading_pairs=None)
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "RLUSD-XRP"))
    assert connector._trading_pairs == ["RLUSD-XRP"]


def test_minimal_connector_does_not_raise():
    class Minimal:
        pass

    minimal = Minimal()
    _run(UnifiedConnectorService.sync_pair_derived_state(minimal, "BTC-USDT"))
    assert minimal._trading_pairs == ["BTC-USDT"]


def test_unknown_pair_rejected_and_not_registered():
    """A pair the connector's symbol map cannot resolve must never enter
    _trading_pairs: there is no rollback, and a poisoned entry breaks per-pair
    status polling and rules rebuilds for the connector's lifetime."""

    class ValidatingConnector(FakePairLimitsConnector):
        KNOWN = {"BTC-USDT"}

        async def exchange_symbol_associated_to_pair(self, trading_pair):
            if trading_pair not in self.KNOWN:
                raise KeyError(trading_pair)
            return trading_pair.replace("-", "")

    connector = ValidatingConnector(trading_pairs=[])

    try:
        _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USD"))
        raise AssertionError("expected ValueError for unknown pair")
    except ValueError:
        pass
    assert connector._trading_pairs == []  # nothing registered, nothing to roll back

    # A valid pair still registers normally afterwards
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))
    assert connector._trading_pairs == ["BTC-USDT"]


def test_already_registered_pair_skips_validation():
    """Re-syncing an already-registered pair must not re-validate — the symbol
    map may be temporarily unavailable, and the pair was validated on entry."""

    class BrokenResolverConnector(FakePairLimitsConnector):
        async def exchange_symbol_associated_to_pair(self, trading_pair):
            raise ConnectionError("symbol map fetch failed")

    connector = BrokenResolverConnector(trading_pairs=["BTC-USDT"])
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))
    assert connector._trading_pairs == ["BTC-USDT"]


def test_rules_built_for_registered_pair():
    connector = FakePairLimitsConnector(trading_pairs=[])
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))

    assert "USDC-XRP" in connector.trading_rules
    assert connector.rules_fetch_count == 1


def test_no_refetch_when_rule_exists():
    connector = FakePairLimitsConnector(trading_pairs=[])
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))

    assert connector.rules_fetch_count == 1  # rule present -> no second on-chain fetch


def test_data_connector_skips_rules_but_syncs_throttler():
    """Data connectors (trading_required=False) never place orders — no rules
    fetch (possibly on-chain, would add latency to order-book bootstrap), but
    their REST fetches share the throttler, so limits must still sync."""
    connector = FakePairLimitsConnector(trading_pairs=[], trading_required=False)
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "BTC-USDT"))

    assert connector._trading_pairs == ["BTC-USDT"]
    assert connector.rules_fetch_count == 0
    limit_ids = {limit.limit_id for limit in connector._throttler._rate_limits}
    assert "order/create-BTC-USDT" in limit_ids


def test_rules_fetch_failure_is_swallowed():
    connector = FakePairLimitsConnector(trading_pairs=[])

    async def broken_update():
        connector.rules_fetch_count += 1
        raise ConnectionError("node unreachable")

    connector._update_trading_rules = broken_update
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "USDC-XRP"))

    assert connector._trading_pairs == ["USDC-XRP"]  # registration still happened
    assert connector.rules_fetch_count == 1


def test_order_book_path_skips_rules_fetch():
    """Market-data paths pass refresh_rules=False: even on a trading connector
    (get_best_connector_for_market prefers them), an order-book bootstrap must
    never pay for a possibly-on-chain trading-rules fetch."""
    connector = FakePairLimitsConnector(trading_pairs=[])
    _run(UnifiedConnectorService.sync_pair_derived_state(
        connector, "USDC-XRP", refresh_rules=False))

    assert connector._trading_pairs == ["USDC-XRP"]
    assert connector.rules_fetch_count == 0
    limit_ids = {limit.limit_id for limit in connector._throttler._rate_limits}
    assert "order/create-USDC-XRP" in limit_ids  # throttler still synced


def test_unknown_pair_cannot_poison_rules_refresh():
    """XRPL's rules fetch iterates ALL registered pairs and raises on the first
    unknown one — so a single bad pair entering _trading_pairs would break rules
    refresh for every valid pair, permanently. Validation must prevent entry."""

    class XrplLikeConnector(FakePairLimitsConnector):
        KNOWN = {"SOLO-XRP", "USDC-XRP"}

        async def exchange_symbol_associated_to_pair(self, trading_pair):
            if trading_pair not in self.KNOWN:
                raise KeyError(trading_pair)
            return trading_pair

        async def _update_trading_rules(self):
            self.rules_fetch_count += 1
            for pair in self._trading_pairs or []:
                if pair not in self.KNOWN:  # faithful: raises before ANY rule lands
                    raise ValueError(f"Market {pair} not found in markets list")
            for pair in self._trading_pairs or []:
                self._trading_rules[pair] = {"min_order_size": Decimal("1")}

    connector = XrplLikeConnector(trading_pairs=[])

    # Typo'd pair (the reviewer's reproduction): rejected, never registered
    try:
        _run(UnifiedConnectorService.sync_pair_derived_state(connector, "XRP-USD"))
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert connector._trading_pairs == []

    # Valid pairs still get rules — refresh is not poisoned
    _run(UnifiedConnectorService.sync_pair_derived_state(connector, "SOLO-XRP"))
    assert "SOLO-XRP" in connector.trading_rules
