"""
DexScreener DEX price source.

Second opinion behind GeckoTerminal (see ``services/gecko_price_source.py``): it prices the
same on-chain (Gateway) tokens, keyed by contract address, but from a different index and a
different provider. GeckoTerminal goes slow and rate-limits under load — measured from this
host, 10-15s responses followed by an HTTP 429 — and every token it fails to answer falls
through to a per-token Gateway quote, i.e. one Jupiter HTTP call each, which rate-limits in
turn and leaves holdings reported at ``price 0.0``. DexScreener answers the same question in
under a second, so this source absorbs that fall-through for every token GeckoTerminal misses.

GeckoTerminal stays authoritative: the caller only asks this source for the tokens GeckoTerminal
did not return. That also means a DexScreener price is never cross-checked against GeckoTerminal
(there is nothing to check it against), so the guard against an absurd price has to live in the
selection below — see ``_select_price``.

Like the GeckoTerminal source, ``fetch_prices`` never raises: any failure yields an empty dict
and the caller simply falls back to Gateway pricing.
"""
import asyncio
import logging
import math
import time
from decimal import Decimal
from typing import Dict, List, Optional

import httpx

from services.gateway_client import GatewayError, check_gateway_error
from services.gecko_price_source import _to_decimal

logger = logging.getLogger(__name__)

_TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens/"

# Addresses per request. The endpoint caps its answer at 30 pairs in TOTAL, not per address,
# so a wide batch silently drops whichever addresses did not make the cut: measured, 10
# addresses came back with only 7 covered (3 vanished with no error at all).
_CHUNK_ADDRESSES = 5
# Quote symbols a price may be read from. Not cosmetic: DexScreener reports a nonsense USD
# liquidity for pools quoted in a thin token, and ranking across those picks them. Measured
# on RAY, the highest-liquidity pair was RAY/JUP at $8538 (claimed $139M liquidity) while
# RAY/USDC, RAY/SOL and RAY/USDT all said $1.72-1.74; on JUP, JUP/MET said $1509 against
# JUP/SOL's $0.3068. Restricting to a stable quote removes the whole class.
_QUOTE_SYMBOLS = ("USDC", "USDT")
# Below this the quoted price is as likely to be a dead pool as a market; skip the token and
# let the Gateway quote have it.
_MIN_QUOTE_LIQ_USD = 1000.0
# How long a chain/network token list (symbol -> address) is cached before refetching.
_TOKEN_LIST_TTL = 3600.0
# Per fetch cycle timeout so a slow DexScreener never stalls a balance refresh.
_FETCH_TIMEOUT = 10.0


