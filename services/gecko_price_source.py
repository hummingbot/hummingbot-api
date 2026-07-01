"""
GeckoTerminal DEX price source.

Fetches USD spot prices for on-chain (Gateway) tokens directly from the GeckoTerminal public
API, in bulk by token contract address. This lets blockchain holdings be priced from a single
batched HTTP call (up to 30 tokens) instead of one Gateway ``quote_swap`` per token, so DEX
prices can be refreshed at a polling cadence similar to the CEX tickers in ``ticker_sources``.

Two vocabularies have to be bridged:

* The Gateway speaks ``chain-network`` ids (e.g. ``solana-mainnet-beta``, ``ethereum-base``)
  and prices tokens by **symbol**.
* GeckoTerminal speaks its own network ids (e.g. ``solana``, ``base``) and keys tokens by
  **contract address**.

So this module maps the network, resolves each requested symbol to its address via the Gateway
token list (cached per network), batch-fetches USD prices, and maps the results back to symbols.

The dependency (``geckoterminal-py``) is imported lazily: if it is not installed the source
degrades to a no-op (returns ``{}``) and callers simply fall back to Gateway pricing.
"""
import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# GeckoTerminal accepts at most 30 token addresses per simple-token-price call.
_MAX_ADDRESSES_PER_CALL = 30
# How long a chain/network token list (symbol -> address) is cached before refetching.
_TOKEN_LIST_TTL = 3600.0
# Per fetch cycle timeout so a slow GeckoTerminal never stalls a balance refresh.
_FETCH_TIMEOUT = 15.0

# Gateway network segment -> GeckoTerminal network id. Keyed by the network part of the
# Gateway ``chain-network`` id (unambiguous in Gateway: only Solana uses "mainnet-beta" and
# only Ethereum uses "mainnet"). Networks absent here are simply not priced via GeckoTerminal.
GATEWAY_TO_GECKO_NETWORK: Dict[str, str] = {
    # Solana
    "mainnet-beta": "solana",
    # Ethereum L1 + EVM L2s / sidechains (Gateway network segment)
    "mainnet": "eth",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "polygon": "polygon_pos",
    "bsc": "bsc",
    "avalanche": "avax",
    "blast": "blast",
    "mode": "mode",
    "scroll": "scroll",
    "linea": "linea",
    "zksync": "zksync",
    "celo": "celo",
}


def gateway_network_to_gecko(network: str) -> Optional[str]:
    """Map a Gateway network segment (e.g. ``mainnet-beta``, ``base``) to a GeckoTerminal id."""
    return GATEWAY_TO_GECKO_NETWORK.get(network)


def _to_decimal(value) -> Optional[Decimal]:
    """Parse a value to a positive Decimal, returning None on empty/invalid/non-positive input."""
    if value is None or value == "":
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return dec if dec > 0 else None


