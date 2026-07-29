"""
Cross-rate finder for the in-house ticker pool.

Ported from hummingbot's ``hummingbot.core.rate_oracle.utils.find_rate`` so the API can
compute a rate for any ``BASE-QUOTE`` pair directly from a dictionary of collected ticker
prices, without depending on the global ``RateOracle``. The algorithm resolves a rate via,
in order: a direct lookup, the reverse pair (reciprocal), or a bridged cross-rate through
any intermediate pair that shares the base or quote asset.

The trading-pair helpers (``combine_to_hb_trading_pair`` and ``split_hb_trading_pair``) are
reused from hummingbot. ``unwrap_token_symbol`` is vendored below instead of imported: it has
moved between ``hummingbot.core.gateway.utils`` and ``hummingbot.core.rate_oracle.utils``
across hummingbot versions, so importing it makes the API fail to boot on some installs.
"""
import re
from decimal import Decimal
from typing import Dict, List, Match, Optional, Pattern

from hummingbot.connector.utils import combine_to_hb_trading_pair, split_hb_trading_pair

# W{TOKEN} only applies to a few special tokens. It should NOT match all W-prefixed token names like WAVE or WOW.
CAPITAL_W_SYMBOLS_PATTERN = re.compile(r"^W(BTC|ETH|AVAX|ALBT|XRP|POL)")

# w{TOKEN} generally means a wrapped token on the Ethereum network. e.g. wNXM, wDGLD.
SMALL_W_SYMBOLS_PATTERN = re.compile(r"^w(\w+)")

# {TOKEN}.e generally means a wrapped token on the Avalanche network.
DOT_E_SYMBOLS_PATTERN = re.compile(r"(\w+)\.e$", re.IGNORECASE)

USD_EQUIVALANT_TOKENS = ["USC"]


def unwrap_token_symbol(on_chain_token_symbol: str) -> str:
    """
    Normalize a wrapped/bridged token symbol to its underlying asset (e.g. WBTC -> BTC,
    wNXM -> NXM, USDC.e -> USDC). Vendored from hummingbot to keep normalization identical
    to the rest of the stack without depending on its module layout.
    """
    patterns: List[Pattern] = [
        CAPITAL_W_SYMBOLS_PATTERN,
        SMALL_W_SYMBOLS_PATTERN,
        DOT_E_SYMBOLS_PATTERN
    ]
    for p in patterns:
        m: Optional[Match] = p.search(on_chain_token_symbol)
        if m is not None:
            return m.group(1)

    if on_chain_token_symbol in USD_EQUIVALANT_TOKENS:
        on_chain_token_symbol = "USDT"
    return on_chain_token_symbol


def find_rate(prices: Dict[str, Decimal], pair: str) -> Optional[Decimal]:
    """
    Find the exchange rate for ``pair`` from a dictionary of ``trading_pair -> price``.

    For example, given prices of {"HBOT-USDT": Decimal("100"), "AAVE-USDT": Decimal("50"),
    "USDT-GBP": Decimal("0.75")}:
        - USDT-HBOT -> 1 / 100
        - HBOT-AAVE -> 100 / 50
        - AAVE-HBOT -> 50 / 100
        - HBOT-GBP  -> 100 * 0.75

    Args:
        prices: The dictionary of trading pairs and their prices.
        pair: The trading pair to price, in ``BASE-QUOTE`` format.

    Returns:
        The computed rate as a Decimal, or None if no path through the prices exists.
    """
    if pair in prices:
        return prices[pair]
    base, quote = split_hb_trading_pair(trading_pair=pair)
    base = unwrap_token_symbol(base)
    quote = unwrap_token_symbol(quote)
    if base == quote:
        return Decimal("1")
    # Re-check the direct pair after normalizing (e.g. HBOT-USD -> HBOT-USDT) before
    # attempting reverse-pair or path-bridging lookups.
    normalized_pair = combine_to_hb_trading_pair(base=base, quote=quote)
    if normalized_pair in prices:
        return prices[normalized_pair]
    reverse_pair = combine_to_hb_trading_pair(base=quote, quote=base)
    if reverse_pair in prices and prices[reverse_pair] > Decimal("0"):
        return Decimal("1") / prices[reverse_pair]
    base_prices = {k: v for k, v in prices.items() if k.startswith(f"{base}-")}
    for base_pair, proxy_price in base_prices.items():
        link_quote = split_hb_trading_pair(base_pair)[1]
        link_pair = combine_to_hb_trading_pair(base=link_quote, quote=quote)
        if link_pair in prices:
            return proxy_price * prices[link_pair]
        common_denom_pair = combine_to_hb_trading_pair(base=quote, quote=link_quote)
        if common_denom_pair in prices and prices[common_denom_pair] > Decimal("0"):
            return proxy_price / prices[common_denom_pair]
    return None
