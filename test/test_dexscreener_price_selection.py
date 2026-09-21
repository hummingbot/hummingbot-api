"""DexScreener prices a token by choosing one pair among many, and the choice is the whole risk.

DexScreener's ``/latest/dex/tokens`` answers with every pair for the requested addresses and no
hint of which one is the market. Ranking them by ``liquidity.usd`` alone picks a pool whose
claimed USD liquidity is nonsense: measured on RAY, the winner was ``RAY/JUP`` at $8538 with
$139M of claimed liquidity, while ``RAY/USDC``, ``RAY/SOL`` and ``RAY/USDT`` all agreed on
$1.72-1.74. That price is then published into the shared pool and feeds the portfolio -- a wrong
price is worse than no price, so the guard has to live in the selection.

The rule these tests pin: only pairs where the requested token is the BASE, the quote is a
stable (``_QUOTE_SYMBOLS``), and the pool holds at least ``_MIN_QUOTE_LIQ_USD``, taking the
deepest of those. Everything else is a fall-through for the Gateway quote, which is where the
token already went before this source existed, so an omission costs nothing.

Two more measured traps are pinned here: the endpoint caps its answer at 30 pairs in TOTAL (not
per address), so a wide batch silently drops addresses -- hence the small chunk size and the
test that a lost chunk does not lose the others; and ``priceUsd`` can be ``null`` on a pair that
has never traded, the same trap the ``nan`` was for GeckoTerminal.
"""
from decimal import Decimal

import httpx
import pytest

from services.dexscreener_price_source import (
    DexScreenerPriceSource,
    _select_price,
    _to_float,
)

RAY = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def _pair(base_address, quote_symbol, liquidity_usd, price_usd):
    return {
        "baseToken": {"address": base_address, "symbol": "TOKEN"},
        "quoteToken": {"address": "quote-address", "symbol": quote_symbol},
        "liquidity": {"usd": liquidity_usd},
        "priceUsd": price_usd,
    }


# --- _select_price: the pure choice -----------------------------------------------------

def test_a_rich_claimed_liquidity_on_a_thin_quote_does_not_win():
    """The measured RAY case: RAY/JUP claims $139M, three stable pools agree on ~1.7."""
    pairs = [
        _pair(RAY, "JUP", 139_000_000, "8538"),
        _pair(RAY, "USDC", 6_200_000, "1.74"),
        _pair(RAY, "USDT", 900_000, "1.73"),
    ]
    assert _select_price(pairs, RAY) == Decimal("1.74")


def test_a_token_quoted_only_in_a_non_stable_and_in_another_token_is_left_unpriced():
    """JUP/MET said $1509 while JUP/SOL said $0.3068 -- neither quote is a stable, so neither is
    trusted: the token falls through to the Gateway quote rather than being priced from a pool
    whose own USD liquidity is what made it rank first."""
    pairs = [
        _pair(JUP, "MET", 4_000_000, "1509"),
        _pair(JUP, "SOL", 900_000, "0.3068"),
    ]
    assert _select_price(pairs, JUP) is None


def test_pairs_where_the_token_is_the_quote_are_ignored():
    """A token is also the quote of other tokens' pairs, at a price that is not its own."""
    pairs = [_pair("some-other-mint", "RAY", 50_000_000, "0.000001")]
    assert _select_price(pairs, RAY) is None


def test_address_matching_is_case_insensitive():
    pairs = [_pair(RAY.upper(), "USDC", 6_200_000, "1.74")]
    assert _select_price(pairs, RAY.lower()) == Decimal("1.74")


@pytest.mark.parametrize("liquidity", [999.99, 0, None, "not-a-number"])
def test_a_pool_under_the_liquidity_floor_or_with_no_readable_liquidity_is_skipped(liquidity):
    assert _select_price([_pair(RAY, "USDC", liquidity, "1.74")], RAY) is None


@pytest.mark.parametrize("price", [None, "", "nan", "Infinity"])
def test_an_unpriceable_pair_is_skipped_rather_than_raised_on(price):
    """`priceUsd` is null on a pair that never traded; `nan` must not escape as a Decimal."""
    assert _select_price([_pair(RAY, "USDC", 6_200_000, price)], RAY) is None


def test_malformed_pairs_are_ignored_rather_than_raised_on():
    """A JSON shape the API is not contracted to keep must not take the batch down."""
    pairs = ["not-a-dict", {}, {"baseToken": None, "quoteToken": None}, _pair(RAY, "USDC", 2_000, "1.74")]
    assert _select_price(pairs, RAY) == Decimal("1.74")


