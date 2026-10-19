"""DexScreener prices a token by choosing one pair among many, and the choice is the whole risk.

DexScreener's ``/latest/dex/tokens`` answers with every pair for the requested addresses and no
hint of which one is the market. Ranking them by ``liquidity.usd`` alone picks a pool whose
claimed USD liquidity is nonsense: measured on RAY, the winner was ``RAY/JUP`` at $8538 with
$139M of claimed liquidity, while ``RAY/USDC``, ``RAY/SOL`` and ``RAY/USDT`` all agreed on
$1.72-1.74. That price is then published into the shared pool and feeds the portfolio -- a wrong
price is worse than no price, so the guard has to live in the selection.

The rules these tests pin, each an identity check rather than a filter of taste: only pairs where
the requested token is the BASE, the pair is on the chain that was asked about, the quote is that
chain's own address for a major asset (``_QUOTE_SYMBOLS``, resolved through the Gateway token
list), and the pool holds at least ``_MIN_QUOTE_LIQ_USD``, taking the deepest of those. Everything
else is a fall-through for the Gateway quote, which is where the token already went before this
source existed, so an omission costs nothing.

Two of those rules answer traps the endpoint sets. It serves the same contract address for every
chain that has one -- asked for USDC at 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 it answers for
"ethereum" and "pulsechain" alike -- so without the chain a deeper foreign pool wins and its price
is published for the network that was asked about. And a symbol is not an identity: a pool quoted
in a token that merely calls itself USDC would pass a symbol test and be ranked on its own claimed
liquidity, which is the RAY/JUP error in a different costume.

The liquidity floor and the SOL-quoted case come from a second measurement, on the live
portfolio: baton's only stable-quoted pool held $1272 and read $0.006060, while its deepest SOL
pool read $0.002282 and GeckoTerminal said $0.0023188. The stable pool is the outlier on this
token population, so a rule that trusted stable quotes over SOL would have published a 2.6x
error the moment GeckoTerminal missed that chunk.

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
    _to_decimal,
    _to_float,
)

RAY = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
JUP = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
BATON = "Hg5Ja55T5wESq4vyFoiVCMeHXtGyVA69X2UHq8hgpump"

# The majors as this chain's Gateway token list carries them, and the chain id the endpoint
# reports for it. Every selection below is an identity check against these, so the tests use the
# real values and not stand-ins: a stand-in quote address would pass or fail the wrong rule.
SOL_Q = "So11111111111111111111111111111111111111112"
USDC_Q = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_Q = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
CHAIN = "solana"
# Lowered because the source compares both sides lowered -- EVM addresses arrive checksummed and
# base58 ones are case-significant, so the only safe comparison is on the folded form.
QUOTES = frozenset({SOL_Q.lower(), USDC_Q.lower(), USDT_Q.lower()})
QUOTE_BY_SYMBOL = {"SOL": SOL_Q, "WSOL": SOL_Q, "USDC": USDC_Q, "USDT": USDT_Q}


def _pair(base_address, quote_symbol, liquidity_usd, price_usd, chain_id=CHAIN, quote_address=None):
    if quote_address is None:
        # A major gets its canonical address; anything else deliberately does not, so a test can
        # ask for a thin quote without accidentally naming a real asset.
        quote_address = QUOTE_BY_SYMBOL.get(quote_symbol, f"thin-{quote_symbol.lower()}-address")
    return {
        "chainId": chain_id,
        "baseToken": {"address": base_address, "symbol": "TOKEN"},
        "quoteToken": {"address": quote_address, "symbol": quote_symbol},
        "liquidity": {"usd": liquidity_usd},
        "priceUsd": price_usd,
    }


def _sel(pairs, address):
    """``_select_price`` for the chain under test, so the guards do not repeat at every call."""
    return _select_price(pairs, address, CHAIN, QUOTES)


# --- _select_price: the pure choice -----------------------------------------------------

def test_a_rich_claimed_liquidity_on_a_thin_quote_does_not_win():
    """The measured RAY case: RAY/JUP claims $139M, three stable pools agree on ~1.7."""
    pairs = [
        _pair(RAY, "JUP", 139_000_000, "8538"),
        _pair(RAY, "USDC", 6_200_000, "1.74"),
        _pair(RAY, "USDT", 900_000, "1.73"),
    ]
    assert _sel(pairs, RAY) == Decimal("1.74")


def test_a_thin_stable_pool_does_not_outprice_the_deep_market():
    """The measured baton case: its only stable-quoted pool held $1272 and read $0.006060 while
    the deepest SOL pool read $0.002282 against a true $0.00232. The floor has to drop the thin
    pool, not hand it the token because it is the only stable one."""
    pairs = [
        _pair(BATON, "USDC", 1_272, "0.006060"),
        _pair(BATON, "SOL", 135_451, "0.002282"),
        _pair(BATON, "SOL", 35_230, "0.002289"),
    ]
    assert _sel(pairs, BATON) == Decimal("0.002282")


def test_a_token_with_no_pool_over_the_floor_is_left_unpriced():
    """Anything the rule declines falls through to the Gateway quote, which is where the token
    already went before this source existed."""
    pairs = [_pair(BATON, "USDC", 1_272, "0.006060")]
    assert _sel(pairs, BATON) is None


def test_a_pool_quoted_in_another_thin_token_does_not_win():
    """JUP/MET said $1509 while JUP/SOL said $0.3068 -- MET is just another token, so ranking by
    its claimed liquidity picks a price that is not JUP's."""
    pairs = [
        _pair(JUP, "MET", 4_000_000, "1509"),
        _pair(JUP, "SOL", 900_000, "0.3068"),
    ]
    assert _sel(pairs, JUP) == Decimal("0.3068")


