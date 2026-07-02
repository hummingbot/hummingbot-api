"""
Ticker source adapters.

Each adapter fetches a single bulk "all tickers" payload from one exchange and normalizes
it into ``{hb_trading_pair -> Ticker}``. The request goes through the connector's own
``_api_get`` so the exchange base URL, domain, authentication wrapper and rate-limit
throttling are reused (unlike raw HTTP calls). The raw exchange symbol is mapped to the
Hummingbot ``BASE-QUOTE`` format via ``trading_pair_associated_to_exchange_symbol``.

Adapters are keyed by connector name. Connectors without an adapter are skipped by the
collector (their held assets are still priced by the per-pair fallback in AccountsService).
Adding support for a new exchange is just registering one more entry in ``TICKER_ADAPTERS``.

The price/volume field names mirror the parsing already maintained upstream in
``hummingbot.core.rate_oracle.sources.*`` (price) and PR #173 (24h quote volume).
"""
import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Per-connector fetch timeout (seconds). One slow exchange must not stall the whole cycle.
_FETCH_TIMEOUT = 20.0


class Ticker:
    """A single normalized ticker: mid price, optional 24h quote volume, and a timestamp."""

    __slots__ = ("price", "volume", "timestamp")

    def __init__(self, price: Decimal, volume: Optional[Decimal], timestamp: float):
        self.price = price
        self.volume = volume
        self.timestamp = timestamp

    def to_dict(self) -> Dict[str, Any]:
        return {
            "price": float(self.price),
            "volume": float(self.volume) if self.volume is not None else None,
            "timestamp": self.timestamp,
        }


def _to_decimal(value: Any) -> Optional[Decimal]:
    """Parse a value to a positive Decimal, returning None on empty/invalid/non-positive input."""
    if value is None or value == "":
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return dec


def _mid(bid: Any, ask: Any, last: Any) -> Optional[Decimal]:
    """Mid price from bid/ask when both are positive, otherwise fall back to last price."""
    bid_d = _to_decimal(bid)
    ask_d = _to_decimal(ask)
    if bid_d is not None and ask_d is not None and bid_d > 0 and ask_d > 0:
        return (bid_d + ask_d) / Decimal("2")
    last_d = _to_decimal(last)
    if last_d is not None and last_d > 0:
        return last_d
    return None


async def _normalize(
    connector,
    rows: List[Dict[str, Any]],
    extract: Callable[[Dict[str, Any]], Tuple[Any, Optional[Decimal], Optional[Decimal]]],
) -> Dict[str, Ticker]:
    """
    Map raw rows to ``{hb_pair -> Ticker}``.

    ``extract`` returns ``(raw_symbol, price, volume)`` for a row. Rows for symbols the
    connector does not track (KeyError from the symbol map) or with no usable price are
    skipped. Each row is guarded so one malformed entry cannot abort the whole exchange.
    """
    now = time.time()
    out: Dict[str, Ticker] = {}
    for row in rows:
        try:
            raw_symbol, price, volume = extract(row)
            if price is None or price <= 0:
                continue
            pair = await connector.trading_pair_associated_to_exchange_symbol(raw_symbol)
        except KeyError:
            continue  # symbol not tracked by this connector
        except Exception as e:  # noqa: BLE001 - never let one bad row kill the batch
            logger.debug(f"Skipping ticker row {row!r}: {e}")
            continue
        out[pair] = Ticker(price=price, volume=volume, timestamp=now)
    return out


# ==================== Per-exchange adapters ====================

async def _binance(connector) -> Dict[str, Ticker]:
    # /ticker/24hr carries both bid/ask and quoteVolume; works for spot and perpetual
    # (the connector applies its own fapi/spot base URL). bookTicker (get_all_pairs_prices)
    # has no volume, so the 24hr endpoint is used here.
    path = "/ticker/24hr"
    rows = await connector._api_get(path_url=path, limit_id=path, is_auth_required=False)
    return await _normalize(
        connector, rows,
        lambda r: (r["symbol"], _mid(r.get("bidPrice"), r.get("askPrice"), r.get("lastPrice")),
                   _to_decimal(r.get("quoteVolume"))),
    )


async def _gate_io(connector) -> Dict[str, Ticker]:
    path = "spot/tickers"
    rows = await connector._api_get(path_url=path, limit_id=path, is_auth_required=False)
    return await _normalize(
        connector, rows,
        lambda r: (r["currency_pair"], _mid(r.get("highest_bid"), r.get("lowest_ask"), r.get("last")),
                   _to_decimal(r.get("quote_volume"))),
    )


async def _okx(connector) -> Dict[str, Ticker]:
    path = "/api/v5/market/tickers"
    payload = await connector._api_get(
        path_url=path, params={"instType": "SPOT"}, limit_id=path, is_auth_required=False
    )
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    return await _normalize(
        connector, rows,
        lambda r: (r["instId"], _mid(r.get("bidPx"), r.get("askPx"), r.get("last")),
                   _to_decimal(r.get("volCcy24h"))),
    )


async def _kucoin(connector) -> Dict[str, Ticker]:
    path = "/api/v1/market/allTickers"
    payload = await connector._api_get(path_url=path, limit_id=path, is_auth_required=False)
    rows = payload.get("data", {}).get("ticker", []) if isinstance(payload, dict) else []
    return await _normalize(
        connector, rows,
        lambda r: (r.get("symbolName") or r.get("symbol"),
                   _mid(r.get("buy"), r.get("sell"), r.get("last")),
                   _to_decimal(r.get("volValue"))),
    )


