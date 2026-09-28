"""A token with a live price must not be valued at zero just because of how it pairs.

Portfolio valuation prices each holding by asking for one literal market,
``TOKEN-<connector quote>``. On xrpl the quote is RLUSD while most tokens pair against
XRP, so for those tokens that market does not exist, the lookup fails and the holding is
reported at $0.00 — even though ``TOKEN-XRP`` and ``XRP-RLUSD`` both return good prices
on their own. These values feed portfolio totals, so it is not cosmetic.

The cross-rate resolution that would normally cover this (``find_rate``) reads the ticker
pool, and xrpl is in ``UNSUPPORTED_TICKER_CONNECTORS``, so that pool is permanently empty
for this connector. The bridge does the same arithmetic against live prices instead.
"""
import weakref

import pytest

pytest.importorskip("hummingbot")

from decimal import Decimal  # noqa: E402

from services.accounts_service import AccountsService  # noqa: E402

MARKETS = {"FUZZY-XRP", "XRP-RLUSD", "BTC-XRP", "ORPHAN-DOGE"}
PRICES = {"FUZZY-XRP": 0.000050, "XRP-RLUSD": 1.38}


class FakeConnector:
    def __init__(self, markets=MARKETS, prices=None):
        self._markets = markets
        self._prices = PRICES if prices is None else prices
        self.price_calls = []

    async def all_trading_pairs(self):
        return list(self._markets)

    async def _get_last_traded_price(self, trading_pair):
        self.price_calls.append(trading_pair)
        return self._prices.get(trading_pair, 0)


@pytest.fixture
def service():
    svc = AccountsService.__new__(AccountsService)
    svc._bridged_rates = weakref.WeakKeyDictionary()
    svc._leg_prices = weakref.WeakKeyDictionary()
    svc._last_known_prices = {}
    return svc


@pytest.mark.asyncio
async def test_a_token_priced_only_against_xrp_gets_a_real_value(service):
    rate = await service._bridged_price(FakeConnector(), "xrpl", "FUZZY-RLUSD")
    assert rate == Decimal(str(0.000050)) * Decimal(str(1.38))


@pytest.mark.asyncio
async def test_the_worked_example_from_the_bug_report(service):
    """10,208.5 FUZZY at 0.000050 XRP, XRP at $1.38 -> ~$0.70, not $0.00."""
    rate = await service._bridged_price(FakeConnector(), "xrpl", "FUZZY-RLUSD")
    assert float(rate * Decimal("10208.5")) == pytest.approx(0.70, abs=0.01)


@pytest.mark.asyncio
async def test_a_token_with_no_route_is_left_alone(service):
    """ORPHAN only pairs with DOGE and there is no DOGE-RLUSD: no invented number."""
    assert await service._bridged_price(FakeConnector(), "xrpl", "ORPHAN-RLUSD") is None


@pytest.mark.asyncio
async def test_a_dead_leg_does_not_produce_a_zero_price(service):
    """If either leg is unpriced the answer is None, so the caller keeps its own 0
    rather than reporting a confidently wrong 0 from a half-complete bridge."""
    connector = FakeConnector(prices={"FUZZY-XRP": 0.000050})
    assert await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD") is None


@pytest.mark.asyncio
async def test_the_second_lookup_is_served_from_cache(service):
    """A refresh prices every held token; each bridge is two live calls."""
    connector = FakeConnector()
    first = await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD")
    calls_after_first = len(connector.price_calls)
    second = await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD")

    assert second == first
    assert len(connector.price_calls) == calls_after_first


@pytest.mark.asyncio
async def test_a_stale_cache_entry_is_refetched(service):
    connector = FakeConnector()
    await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD")
    stamped_at, rate = service._bridged_rates[connector]["FUZZY-RLUSD"]
    service._bridged_rates[connector]["FUZZY-RLUSD"] = (
        stamped_at - AccountsService.BRIDGED_RATE_TTL - 1,
        rate,
    )

    calls_before = len(connector.price_calls)
    await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD")
    assert len(connector.price_calls) > calls_before


@pytest.mark.asyncio
async def test_two_accounts_on_the_same_exchange_do_not_share_a_rate(service):
    """Each account gets its own connector instance, and two accounts can define
    different custom markets — so the same pair name can be a different asset at a
    different price. Caching by connector name would serve one account's price to the
    other; the cache is keyed on the instance instead."""
    account_a = FakeConnector(prices={"FUZZY-XRP": 0.000050, "XRP-RLUSD": 1.38})
    account_b = FakeConnector(prices={"FUZZY-XRP": 0.000090, "XRP-RLUSD": 1.38})

    rate_a = await service._bridged_price(account_a, "xrpl", "FUZZY-RLUSD")
    rate_b = await service._bridged_price(account_b, "xrpl", "FUZZY-RLUSD")

    assert rate_a != rate_b
    assert rate_b == Decimal(str(0.000090)) * Decimal(str(1.38))
    assert account_b.price_calls, "second account must not be served from the first's cache"