class GeckoPriceSource:
    """Batched USD price lookups for Gateway tokens via the GeckoTerminal API.

    A single async client is reused across calls. Token lists are cached per ``(chain, network)``
    with a TTL so the address for each symbol is not refetched on every cycle.
    """

    def __init__(self, gateway_client):
        """
        Args:
            gateway_client: The ``GatewayClient`` used to fetch token lists (symbol -> address).
        """
        self._gateway_client = gateway_client
        self._client = None
        self._client_unavailable = False
        # (chain, network) -> (fetched_at, {UPPER_SYMBOL: address})
        self._token_list_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, str]]] = {}

    def _get_client(self):
        """Lazily construct the GeckoTerminal async client; return None if the dep is missing."""
        if self._client is not None:
            return self._client
        if self._client_unavailable:
            return None
        try:
            from geckoterminal_py import GeckoTerminalAsyncClient
        except ImportError:
            logger.warning(
                "geckoterminal-py is not installed; GeckoTerminal DEX pricing disabled "
                "(falling back to Gateway prices). Add 'geckoterminal-py' to the environment."
            )
            self._client_unavailable = True
            return None
        self._client = GeckoTerminalAsyncClient()
        return self._client

    async def close(self):
        """Close the underlying HTTP client if it was created."""
        if self._client is not None:
            try:
                await self._client.close()
            finally:
                self._client = None

    async def _symbol_to_address(self, chain: str, network: str) -> Dict[str, str]:
        """Return a cached ``{UPPER_SYMBOL: address}`` map for a chain/network from Gateway."""
        key = (chain, network)
        cached = self._token_list_cache.get(key)
        if cached is not None and (time.time() - cached[0]) < _TOKEN_LIST_TTL:
            return cached[1]

        response = await self._gateway_client.get_tokens(chain, network)
        tokens = response.get("tokens", []) if isinstance(response, dict) else []
        mapping: Dict[str, str] = {}
        for token in tokens:
            symbol = token.get("symbol")
            address = token.get("address")
            if symbol and address:
                mapping[symbol.upper()] = address
        self._token_list_cache[key] = (time.time(), mapping)
        return mapping

    async def fetch_prices(self, chain: str, network: str, symbols: List[str]) -> Dict[str, Decimal]:
        """
        Fetch USD prices for the given token ``symbols`` on a Gateway chain/network.

        Returns ``{symbol: price}`` (original-cased symbols, USD price as Decimal) only for
        symbols GeckoTerminal could price. Symbols with no known address, no listed pool, or a
        non-positive price are omitted so the caller can fall back to Gateway for those. Never
        raises: any failure yields an empty dict.
        """
        gecko_network = gateway_network_to_gecko(network)
        if not gecko_network or not symbols:
            return {}
        client = self._get_client()
        if client is None:
            return {}
        try:
            return await asyncio.wait_for(
                self._fetch_prices(client, chain, network, gecko_network, symbols),
                timeout=_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"GeckoTerminal price fetch timed out for {chain}-{network}")
            return {}
        except Exception as e:  # noqa: BLE001 - pricing must never break the balance refresh
            logger.warning(f"GeckoTerminal price fetch failed for {chain}-{network}: {e}")
            return {}

    async def _fetch_prices(self, client, chain: str, network: str, gecko_network: str,
                            symbols: List[str]) -> Dict[str, Decimal]:
        symbol_to_address = await self._symbol_to_address(chain, network)

        # Resolve requested symbols to addresses; remember address (both exact and lowercased)
        # -> original symbol so we can map the response back regardless of address casing.
        addresses: List[str] = []
        addr_to_symbol: Dict[str, str] = {}
        for symbol in symbols:
            address = symbol_to_address.get(symbol.upper())
            if not address:
                continue
            addresses.append(address)
            addr_to_symbol[address] = symbol
            addr_to_symbol[address.lower()] = symbol
        if not addresses:
            return {}

        # De-duplicate while preserving order, then chunk to the per-call address limit.
        unique_addresses = list(dict.fromkeys(addresses))
        chunks = [unique_addresses[i:i + _MAX_ADDRESSES_PER_CALL]
                  for i in range(0, len(unique_addresses), _MAX_ADDRESSES_PER_CALL)]

        results = await asyncio.gather(
            *[client.get_simple_token_price(gecko_network, chunk) for chunk in chunks],
            return_exceptions=True,
        )

        prices: Dict[str, Decimal] = {}
        for result in results:
            if isinstance(result, Exception):
                logger.debug(f"GeckoTerminal chunk failed for {gecko_network}: {result}")
                continue
            # result is a DataFrame with columns token_address / price_usd.
            for address, price_usd in zip(result["token_address"], result["price_usd"]):
                symbol = addr_to_symbol.get(address) or addr_to_symbol.get(str(address).lower())
                price = _to_decimal(price_usd)
                if symbol and price is not None:
                    prices[symbol] = price
        return prices