async def _ascend_ex(connector) -> Dict[str, Ticker]:
    path = "spot/ticker"
    payload = await connector._api_get(path_url=path, limit_id=path, is_auth_required=False)
    rows = payload.get("data", []) if isinstance(payload, dict) else []

    def extract(r):
        bid = r.get("bid", [None])[0] if isinstance(r.get("bid"), list) else None
        ask = r.get("ask", [None])[0] if isinstance(r.get("ask"), list) else None
        return r["symbol"], _mid(bid, ask, None), _to_decimal(r.get("volume"))

    return await _normalize(connector, rows, extract)


# ==================== Generic adapter (price-only, any connector) ====================

# Candidate field names, ordered by preference, used to parse arbitrary exchange ticker rows.
_SYMBOL_KEYS = ("symbol", "currency_pair", "instId", "symbolName", "trading_pair", "market", "pair", "s")
_LAST_KEYS = ("last", "lastPrice", "lastPr", "close", "price", "c", "lastTradeRate")
_BID_KEYS = ("bidPrice", "highest_bid", "bidPx", "buy", "bestBid", "bid", "b")
_ASK_KEYS = ("askPrice", "lowest_ask", "askPx", "sell", "bestAsk", "ask", "a")


def _first(row: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    """Return the first present, non-empty value among ``keys`` (unwrapping [price, size] lists)."""
    for key in keys:
        if key in row and row[key] not in (None, ""):
            value = row[key]
            return value[0] if isinstance(value, (list, tuple)) and value else value
    return None


def _heuristic_rows(raw: Any) -> List[Dict[str, Any]]:
    """Coerce an arbitrary 'all tickers' payload into a flat list of row dicts."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict) and isinstance(data.get("ticker"), list):
            return [r for r in data["ticker"] if isinstance(r, dict)]
        # Symbol-keyed dict-of-dicts (e.g. Kraken's {"result": {SYMBOL: {...}}}); inject the key.
        container = raw.get("result") if isinstance(raw.get("result"), dict) else raw
        rows = []
        for key, value in container.items():
            if isinstance(value, dict):
                rows.append({**value, "_sym": key})
        return rows
    return []


async def _generic(connector, connector_name: str) -> Dict[str, Ticker]:
    """
    Price-only adapter for any connector without a dedicated one.

    Prefers the single bulk ``get_all_pairs_prices()`` call (heuristically parsed and mapped
    through the connector's symbol map). If that endpoint is unavailable, falls back to
    ``get_last_traded_prices()`` over the connector's known pairs, whose keys are already in
    Hummingbot format. Volume is not available on this path.
    """
    # 1. Bulk all-pairs endpoint with heuristic parsing.
    try:
        raw = await connector.get_all_pairs_prices()
        rows = _heuristic_rows(raw)
        if rows:
            tickers = await _normalize(
                connector, rows,
                lambda r: (_first(r, _SYMBOL_KEYS) or r.get("_sym"),
                           _mid(_first(r, _BID_KEYS), _first(r, _ASK_KEYS), _first(r, _LAST_KEYS)),
                           None),
            )
            if tickers:
                return tickers
    except (NotImplementedError, AttributeError):
        pass
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Generic all-pairs fetch failed for '{connector_name}': {e}")

    # 2. Fallback: last traded prices over known pairs (keys already BASE-QUOTE).
    try:
        pairs = list(getattr(connector, "trading_rules", {}).keys())
        if not pairs:
            return {}
        prices = await connector.get_last_traded_prices(pairs)
        now = time.time()
        return {
            pair: Ticker(price=dec, volume=None, timestamp=now)
            for pair, price in prices.items()
            if (dec := _to_decimal(price)) is not None and dec > 0
        }
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Generic last-traded fetch failed for '{connector_name}': {e}")
        return {}


# Registry of dedicated price+volume adapters, verified against each exchange's spot endpoint.
# Perpetual variants are intentionally NOT reused here: their ticker endpoints differ (different
# rate-limit ids, instrument types and volume scaling), so they are handled by the generic
# price-only adapter above via each connector's own methods. Any connector not listed here uses
# the generic adapter.
TICKER_ADAPTERS: Dict[str, Callable[[Any], Awaitable[Dict[str, Ticker]]]] = {
    "binance": _binance,
    "gate_io": _gate_io,
    "okx": _okx,
    "kucoin": _kucoin,
    "ascend_ex": _ascend_ex,
}


async def fetch_tickers(connector, connector_name: str) -> Dict[str, Ticker]:
    """
    Fetch and normalize tickers for one connector. Returns ``{hb_pair -> Ticker}``.

    Uses the dedicated adapter (price + 24h volume) when one is registered, otherwise the
    generic price-only adapter. Never raises: failures and timeouts are logged and yield an
    empty dict so one exchange cannot break the collection cycle.
    """
    adapter = TICKER_ADAPTERS.get(connector_name)
    try:
        if adapter is not None:
            return await asyncio.wait_for(adapter(connector), timeout=_FETCH_TIMEOUT)
        return await asyncio.wait_for(_generic(connector, connector_name), timeout=_FETCH_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"Ticker fetch timed out for '{connector_name}'")
        return {}
    except Exception as e:  # noqa: BLE001 - one exchange failing must not break the cycle
        logger.warning(f"Failed to fetch tickers for '{connector_name}': {e}")
        return {}