def test_a_deeper_pool_on_another_chain_does_not_win():
    """The endpoint serves the same contract address for every chain that has one: asked for USDC
    at 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 it answers for "ethereum" and "pulsechain"
    alike. Ranking the two together puts another network's price on the holding."""
    pairs = [
        _pair(RAY, "USDC", 900_000_000, "8538", chain_id="pulsechain"),
        _pair(RAY, "USDC", 6_200_000, "1.72"),
    ]
    assert _sel(pairs, RAY) == Decimal("1.72")


def test_a_pair_with_no_chain_id_is_not_taken_on_trust():
    """An unverifiable chain is a chain that cannot be ruled out."""
    pairs = [_pair(RAY, "USDC", 6_200_000, "1.74", chain_id="")]
    assert _sel(pairs, RAY) is None


def test_a_pool_quoted_in_a_token_that_calls_itself_usdc_does_not_win():
    """The measured trap from the other side: the quote has to be this chain's own USDC, not a
    token that merely named itself USDC. Symbols are not identities, so a symbol test would let
    this pool -- ranked on its own claimed liquidity -- publish a price that is not RAY's."""
    pairs = [
        _pair(RAY, "USDC", 900_000_000, "8538", quote_address="fake-usdc-mint"),
        _pair(RAY, "USDC", 6_200_000, "1.74"),
    ]
    assert _sel(pairs, RAY) == Decimal("1.74")


def test_a_quote_is_matched_by_address_regardless_of_casing():
    """EVM addresses come back checksummed while the Gateway token list holds them lowered."""
    pairs = [_pair(RAY, "USDC", 6_200_000, "1.74", quote_address=USDC_Q.upper())]
    assert _sel(pairs, RAY) == Decimal("1.74")


def test_pairs_where_the_token_is_the_quote_are_ignored():
    """A token is also the quote of other tokens' pairs, at a price that is not its own."""
    pairs = [_pair("some-other-mint", "RAY", 50_000_000, "0.000001")]
    assert _sel(pairs, RAY) is None


def test_address_matching_is_case_insensitive():
    pairs = [_pair(RAY.upper(), "USDC", 6_200_000, "1.74")]
    assert _sel(pairs, RAY.lower()) == Decimal("1.74")


@pytest.mark.parametrize("liquidity", [9_999.99, 0, None, "not-a-number"])
def test_a_pool_under_the_liquidity_floor_or_with_no_readable_liquidity_is_skipped(liquidity):
    assert _sel([_pair(RAY, "USDC", liquidity, "1.74")], RAY) is None


@pytest.mark.parametrize("price", [None, "", "nan", "Infinity"])
def test_an_unpriceable_pair_is_skipped_rather_than_raised_on(price):
    """`priceUsd` is null on a pair that never traded; `nan` must not escape as a Decimal."""
    assert _sel([_pair(RAY, "USDC", 6_200_000, price)], RAY) is None


def test_malformed_pairs_are_ignored_rather_than_raised_on():
    """A JSON shape the API is not contracted to keep must not take the batch down."""
    pairs = ["not-a-dict", {}, {"baseToken": None, "quoteToken": None}, _pair(RAY, "USDC", 20_000, "1.74")]
    assert _sel(pairs, RAY) == Decimal("1.74")


@pytest.mark.parametrize("value,expected", [("1200.5", 1200.5), (0, 0.0)])
def test_finite_floats_parse(value, expected):
    assert _to_float(value) == expected


@pytest.mark.parametrize("value", [None, "", float("nan"), float("inf"), "abc"])
def test_non_finite_floats_are_rejected(value):
    assert _to_float(value) is None