@pytest.mark.asyncio
async def test_a_discarded_connector_does_not_hold_its_entries(service):
    """Connectors are stopped and replaced; their cached rates should go with them
    rather than accumulating for the life of the process."""
    connector = FakeConnector()
    await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD")
    assert len(service._bridged_rates) == 1

    del connector
    import gc

    gc.collect()
    assert len(service._bridged_rates) == 0


@pytest.mark.asyncio
async def test_a_connector_that_cannot_list_its_pairs_is_not_fatal(service):
    class Broken(FakeConnector):
        async def all_trading_pairs(self):
            raise RuntimeError("node pool down")

    assert await service._bridged_price(Broken(), "xrpl", "FUZZY-RLUSD") is None


def test_liquid_links_are_tried_first():
    candidates = AccountsService._bridge_candidates(
        "FUZZY", "RLUSD", {"FUZZY-AAA", "FUZZY-XRP", "FUZZY-ZZZ"}
    )
    assert candidates[0] == "XRP"


def test_the_quote_itself_is_never_a_link():
    """TOKEN-RLUSD is the lookup that already failed; it is not a bridge."""
    assert "RLUSD" not in AccountsService._bridge_candidates(
        "FUZZY", "RLUSD", {"FUZZY-RLUSD", "FUZZY-XRP"}
    )


@pytest.mark.asyncio
async def test_the_shared_link_leg_is_fetched_once_for_the_whole_wallet(service):
    """Every bridged token in a refresh crosses the same link leg — XRP-RLUSD for all of
    them — so fetching it per token doubles the request budget for nothing."""
    connector = FakeConnector(
        markets=MARKETS | {"CORE-XRP", "CSC-XRP"},
        prices={**PRICES, "CORE-XRP": 0.008, "CSC-XRP": 0.00004},
    )

    for token in ("FUZZY", "CORE", "CSC"):
        assert await service._bridged_price(connector, "xrpl", f"{token}-RLUSD") is not None

    assert connector.price_calls.count("XRP-RLUSD") == 1
    assert len(connector.price_calls) == 4  # three base legs, one shared link leg


@pytest.mark.asyncio
async def test_a_starved_link_leg_does_not_zero_the_rest_of_the_wallet(service):
    """The failure this guards against: the connector's rate budget runs out partway
    through a refresh, so the tokens at the back of the queue lose their link leg. With
    the leg fetched once up front they are priced from the value already in hand instead
    of reporting $0.00 — and reporting it every cycle, since a failed lookup is never
    cached and never recovers."""
    connector = FakeConnector(
        markets=MARKETS | {"CORE-XRP"}, prices={**PRICES, "CORE-XRP": 0.008}
    )
    assert await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD") is not None

    # The node stops answering for the link leg, as a drained rate limiter does.
    connector._prices = {"CORE-XRP": 0.008}

    rate = await service._bridged_price(connector, "xrpl", "CORE-RLUSD")
    assert rate == Decimal(str(0.008)) * Decimal(str(1.38))


@pytest.mark.asyncio
async def test_a_stale_link_leg_is_refetched(service):
    connector = FakeConnector()
    await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD")
    stamped_at, price = service._leg_prices[connector]["XRP-RLUSD"]
    service._leg_prices[connector]["XRP-RLUSD"] = (
        stamped_at - AccountsService.BRIDGED_RATE_TTL - 1,
        price,
    )
    service._bridged_rates[connector].clear()

    await service._bridged_price(connector, "xrpl", "FUZZY-RLUSD")
    assert connector.price_calls.count("XRP-RLUSD") == 2


@pytest.mark.asyncio
async def test_two_accounts_do_not_share_a_link_leg_either(service):
    """Same reasoning as the bridged rate: a connector instance is what a configuration
    is scoped to, so one account's leg price must not answer for another's."""
    account_a = FakeConnector(prices={"FUZZY-XRP": 0.000050, "XRP-RLUSD": 1.38})
    account_b = FakeConnector(prices={"FUZZY-XRP": 0.000050, "XRP-RLUSD": 1.61})

    rate_a = await service._bridged_price(account_a, "xrpl", "FUZZY-RLUSD")
    rate_b = await service._bridged_price(account_b, "xrpl", "FUZZY-RLUSD")

    assert rate_a != rate_b
    assert "XRP-RLUSD" in account_b.price_calls