@pytest.mark.parametrize("value,expected", [("1200.5", 1200.5), (0, 0.0)])
def test_finite_floats_parse(value, expected):
    assert _to_float(value) == expected


@pytest.mark.parametrize("value", [None, "", float("nan"), float("inf"), "abc"])
def test_non_finite_floats_are_rejected(value):
    assert _to_float(value) is None


# --- fetch_prices: the contract with the caller -----------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttp:
    """Stands in for httpx.AsyncClient, answering from a per-address pair map."""

    def __init__(self, pairs_by_address, failing_addresses=()):
        self._pairs_by_address = pairs_by_address
        self._failing = set(failing_addresses)
        self.calls = []

    async def get(self, url):
        addresses = url.rsplit("/", 1)[-1].split(",")
        self.calls.append(addresses)
        if any(a in self._failing for a in addresses):
            raise httpx.ConnectError("boom")
        pairs = []
        for address in addresses:
            pairs.extend(self._pairs_by_address.get(address, []))
        return _FakeResponse({"pairs": pairs})


class _FakeGateway:
    """Stands in for the Gateway client, answering the token-list lookup only."""

    def __init__(self, symbol_to_address):
        self._symbol_to_address = symbol_to_address

    async def get_tokens(self, chain, network):
        return {"tokens": [{"symbol": s, "address": a} for s, a in self._symbol_to_address.items()]}


def _source(pairs_by_address, symbol_to_address, failing_addresses=()):
    source = DexScreenerPriceSource(_FakeGateway(symbol_to_address))
    source._client = _FakeHttp(pairs_by_address, failing_addresses)
    return source


async def test_a_price_is_mapped_back_to_the_symbol_that_was_asked_for():
    source = _source({RAY: [_pair(RAY, "USDC", 6_200_000, "1.74")]}, {"RAY": RAY})
    assert await source.fetch_prices("solana", "mainnet-beta", ["RAY"]) == {"RAY": Decimal("1.74")}


async def test_a_symbol_with_no_gateway_address_is_omitted():
    source = _source({RAY: [_pair(RAY, "USDC", 6_200_000, "1.74")]}, {"RAY": RAY})
    assert await source.fetch_prices("solana", "mainnet-beta", ["RAY", "NOT-A-TOKEN"]) == {"RAY": Decimal("1.74")}


async def test_a_lost_chunk_does_not_lose_the_others():
    """The endpoint caps its answer at 30 pairs in total, so a chunk can come back empty or
    fail outright; the tokens in the surviving chunks must still be priced. The chunk that
    fails is lost whole, which is why the request is split in the first place."""
    symbols = {f"T{i}": f"addr{i}" for i in range(5)}
    symbols["LOST"] = "addr-lost"
    pairs = {f"addr{i}": [_pair(f"addr{i}", "USDC", 6_200_000, str(i + 1))] for i in range(5)}
    source = _source(pairs, symbols, failing_addresses={"addr-lost"})

    result = await source.fetch_prices("solana", "mainnet-beta", list(symbols))

    assert result == {f"T{i}": Decimal(str(i + 1)) for i in range(5)}


async def test_a_wide_request_is_split_into_several_calls():
    """Addresses per request is capped well below the 30-pair answer limit, so every address
    still gets a pair rather than being dropped off the end of one big response."""
    addresses = {f"T{i}": f"addr{i}" for i in range(12)}
    pairs = {f"addr{i}": [_pair(f"addr{i}", "USDC", 6_200_000, str(i + 1))] for i in range(12)}
    source = _source(pairs, addresses)

    result = await source.fetch_prices("solana", "mainnet-beta", list(addresses))

    assert len(source._client.calls) > 1
    assert all(len(call) <= 5 for call in source._client.calls)
    assert result == {f"T{i}": Decimal(str(i + 1)) for i in range(12)}


async def test_a_total_failure_yields_an_empty_dict_instead_of_raising():
    """Never raises: the caller falls back to the per-token Gateway quote on any failure."""
    source = _source({}, {"RAY": RAY}, failing_addresses={RAY})
    assert await source.fetch_prices("solana", "mainnet-beta", ["RAY"]) == {}


async def test_no_symbols_asks_the_api_nothing():
    source = _source({RAY: []}, {})
    assert await source.fetch_prices("solana", "mainnet-beta", []) == {}
