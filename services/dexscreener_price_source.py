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
(there is nothing to check it against), so the guards against an absurd price have to live in the
selection below — see ``_select_price``, which accepts a pair only if it is on the chain that was
asked about and is quoted in that chain's own address for a major asset, not in a token merely
named like one.

Like the GeckoTerminal source, ``fetch_prices`` never raises: any failure yields an empty dict
and the caller simply falls back to Gateway pricing.
"""
import asyncio
import logging
import math
import time
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional

import httpx

from services.gateway_client import GatewayError, check_gateway_error

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
# JUP/SOL's $0.3068. Restricting the quote to a major asset removes the whole class.
#
# SOL is in the list, not just the stables, because on this token population the stable-quoted
# pool is the outlier rather than the reference. Measured across the holdings: baton's only
# stable pool read $0.006060 against $0.002282 on its deepest SOL pool, KNOTS' stable pool read
# 0.02276 against 0.02431 on SOL, ZCAT's 0.1190 against 0.1140 — and GeckoTerminal sided with
# SOL on all three. Taking the deepest pool then lands on SOL for a pump.fun token and on a
# stable for a large one, which is what the measurement supports.
#
# These symbols are a lookup key into the Gateway token list, not a test against what DexScreener
# calls the quote. The quote is matched by its ADDRESS, because a symbol is not a unique identity:
# a pool quoted in a token that calls itself USDC would pass a symbol test, be ranked on its own
# claimed liquidity, and publish its price. See ``_select_price``.
_QUOTE_SYMBOLS = ("USDC", "USDT", "SOL", "WSOL")
# Below this the quoted price is as likely to be a dead pool as a market; skip the token and let
# the Gateway quote have it. Measured: baton's only stable-quoted pool held $1272 and read
# $0.006060 against a true $0.00232 — a 2.6x error a floor of this order drops.
_MIN_QUOTE_LIQ_USD = 10000.0
# How long a chain/network token list (symbol -> address) is cached before refetching.
_TOKEN_LIST_TTL = 3600.0
# Per fetch cycle timeout so a slow DexScreener never stalls a balance refresh.
_FETCH_TIMEOUT = 10.0

# Gateway network segment -> DexScreener chainId, keyed by the network part of the Gateway
# ``chain-network`` id exactly as ``GATEWAY_TO_GECKO_NETWORK`` is. The two maps are not
# interchangeable: DexScreener calls Ethereum "ethereum" where GeckoTerminal calls it "eth",
# and Polygon "polygon" against GeckoTerminal's "polygon_pos".
#
# Only ids verified against the live API are listed (mode is absent for that reason alone), and
# a network absent from the map is not priced via DexScreener at all. That is the point rather
# than an omission: an unnameable chain cannot be verified, and an unverified pool can be the
# deepest one. See ``_select_price``.
GATEWAY_TO_DEXSCREENER_CHAIN: Dict[str, str] = {
    # Solana
    "mainnet-beta": "solana",
    # Ethereum L1 + EVM L2s / sidechains (Gateway network segment)
    "mainnet": "ethereum",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "polygon": "polygon",
    "bsc": "bsc",
    "avalanche": "avalanche",
    "blast": "blast",
    "scroll": "scroll",
    "linea": "linea",
    "zksync": "zksync",
    "celo": "celo",
}


def gateway_network_to_dexscreener_chain(network: str) -> Optional[str]:
    """Map a Gateway network segment (e.g. ``mainnet-beta``, ``base``) to a DexScreener chainId."""
    return GATEWAY_TO_DEXSCREENER_CHAIN.get(network)


def _to_float(value) -> Optional[float]:
    """Parse a finite float, returning None on empty/invalid/non-finite input."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_decimal(value) -> Optional[Decimal]:
    """Parse a value to a positive Decimal, returning None on empty/invalid/non-finite input.

    Deliberately local rather than shared with the GeckoTerminal source: that module's guard
    arrives with a separate fix, and this source has to be correct without it. ``priceUsd`` is
    null on a pair that has never traded, and a non-finite value must be declined rather than
    raised on — ``Decimal('nan') > 0`` raises, and a raise here would escape ``_fetch_prices``
    into ``fetch_prices``'s broad ``except`` and drop the whole batch, unpricing every token it
    had already resolved.
    """
    number = _to_float(value)
    if number is None or number <= 0:
        return None
    return Decimal(str(number))


def _select_price(
    pairs: List[Dict],
    address: str,
    chain_id: str,
    quote_addresses: FrozenSet[str],
) -> Optional[Decimal]:
    """
    Best USD price for ``address`` among DexScreener ``pairs``, or None if there is none.

    A DexScreener response carries many pairs per token and several tokens per response, so
    the price is a choice, not a lookup. The choice is: among the pairs where the requested
    token is the BASE (a token also appears as the quote of other pairs, at a price that is
    not its own) and the pair is on ``chain_id`` and the quote is one of ``quote_addresses``
    (the chain's canonical addresses for the major assets, see ``_QUOTE_SYMBOLS``) and the pool
    holds at least ``_MIN_QUOTE_LIQ_USD``, take the deepest pool. The price may be null on a
    pair that has never traded, which ``_to_decimal`` turns into None like the GeckoTerminal NaN.

    ``chain_id`` and ``quote_addresses`` are identity checks rather than filters of taste, and
    both come from a measured trap. The endpoint answers for the address on every chain that
    has one -- the same USDC address comes back as both "ethereum" and "pulsechain" -- so
    without the chain a deeper foreign pool can win and its price be published for the network
    that was asked about. And a quote is matched by address because a symbol is not an identity:
    a pool quoted in a token that merely calls itself USDC would pass a symbol test and be
    ranked on its own claimed liquidity.
    """
    target = address.lower()
    best_liquidity: Optional[float] = None
    best_price: Optional[Decimal] = None

    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        if str(pair.get("chainId") or "").lower() != chain_id:
            continue
        base_address = (pair.get("baseToken") or {}).get("address") or ""
        if str(base_address).lower() != target:
            continue
        quote_address = (pair.get("quoteToken") or {}).get("address") or ""
        if str(quote_address).lower() not in quote_addresses:
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
        chain_id = gateway_network_to_dexscreener_chain(network)
        if not chain_id:
            # Without a chain to check the pairs against, a foreign pool could be the one that
            # wins. Skip the network rather than price it unverified; the token falls through to
            # the Gateway quote, which is where it went before this source existed.
            return {}

        symbol_to_address = await self._symbol_to_address(chain, network)

        # The canonical address of each major quote asset on this chain, straight from the
        # Gateway token list, which is what a pair's quote token must match. A symbol with no
        # Gateway entry contributes nothing (WSOL on this install; SOL and WSOL are the same
        # mint and the list carries SOL). Should none of the majors be in the list the set is
        # empty and no pair qualifies -- that is the Gateway-quote fall-through, not a new
        # failure mode.
        quote_addresses = frozenset(
            str(symbol_to_address[symbol]).lower()
            for symbol in _QUOTE_SYMBOLS
            if symbol in symbol_to_address
        )

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
                price = _select_price(result, address, chain_id, quote_addresses)
                symbol = address_to_symbol.get(address.lower())
                if price is not None and symbol is not None:
                    prices[symbol] = price
        return prices
