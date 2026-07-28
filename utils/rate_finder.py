"""
Cross-rate finder for the in-house ticker pool.

Ported from hummingbot's ``hummingbot.core.rate_oracle.utils.find_rate`` so the API can
compute a rate for any ``BASE-QUOTE`` pair directly from a dictionary of collected ticker
prices, without depending on the global ``RateOracle``. The algorithm resolves a rate via,
in order: a direct lookup, the reverse pair (reciprocal), or a bridged cross-rate through
any intermediate pair that shares the base or quote asset.

The helper functions (``combine_to_hb_trading_pair``, ``split_hb_trading_pair`` and
``unwrap_token_symbol``) are reused from hummingbot to keep trading-pair formatting and
token normalization identical to the rest of the stack.
"""
from decimal import Decimal
from typing import Dict, Optional

from hummingbot.connector.utils import combine_to_hb_trading_pair, split_hb_trading_pair
from hummingbot.core.rate_oracle.utils import unwrap_token_symbol


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