@pytest.mark.parametrize("value", [float("nan"), "nan", float("inf"), float("-inf"), "Infinity", 0, -1, None, ""])
def test_an_unpriceable_value_yields_none_instead_of_raising(value):
    """This module owns its parsing rather than borrowing GeckoTerminal's, so the guard has to
    hold here on its own: ``Decimal('nan') > 0`` raises, and a raise mid-batch would drop every
    token the batch had already priced."""
    assert _to_decimal(value) is None


@pytest.mark.parametrize("value,expected", [("1.74", Decimal("1.74")), (0.00003123, Decimal("3.123e-05"))])
def test_a_positive_price_parses(value, expected):
    assert _to_decimal(value) == expected


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


def _gateway_map(**tokens):
    """The Gateway token list a real install answers with: the majors, plus the test's tokens.

    The majors have to be here or nothing is priced at all: the source resolves the canonical
    quote addresses out of this same list, and a list without them is a list whose quotes cannot
    be identified."""
    return {**QUOTE_BY_SYMBOL, **tokens}


async def test_a_price_is_mapped_back_to_the_symbol_that_was_asked_for():
    source = _source({RAY: [_pair(RAY, "USDC", 6_200_000, "1.74")]}, _gateway_map(RAY=RAY))
    assert await source.fetch_prices("solana", "mainnet-beta", ["RAY"]) == {"RAY": Decimal("1.74")}


async def test_a_symbol_with_no_gateway_address_is_omitted():
    source = _source({RAY: [_pair(RAY, "USDC", 6_200_000, "1.74")]}, _gateway_map(RAY=RAY))
    assert await source.fetch_prices("solana", "mainnet-beta", ["RAY", "NOT-A-TOKEN"]) == {"RAY": Decimal("1.74")}


async def test_a_lost_chunk_does_not_lose_the_others():
    """The endpoint caps its answer at 30 pairs in total, so a chunk can come back empty or
    fail outright; the tokens in the surviving chunks must still be priced. The chunk that
    fails is lost whole, which is why the request is split in the first place."""
    # The holdings asked about are the T's: the majors are in the token list because the quote
    # addresses are resolved out of it, but asking for them too would shift the chunk boundaries
    # and put the failing address in with tokens that must survive.
    symbols = _gateway_map(**{f"T{i}": f"addr{i}" for i in range(5)}, LOST="addr-lost")
    pairs = {f"addr{i}": [_pair(f"addr{i}", "USDC", 6_200_000, str(i + 1))] for i in range(5)}
    source = _source(pairs, symbols, failing_addresses={"addr-lost"})

    result = await source.fetch_prices(
        "solana", "mainnet-beta", [f"T{i}" for i in range(5)] + ["LOST"]
    )

    assert result == {f"T{i}": Decimal(str(i + 1)) for i in range(5)}


async def test_a_wide_request_is_split_into_several_calls():
    """Addresses per request is capped well below the 30-pair answer limit, so every address
    still gets a pair rather than being dropped off the end of one big response."""
    addresses = _gateway_map(**{f"T{i}": f"addr{i}" for i in range(12)})
    pairs = {f"addr{i}": [_pair(f"addr{i}", "USDC", 6_200_000, str(i + 1))] for i in range(12)}
    source = _source(pairs, addresses)

    result = await source.fetch_prices("solana", "mainnet-beta", list(addresses))

    assert len(source._client.calls) > 1
    assert all(len(call) <= 5 for call in source._client.calls)
    assert result == {f"T{i}": Decimal(str(i + 1)) for i in range(12)}


async def test_a_total_failure_yields_an_empty_dict_instead_of_raising():
    """Never raises: the caller falls back to the per-token Gateway quote on any failure."""
    source = _source({}, _gateway_map(RAY=RAY), failing_addresses={RAY})
    assert await source.fetch_prices("solana", "mainnet-beta", ["RAY"]) == {}


async def test_a_network_with_no_verified_chain_id_is_not_priced_at_all():
    """A chain that cannot be named cannot be verified, and an unverified pool can be the deepest
    one. The token falls through to the Gateway quote, which is where it went before this source
    existed -- so the API is not even asked."""
    source = _source({RAY: [_pair(RAY, "USDC", 6_200_000, "1.74")]}, _gateway_map(RAY=RAY))
    assert await source.fetch_prices("solana", "devnet", ["RAY"]) == {}
    assert source._client.calls == []


async def test_no_symbols_asks_the_api_nothing():
    source = _source({RAY: []}, {})
    assert await source.fetch_prices("solana", "mainnet-beta", []) == {}
