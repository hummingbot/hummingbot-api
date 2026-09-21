"""The DexScreener source is only worth having if it is wired into the pricing method correctly.

``services/dexscreener_price_source.py`` is tested on its own; these tests pin the integration
around it in ``GatewayWalletService._fetch_gateway_prices_immediate``, because the ordering there
is the whole design:

* GeckoTerminal is asked first and stays authoritative. DexScreener is asked only for what
  GeckoTerminal left unanswered, so a token GeckoTerminal priced is never re-priced and the two
  are never merged — a DexScreener price is never cross-checked against anything, so letting it
  override the trusted source would be strictly worse than not having it.
* Whatever both indexes miss still falls through to the per-token Gateway quote, which is where
  every token went before this source existed. That path costs one rate-limited Jupiter call per
  token and is what the second source exists to spare.
* A DexScreener failure is absorbed: the outage must not take the refresh down with it.

Measured against the live portfolio with GeckoTerminal forced to answer nothing: the method
priced 23 of 26 holdings — the same 23 it reaches with GeckoTerminal up. Losing the authoritative
source outright cost the refresh nothing, which is the property these tests hold in place.
"""
from decimal import Decimal

import pytest
from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient

from services.gateway_wallet_service import GatewayWalletService

CHAIN = "solana"
NETWORK = "mainnet-beta"


class _FakeGatewayQuotes:
    """Stands in for hummingbot's GatewayHttpClient, pricing per token like the Jupiter path."""

    def __init__(self, quotes=None):
        self._quotes = quotes or {}
        self.asked = []

    async def get_price(self, network, base_asset, quote_asset, amount, side):
        self.asked.append(base_asset)
        if base_asset in self._quotes:
            return {"price": self._quotes[base_asset]}
        raise RuntimeError(f"no route for {base_asset}")


class _StubSource:
    """Stands in for a batch price source, recording which symbols it was asked for."""

    def __init__(self, prices=None, raises=None):
        self._prices = prices or {}
        self._raises = raises
        self.asked = []

    async def fetch_prices(self, chain, network, symbols):
        self.asked = list(symbols)
        if self._raises is not None:
            raise self._raises
        return {s: Decimal(p) for s, p in self._prices.items() if s in symbols}

    async def close(self):
        pass


class _Recorder:
    """Stands in for the market data service, recording published prices."""

    def __init__(self):
        self.published = {}

    def set_price(self, trading_pair, price):
        self.published[trading_pair] = price


@pytest.fixture
def gateway_quotes(monkeypatch):
    fake = _FakeGatewayQuotes()
    monkeypatch.setattr(GatewayHttpClient, "get_instance", staticmethod(lambda *a, **k: fake))
    return fake


def _service(gecko, dexscreener, recorder=None):
    service = GatewayWalletService(gateway_client=object(), market_data_service=recorder)
    service._gecko_source = gecko
    service._dexscreener_source = dexscreener
    return service


async def test_a_token_geckoterminal_missed_is_priced_by_dexscreener(gateway_quotes):
    """The fallback's whole reason to exist: GeckoTerminal answered nothing, so DexScreener
    prices the token and the rate-limited per-token Gateway quote is never spent on it."""
    gecko = _StubSource({})
    dexscreener = _StubSource({"SOL": "116.05"})
    recorder = _Recorder()
    service = _service(gecko, dexscreener, recorder)

    prices = await service._fetch_gateway_prices_immediate(CHAIN, NETWORK, ["SOL"])

    assert prices == {"SOL": Decimal("116.05")}
    assert gateway_quotes.asked == []
    assert recorder.published["SOL-USDC"] == Decimal("116.05")


async def test_geckoterminal_stays_authoritative_and_is_never_re_priced(gateway_quotes):
    """A token GeckoTerminal priced is not even offered to DexScreener, so the trusted source
    can never be overridden by a source nothing cross-checks."""
    gecko = _StubSource({"SOL": "115.9"})
    dexscreener = _StubSource({"SOL": "999"})
    service = _service(gecko, dexscreener)

    prices = await service._fetch_gateway_prices_immediate(CHAIN, NETWORK, ["SOL"])

    assert prices == {"SOL": Decimal("115.9")}
    assert dexscreener.asked == []
    assert gateway_quotes.asked == []


async def test_a_dexscreener_outage_does_not_take_the_refresh_down(gateway_quotes):
    """An unreachable or malformed DexScreener leaves the token to the Gateway quote, exactly
    as it was before this source existed."""
    gateway_quotes._quotes = {"SOL": "116.2"}
    gecko = _StubSource({})
    dexscreener = _StubSource(raises=RuntimeError("probe: simulated outage"))
    service = _service(gecko, dexscreener)

    prices = await service._fetch_gateway_prices_immediate(CHAIN, NETWORK, ["SOL"])

    assert prices == {"SOL": Decimal("116.2")}
    assert gateway_quotes.asked == ["SOL"]


async def test_what_both_indexes_miss_is_still_quoted_per_token(gateway_quotes):
    """The Gateway path has to keep serving the tokens no index knows: the pump.fun stragglers
    that made this worth wiring at all are exactly the ones that survive it."""
    gateway_quotes._quotes = {"Apdowl": "0.000315"}
    gecko = _StubSource({})
    dexscreener = _StubSource({})
    service = _service(gecko, dexscreener)

    prices = await service._fetch_gateway_prices_immediate(CHAIN, NETWORK, ["Apdowl", "OpenClaw"])

    assert prices == {"Apdowl": Decimal("0.000315")}
    assert gateway_quotes.asked == ["Apdowl", "OpenClaw"]


async def test_dexscreener_is_asked_only_for_what_geckoterminal_left(gateway_quotes):
    """The split, in one assertion: the overlap goes to the batch source, the remainder to the
    second source, and neither list contains the other's tokens."""
    gecko = _StubSource({"SOL": "115.9", "RAY": "1.74"})
    dexscreener = _StubSource({"NEAR": "4.25"})
    service = _service(gecko, dexscreener)

    prices = await service._fetch_gateway_prices_immediate(CHAIN, NETWORK, ["SOL", "RAY", "NEAR"])

    assert prices == {"SOL": Decimal("115.9"), "RAY": Decimal("1.74"), "NEAR": Decimal("4.25")}
    assert sorted(dexscreener.asked) == ["NEAR"]
    assert gateway_quotes.asked == []


async def test_usdc_ends_at_one_even_when_a_batch_source_prices_it_off_parity(gateway_quotes):
    """The batch sources run first and will answer for USDC given the chance — GeckoTerminal
    reports a hair under a dollar (measured 0.998). The reserve-asset short-circuit runs after
    them and is the last writer, so the near-parity figure never reaches the portfolio."""
    gecko = _StubSource({"USDC": "0.998"})
    dexscreener = _StubSource({})
    service = _service(gecko, dexscreener)

    prices = await service._fetch_gateway_prices_immediate(CHAIN, NETWORK, ["USDC"])

    assert prices == {"USDC": Decimal("1")}
    assert gateway_quotes.asked == []
