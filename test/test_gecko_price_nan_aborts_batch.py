"""One unpriced token must not unprice the whole GeckoTerminal batch.

GeckoTerminal answers a simple-token-price call with a row per address it can price, and
``nan`` for one it cannot (no listed pool). ``_to_decimal`` guarded the Decimal conversion but
not the ``> 0`` comparison that followed, and ``Decimal('nan') > 0`` raises InvalidOperation.
That raise happened mid-loop in ``_fetch_prices``, so it escaped into ``fetch_prices``'s broad
``except`` and turned the whole result into ``{}`` — every token the batch had already priced
was dropped, and each then fell back to a per-token Gateway/Jupiter quote (rate-limited at
scale), surfacing as holdings reported at price 0.0.

These tests pin both halves: ``_to_decimal`` rejects a non-finite value instead of raising, and
a batch that contains one such row still returns the prices for every other token.
"""
from decimal import Decimal

import pytest

from services.gecko_price_source import GeckoPriceSource, _to_decimal

NAN = float("nan")


@pytest.mark.parametrize("value", [NAN, "nan", float("inf"), float("-inf"), "Infinity"])
def test_non_finite_prices_are_rejected_not_raised(value):
    assert _to_decimal(value) is None


@pytest.mark.parametrize("value,expected", [("4.2476", Decimal("4.2476")), (1, Decimal("1"))])
def test_finite_prices_still_parse(value, expected):
    assert _to_decimal(value) == expected


@pytest.mark.parametrize("value", [0, -1, None, ""])
def test_non_positive_prices_are_rejected(value):
    assert _to_decimal(value) is None


class _FakeGateway:
    """Stands in for the Gateway client, answering the token-list lookup only."""

    def __init__(self, symbol_to_address):
        self._symbol_to_address = symbol_to_address

    async def get_tokens(self, chain, network):
        return {"tokens": [{"symbol": s, "address": a} for s, a in self._symbol_to_address.items()]}


class _FakeGecko:
    """Stands in for the GeckoTerminal client, returning one row per requested address."""

    def __init__(self, address_to_price):
        self._address_to_price = address_to_price

    async def get_simple_token_price(self, network, addresses):
        wanted = set(addresses)
        addresses_out = [a for a in self._address_to_price if a in wanted]
        return {
            "token_address": addresses_out,
            "price_usd": [self._address_to_price[a] for a in addresses_out],
        }


async def test_one_nan_row_does_not_drop_the_rest_of_the_batch():
    priced = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    # GeckoTerminal's real answer for this address is ``nan``; it must not abort the batch.
    unpriced = "AR6XsCMFrvRvFdYHQDD5s1CgTndhVfqxEJbHsZtHpump"
    source = GeckoPriceSource(_FakeGateway({"BONK": priced, "DEAD": unpriced}))
    source._client = _FakeGecko({priced: "0.0000123", unpriced: NAN})

    assert await source.fetch_prices("solana", "mainnet-beta", ["BONK", "DEAD"]) == {"BONK": Decimal("0.0000123")}