def _to_float(value) -> Optional[float]:
    """Parse a finite float, returning None on empty/invalid/non-finite input."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _select_price(pairs: List[Dict], address: str) -> Optional[Decimal]:
    """
    Best USD price for ``address`` among DexScreener ``pairs``, or None if there is none.

    A DexScreener response carries many pairs per token and several tokens per response, so
    the price is a choice, not a lookup. The choice is: among the pairs where the requested
    token is the BASE (a token also appears as the quote of other pairs, at a price that is
    not its own) and the quote is a stable (see ``_QUOTE_SYMBOLS``) and the pool holds at
    least ``_MIN_QUOTE_LIQ_USD``, take the deepest pool. The price may be null on a pair that
    has never traded, which ``_to_decimal`` turns into None like the GeckoTerminal NaN.
    """
    target = address.lower()
    best_liquidity: Optional[float] = None
    best_price: Optional[Decimal] = None

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        base_address = (pair.get("baseToken") or {}).get("address") or ""
        if str(base_address).lower() != target:
            continue
        quote_symbol = ((pair.get("quoteToken") or {}).get("symbol") or "").upper()
        if quote_symbol not in _QUOTE_SYMBOLS:
            continue
        liquidity = _to_float((pair.get("liquidity") or {}).get("usd"))
        if liquidity is None or liquidity < _MIN_QUOTE_LIQ_USD:
            continue
        price = _to_decimal(pair.get("priceUsd"))
        if price is None:
            continue
        if best_liquidity is None or liquidity > best_liquidity:
            best_liquidity = liquidity
            best_price = price

    return best_price


class DexScreenerPriceSource:
    """
    Batched USD price lookups for Gateway tokens via the DexScreener API.

    Used as the second source behind GeckoTerminal: a single async client is reused across
    calls and token lists are cached per ``(chain, network)`` with a TTL, so neither the
    address of a symbol nor the price is refetched on every balance refresh.
    """

    def __init__(self, gateway_client):
        """
        Args:
            gateway_client: ``GatewayClient`` used to fetch token lists (symbol -> address).
        """
        self._gateway_client = gateway_client
        self._client: Optional[httpx.AsyncClient] = None
        # (chain, network) -> (fetched_at, {UPPER_SYMBOL: address})
        self._token_list_cache: Dict[tuple, tuple] = {}

    def _get_client(self) -> Optional[httpx.AsyncClient]:
        """Return the shared HTTP client, creating it on first use."""
        if self._client is None:
            try:
                self._client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
            except Exception as e:  # noqa: BLE001 - pricing must never break the balance refresh
                logger.warning(f"DexScreener client unavailable: {e}")
                return None
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client if it was created."""
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    async def _symbol_to_address(self, chain: str, network: str) -> Dict[str, str]:
        """Return a cached ``{UPPER_SYMBOL: address}`` map for a chain/network from Gateway."""
        key = (chain, network)
        cached = self._token_list_cache.get(key)
        if cached is not None and (time.time() - cached[0]) < _TOKEN_LIST_TTL:
            return cached[1]

        try:
            response = check_gateway_error(await self._gateway_client.get_tokens(chain, network))
        except GatewayError as e:
            # Don't cache on Gateway errors — caching an empty map would silently disable
            # DexScreener pricing for the whole TTL window.
            logger.warning(f"Gateway error fetching token list for {chain}/{network}: {e}")
            return {}
        tokens = response.get("tokens", []) if isinstance(response, dict) else []
        mapping: Dict[str, str] = {}
        for token in tokens:
            symbol = token.get("symbol")
            address = token.get("address")
            if symbol and address:
                mapping[symbol.upper()] = address
        self._token_list_cache[key] = (time.time(), mapping)
        return mapping

    async def _fetch_pairs(self, client: httpx.AsyncClient, addresses: List[str]) -> List[Dict]:
        """Fetch the raw pair list DexScreener returns for up to ``_CHUNK_ADDRESSES`` tokens."""
        response = await client.get(_TOKENS_URL + ",".join(addresses))
        response.raise_for_status()
        payload = response.json()
        pairs = payload.get("pairs") if isinstance(payload, dict) else None
        return pairs if isinstance(pairs, list) else []

    async def fetch_prices(self, chain: str, network: str, symbols: List[str]) -> Dict[str, Decimal]:
        """
        Fetch USD prices for the given token ``symbols`` on a Gateway chain/network.

        Returns ``{symbol: price}`` (original-cased symbols, USD price as Decimal) only for
        the symbols DexScreener could price. Symbols with no known address, no quoted pool, or
        a non-positive price are omitted so the caller can fall back to Gateway for those.
        Never raises: any failure yields an empty dict.
        """
        if not symbols:
            return {}
        client = self._get_client()
        if client is None:
            return {}
        try:
            return await asyncio.wait_for(
                self._fetch_prices(client, chain, network, symbols),
                timeout=_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"DexScreener price fetch timed out for {chain}-{network}")
            return {}
        except Exception as e:  # noqa: BLE001 - pricing must never break the balance refresh
            logger.warning(f"DexScreener price fetch failed for {chain}-{network}: {e}")
            return {}

    async def _fetch_prices(
        self, client: httpx.AsyncClient, chain: str, network: str, symbols: List[str]
    ) -> Dict[str, Decimal]:
        symbol_to_address = await self._symbol_to_address(chain, network)

        # Resolve the requested symbols to addresses; remember the original symbol per address
        # so the pair list can be mapped back to symbols regardless of address casing.
        addresses: List[str] = []
        address_to_symbol: Dict[str, str] = {}
        for symbol in symbols:
            address = symbol_to_address.get(symbol.upper())
            if not address:
                continue
            addresses.append(address)
            address_to_symbol.setdefault(address.lower(), symbol)
        if not addresses:
            return {}

        unique_addresses = list(dict.fromkeys(addresses))
        chunks = [
            unique_addresses[i:i + _CHUNK_ADDRESSES]
            for i in range(0, len(unique_addresses), _CHUNK_ADDRESSES)
        ]

        results = await asyncio.gather(
            *[self._fetch_pairs(client, chunk) for chunk in chunks],
            return_exceptions=True,
        )

        prices: Dict[str, Decimal] = {}
        for chunk, result in zip(chunks, results):
            if isinstance(result, Exception):
                # Unlike the GeckoTerminal source this is a warning, not a debug: a lost chunk
                # is the difference between a priced holding and a Gateway quote, and it is
                # invisible in the balance entry that comes out of it.
                logger.warning(f"DexScreener chunk failed for {chain}-{network}: {result}")
                continue
            for address in chunk:
                price = _select_price(result, address)
                symbol = address_to_symbol.get(address.lower())
                if price is not None and symbol is not None:
                    prices[symbol] = price
        return prices
