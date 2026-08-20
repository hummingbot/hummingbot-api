"""A trading pair is base-quote, and a base symbol may contain a hyphen.

Gateway reads a token's symbol off the chain the first time it is used, so symbols are
whatever the mint says rather than what a curated list allows. The first pool that
exercised that recorded its base as `DOGE-1`, and every bare `split("-")` in this service
either raised `too many values to unpack` at the caller — as a 400 naming neither the pair
nor the problem — or silently produced nothing.
"""
import pytest

from utils.trading_pair import InvalidTradingPair, split_trading_pair


@pytest.mark.parametrize(
    "pair,expected",
    [
        ("SOL-USDC", ("SOL", "USDC")),
        # The case found live. `DOGE-1` over `SOL`, not three assets.
        ("DOGE-1-SOL", ("DOGE-1", "SOL")),
        ("ETH-USDT", ("ETH", "USDT")),
        # Splitting from the right is what makes extra hyphens belong to the base.
        ("a-b-c-USDC", ("a-b-c", "USDC")),
    ],
)
def test_the_quote_asset_is_the_last_segment(pair, expected):
    assert split_trading_pair(pair) == expected


@pytest.mark.parametrize("pair", ["SOL", "", "-SOL", "SOL-", "-"])
def test_a_pair_that_is_not_base_quote_is_rejected_by_name(pair):
    with pytest.raises(InvalidTradingPair) as raised:
        split_trading_pair(pair)

    # The message a caller sees has to name the pair; `too many values to unpack` did not,
    # which is what made the live failure unreadable.
    assert repr(pair) in str(raised.value)


def test_the_error_is_a_value_error():
    """The routers map ValueError to a 400, so this keeps that mapping without new code."""
    assert issubclass(InvalidTradingPair, ValueError)


def test_no_bare_two_way_unpack_survives_in_the_service():
    """The defect was one line repeated; this is what stops it being reintroduced.

    Only the service's own code is checked. `bots/controllers/` holds strategy templates
    that are edited and shipped separately.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for directory in ("routers", "services", "utils", "models"):
        for path in (root / directory).rglob("*.py"):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"=\s*[\w.]*(trading_pair|pair)\.split\(\"-\"\)", line):
                    offenders.append(f"{path.relative_to(root)}:{number}")

    assert offenders == [], "bare split('-') on a trading pair: use split_trading_pair"
