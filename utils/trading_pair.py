"""Splitting a trading pair into its two assets.

A pair is written base-quote, which is unambiguous only while no symbol contains a
hyphen. Symbols now come off the chain rather than from a curated list — Gateway reads a
token's name, symbol and decimals from its mint the first time it is used — so they are
whatever the mint says. The first pool that exercised this recorded its base token as
`DOGE-1`, making the pair `DOGE-1-SOL`.

`"DOGE-1-SOL".split("-")` returns three parts, and unpacking three into two raises
`ValueError: too many values to unpack (expected 2)`. That escaped to callers as the HTTP
message, which names neither the pair nor the problem.

Splitting from the right is correct rather than merely more forgiving: the quote asset is
the last segment, so `rsplit("-", 1)` reads `DOGE-1-SOL` as `DOGE-1` over `SOL` — which is
what it means. A quote symbol containing a hyphen is genuinely ambiguous and stays so.
"""


class InvalidTradingPair(ValueError):
    """A pair that cannot be read as base-quote, naming the pair that could not be."""


def split_trading_pair(trading_pair: str) -> tuple[str, str]:
    """Split `base-quote` into its two assets, tolerating a hyphen in the base symbol.

    Raises InvalidTradingPair when there is no hyphen to split on, or when either side is
    empty — `"-SOL"` and `"SOL-"` are malformed rather than merely unusual.
    """
    base, separator, quote = trading_pair.rpartition("-")

    if not separator or not base or not quote:
        raise InvalidTradingPair(
            f"Trading pair {trading_pair!r} is not in base-quote form. "
            "Expected two asset symbols separated by a hyphen, e.g. 'SOL-USDC'."
        )

    return base, quote
