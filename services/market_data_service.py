"""
Market Data Service - Centralized market data access with proper connector integration.

This service provides access to market data (candles, order books, prices, trading rules)
using the UnifiedConnectorService to ensure proper connector usage.
"""
import asyncio
import logging
import time
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from services.unified_connector_service import UnifiedConnectorService

from hummingbot.connector.utils import combine_to_hb_trading_pair
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory, UnsupportedConnectorException
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig

from services.ticker_sources import Ticker, fetch_tickers
from utils.rate_finder import find_rate

logger = logging.getLogger(__name__)


class FeedType(Enum):
    """Types of market data feeds that can be managed."""
    CANDLES = "candles"
    ORDER_BOOK = "order_book"
    TRADES = "trades"
    TICKER = "ticker"


class MarketDataService:
    """
    Centralized market data service using UnifiedConnectorService.

    This service manages:
    - Candles feeds with automatic lifecycle management
    - Order book access via UnifiedConnectorService
    - Price and trading rules queries
    - Feed cleanup for unused data streams
    """

    def __init__(
            self,
            connector_service: "UnifiedConnectorService",
            quote_token: str = "USDT",
            cleanup_interval: int = 300,
            feed_timeout: int = 600,
            ticker_update_interval: int = 30,
    ):
        """
        Initialize the MarketDataService.

        Args:
            connector_service: UnifiedConnectorService for connector access
            quote_token: Global quote token everything is valued in (e.g. "USDT")
            cleanup_interval: How often to run feed cleanup (seconds, default: 5 minutes)
            feed_timeout: How long to keep unused feeds alive (seconds, default: 10 minutes)
            ticker_update_interval: How often to refresh tickers from connected exchanges (seconds)
        """
        self._connector_service = connector_service
        self._quote_token = quote_token
        self._cleanup_interval = cleanup_interval
        self._feed_timeout = feed_timeout
        self._ticker_update_interval = ticker_update_interval

        # Candle feeds management
        self._candle_feeds: Dict[str, Any] = {}
        self._last_access_times: Dict[str, float] = {}
        self._feed_configs: Dict[str, Tuple[FeedType, Any]] = {}

        # Ticker pool: per-connector tickers gathered from connected exchanges, plus a merged
        # price dict used by the cross-rate finder. External (e.g. blockchain/Gateway) prices
        # are kept separately so the periodic ticker rebuild never wipes them.
        self._tickers: Dict[str, Dict[str, Ticker]] = {}
        self._prices: Dict[str, Decimal] = {}
        self._external_prices: Dict[str, Decimal] = {}

        # Background tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._ticker_task: Optional[asyncio.Task] = None
        self._is_running = False

        logger.info("MarketDataService initialized")

    # ==================== Lifecycle ====================

    def start(self):
        """Start the market data service."""
        if not self._is_running:
            self._is_running = True
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            self._ticker_task = asyncio.create_task(self._ticker_collection_loop())
            logger.info(
                f"MarketDataService started with cleanup_interval={self._cleanup_interval}s, "
                f"feed_timeout={self._feed_timeout}s, ticker_update_interval={self._ticker_update_interval}s"
            )

    async def warmup_tickers(self):
        """Run one ticker collection pass so the price pool is populated before serving traffic."""
        try:
            await self._collect_all_tickers()
            logger.info(
                f"Ticker pool warmed up: {len(self._prices)} prices across "
                f"{len(self._tickers)} connectors"
            )
        except Exception as e:
            logger.warning(f"Ticker warmup failed: {e}")

    def stop(self):
        """Stop the market data service and cleanup all feeds."""
        self._is_running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None

        if self._ticker_task:
            self._ticker_task.cancel()
            self._ticker_task = None

        # Stop all candle feeds
        for feed_key, feed in self._candle_feeds.items():
            try:
                feed.stop()
            except Exception as e:
                logger.error(f"Error stopping candle feed {feed_key}: {e}")

        self._candle_feeds.clear()
        self._last_access_times.clear()
        self._feed_configs.clear()
        self._tickers.clear()
        self._prices.clear()

        logger.info("MarketDataService stopped")

    # ==================== Order Book Access ====================

    async def initialize_order_book(
            self,
            connector_name: str,
            trading_pair: str,
            account_name: Optional[str] = None,
            timeout: float = 30.0
    ) -> bool:
        """
        Initialize an order book for a trading pair.

        Uses the UnifiedConnectorService to get the best available connector
        (prefers trading connectors which already have order book trackers running).

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair (e.g., "SOL-FDUSD")
            account_name: Optional account name for trading connector preference
            timeout: Timeout for waiting for order book to be ready

        Returns:
            True if order book is ready, False otherwise
        """
        return await self._connector_service.initialize_order_book(
            connector_name=connector_name,
            trading_pair=trading_pair,
            account_name=account_name,
            timeout=timeout
        )

    async def remove_trading_pair(
            self,
            connector_name: str,
            trading_pair: str,
            account_name: Optional[str] = None
    ) -> bool:
        """
        Remove a trading pair from order book tracking.

        Cleans up order book resources for a trading pair that is no longer needed.

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair to remove
            account_name: Optional account name for trading connector preference

        Returns:
            True if successfully removed, False otherwise
        """
        # Clean up our local tracking for this feed
        feed_key = self._generate_feed_key(FeedType.ORDER_BOOK, connector_name, trading_pair)
        self._last_access_times.pop(feed_key, None)
        self._feed_configs.pop(feed_key, None)

        return await self._connector_service.remove_trading_pair(
            connector_name=connector_name,
            trading_pair=trading_pair,
            account_name=account_name
        )

    def get_order_book(self, connector_name: str, trading_pair: str, account_name: Optional[str] = None):
        """
        Get order book for a trading pair.

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair
            account_name: Optional account name for trading connector preference

        Returns:
            OrderBook instance or None
        """
        feed_key = self._generate_feed_key(FeedType.ORDER_BOOK, connector_name, trading_pair)
        self._last_access_times[feed_key] = time.time()
        self._feed_configs[feed_key] = (FeedType.ORDER_BOOK, (connector_name, trading_pair))

        connector = self._connector_service.get_best_connector_for_market(
            connector_name, account_name
        )

        if connector and hasattr(connector, 'order_book_tracker'):
            tracker = connector.order_book_tracker
            if tracker and trading_pair in tracker.order_books:
                return tracker.order_books[trading_pair]

        logger.warning(f"No order book found for {connector_name}/{trading_pair}")
        return None

    def get_order_book_snapshot(
            self,
            connector_name: str,
            trading_pair: str,
            account_name: Optional[str] = None
    ) -> Optional[Tuple]:
        """
        Get order book snapshot (bids, asks DataFrames).

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair
            account_name: Optional account name for trading connector preference

        Returns:
            Tuple of (bids_df, asks_df) or None
        """
        order_book = self.get_order_book(connector_name, trading_pair, account_name)
        if order_book:
            try:
                return order_book.snapshot
            except Exception as e:
                logger.error(f"Error getting order book snapshot: {e}")
        return None

    async def get_order_book_data(
            self,
            connector_name: str,
            trading_pair: str,
            depth: int = 10,
            account_name: Optional[str] = None
    ) -> Dict:
        """
        Get order book data as a dictionary.

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair
            depth: Number of bid/ask levels to return
            account_name: Optional account name for trading connector preference

        Returns:
            Dictionary with bids, asks, and metadata
        """
        try:
            connector = self._connector_service.get_best_connector_for_market(
                connector_name, account_name
            )

            if not connector:
                return {"error": f"No connector available for {connector_name}"}

            # Try to get from existing order book tracker
            if hasattr(connector, 'order_book_tracker') and connector.order_book_tracker:
                tracker = connector.order_book_tracker
                if trading_pair in tracker.order_books:
                    order_book = tracker.order_books[trading_pair]
                    snapshot = order_book.snapshot

                    return {
                        "trading_pair": trading_pair,
                        "bids": snapshot[0].head(depth)[["price", "amount"]].values.tolist(),
                        "asks": snapshot[1].head(depth)[["price", "amount"]].values.tolist(),
                        "timestamp": time.time()
                    }

            # Fallback to getting fresh order book from data source
            if hasattr(connector, '_orderbook_ds') and connector._orderbook_ds:
                orderbook_ds = connector._orderbook_ds
                order_book = await orderbook_ds.get_new_order_book(trading_pair)
                snapshot = order_book.snapshot

                return {
                    "trading_pair": trading_pair,
                    "bids": snapshot[0].head(depth)[["price", "amount"]].values.tolist(),
                    "asks": snapshot[1].head(depth)[["price", "amount"]].values.tolist(),
                    "timestamp": time.time()
                }

            return {"error": f"Order book not available for {connector_name}/{trading_pair}"}

        except Exception as e:
            logger.error(f"Error getting order book data for {connector_name}/{trading_pair}: {e}")
            return {"error": str(e)}

    async def get_order_book_query_result(
            self,
            connector_name: str,
            trading_pair: str,
            is_buy: bool,
            account_name: Optional[str] = None,
            **kwargs
    ) -> Dict:
        """
        Query order book for price/volume calculations.

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair
            is_buy: True for buy side, False for sell side
            account_name: Optional account name
            **kwargs: Query parameters (volume, price, quote_volume, etc.)

        Returns:
            Query result dictionary
        """
        try:
            current_time = time.time()
            connector = self._connector_service.get_best_connector_for_market(
                connector_name, account_name
            )

            if not connector:
                return {"error": f"No connector available for {connector_name}"}

            # Get order book
            order_book = None
            if hasattr(connector, 'order_book_tracker') and connector.order_book_tracker:
                tracker = connector.order_book_tracker
                if trading_pair in tracker.order_books:
                    order_book = tracker.order_books[trading_pair]

            if not order_book and hasattr(connector, '_orderbook_ds') and connector._orderbook_ds:
                order_book = await connector._orderbook_ds.get_new_order_book(trading_pair)

            if not order_book:
                return {"error": f"No order book available for {connector_name}/{trading_pair}"}

            # Process query
            if 'volume' in kwargs:
                result = order_book.get_price_for_volume(is_buy, kwargs['volume'])
                return {
                    "trading_pair": trading_pair,
                    "is_buy": is_buy,
                    "query_volume": kwargs['volume'],
                    "result_price": float(result.result_price) if result.result_price else None,
                    "result_volume": float(result.result_volume) if result.result_volume else None,
                    "timestamp": current_time
                }

            elif 'price' in kwargs:
                result = order_book.get_volume_for_price(is_buy, kwargs['price'])
                return {
                    "trading_pair": trading_pair,
                    "is_buy": is_buy,
                    "query_price": kwargs['price'],
                    "result_volume": float(result.result_volume) if result.result_volume else None,
                    "result_price": float(result.result_price) if result.result_price else None,
                    "timestamp": current_time
                }

            elif 'vwap_volume' in kwargs:
                result = order_book.get_vwap_for_volume(is_buy, kwargs['vwap_volume'])
                return {
                    "trading_pair": trading_pair,
                    "is_buy": is_buy,
                    "query_volume": kwargs['vwap_volume'],
                    "average_price": float(result.result_price) if result.result_price else None,
                    "result_volume": float(result.result_volume) if result.result_volume else None,
                    "timestamp": current_time
                }

            else:
                return {"error": "Invalid query parameters"}

        except Exception as e:
            logger.error(f"Error in order book query for {connector_name}/{trading_pair}: {e}")
            return {"error": str(e)}

    # ==================== Candles ====================

    @staticmethod
    def validate_connector(connector_name: str) -> None:
        if connector_name not in CandlesFactory._candles_map:
            raise UnsupportedConnectorException(connector_name)

    @staticmethod
    async def _validate_pair(feed, connector_name: str, trading_pair: str) -> None:
        """
        Validate that a trading pair exists on the exchange by loading the feed's exchange
        data and probing a single REST candle.

        Called once per feed, at creation time, so the cost is not paid on every request.

        Raises:
            ValueError: If the trading pair does not exist or the exchange returns an error.
        """
        try:
            # Some feeds (e.g. hyperliquid spot) need exchange data (symbol maps,
            # quanto multipliers, etc.) loaded before a REST candle fetch can build
            # its payload. start_network() does this internally, but fetch_candles()
            # does not, so initialize explicitly here. No-op on feeds that don't need it.
            await feed.initialize_exchange_data()
            # Probe a generous window: a 1-candle probe spans only the current (often
            # incomplete) interval, which is empty for illiquid pairs. fetch_candles
            # returns a 0-d numpy array (np.array(None)) when no candles come back, so
            # check ndim before len() to stay numpy-safe.
            candles = await feed.fetch_candles(end_time=int(time.time()), limit=50)
            if candles is None or getattr(candles, "ndim", 0) < 2 or len(candles) == 0:
                raise ValueError(
                    f"Trading pair '{trading_pair}' not found on '{connector_name}'. "
                    f"No candle data returned."
                )
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(
                f"Trading pair '{trading_pair}' appears to be invalid on '{connector_name}': {e}"
            )

    async def get_candles_feed(self, config: CandlesConfig):
        """
        Get or create a candles feed.

        On first creation the trading pair is validated (exchange data load + a one-candle
        REST probe). Cached feeds are returned directly, so repeated requests for the same
        feed pay no extra REST cost and never re-initialize exchange data.

        Args:
            config: CandlesConfig for the desired feed

        Returns:
            Candle feed instance

        Raises:
            ValueError: If the trading pair does not exist on the exchange.
        """
        feed_key = self._generate_feed_key(
            FeedType.CANDLES, config.connector, config.trading_pair, config.interval
        )

        if feed_key not in self._candle_feeds:
            self.validate_connector(config.connector)
            feed = CandlesFactory.get_candle(config)
            await self._validate_pair(feed, config.connector, config.trading_pair)
            feed.start()
            self._candle_feeds[feed_key] = feed
            self._feed_configs[feed_key] = (FeedType.CANDLES, config)
            logger.info(f"Created candle feed: {feed_key}")

        self._last_access_times[feed_key] = time.time()
        return self._candle_feeds[feed_key]

    async def get_candles_df(
            self,
            connector_name: str,
            trading_pair: str,
            interval: str,
            max_records: int = 500
    ):
        """
        Get candles dataframe.

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair
            interval: Candle interval
            max_records: Maximum number of records

        Returns:
            Pandas DataFrame with candle data
        """
        config = CandlesConfig(
            connector=connector_name,
            trading_pair=trading_pair,
            interval=interval,
            max_records=max_records
        )

        feed = await self.get_candles_feed(config)
        return feed.candles_df

    def stop_candle_feed(self, config: CandlesConfig):
        """Stop a specific candle feed."""
        feed_key = self._generate_feed_key(
            FeedType.CANDLES, config.connector, config.trading_pair, config.interval
        )

        if feed_key in self._candle_feeds:
            try:
                self._candle_feeds[feed_key].stop()
                del self._candle_feeds[feed_key]
                logger.info(f"Stopped candle feed: {feed_key}")
            except Exception as e:
                logger.error(f"Error stopping candle feed {feed_key}: {e}")

    # ==================== Prices ====================

    async def get_prices(
            self,
            connector_name: str,
            trading_pairs: List[str],
            account_name: Optional[str] = None
    ) -> Dict[str, float]:
        """
        Get current prices for trading pairs.

        Args:
            connector_name: Exchange connector name
            trading_pairs: List of trading pairs
            account_name: Optional account name for trading connector preference

        Returns:
            Dictionary mapping trading pairs to prices
        """
        try:
            connector = self._connector_service.get_best_connector_for_market(
                connector_name, account_name
            )

            if not connector:
                return {"error": f"No connector available for {connector_name}"}

            prices = await connector.get_last_traded_prices(trading_pairs)
            return {pair: float(price) for pair, price in prices.items()}

        except Exception as e:
            logger.error(f"Error getting prices for {connector_name}: {e}")
            return {"error": str(e)}

    def get_rate(self, base: str, quote: Optional[str] = None) -> Optional[Decimal]:
        """
        Get exchange rate from the collected ticker pool using cross-rate resolution.

        Resolves the rate from the merged price pool (all connected exchanges plus any
        externally pushed prices) via :func:`find_rate`, so direct, reverse and bridged
        pairs are all supported.

        Args:
            base: Base currency
            quote: Quote currency (defaults to the configured global quote token)

        Returns:
            Exchange rate or None if it cannot be resolved from the pool
        """
        quote = quote or self._quote_token
        return self.get_pair_rate(combine_to_hb_trading_pair(base=base, quote=quote))

    def get_pair_rate(self, trading_pair: str) -> Optional[Decimal]:
        """
        Resolve a rate for a ``BASE-QUOTE`` trading pair from the ticker pool.

        This mirrors the ``RateOracle.get_pair_rate`` interface so it can also serve as the
        rate provider for connector trade-volume telemetry.
        """
        try:
            return find_rate(self._prices, trading_pair)
        except Exception as e:
            logger.debug(f"Rate not available for {trading_pair}: {e}")
            return None

    def set_price(self, trading_pair: str, price: Decimal):
        """
        Push an external price into the pool (e.g. blockchain/Gateway DEX prices that are
        not covered by CEX tickers). External prices persist across ticker refreshes and
        take precedence over collected ticker prices for the same pair.
        """
        try:
            self._external_prices[trading_pair] = Decimal(str(price))
            self._prices[trading_pair] = self._external_prices[trading_pair]
        except Exception as e:
            logger.debug(f"Failed to set price for {trading_pair}: {e}")

    @property
    def prices(self) -> Dict[str, Decimal]:
        """The merged price pool (ticker prices plus external prices)."""
        return self._prices

    def get_ticker(self, connector_name: str, trading_pair: str) -> Optional[Ticker]:
        """Get a single collected ticker for a connector/pair, or None."""
        return self._tickers.get(connector_name, {}).get(trading_pair)

    def get_tickers(self, connector_name: Optional[str] = None) -> Dict[str, Dict[str, Ticker]]:
        """
        Get collected tickers.

        Args:
            connector_name: If provided, return only that connector's tickers (wrapped in a
                single-key dict); otherwise return tickers for all connectors.
        """
        if connector_name is not None:
            return {connector_name: self._tickers.get(connector_name, {})}
        return self._tickers

    def get_rate_for_connector(
            self, connector_name: str, base: str, quote: Optional[str] = None
    ) -> Optional[Decimal]:
        """Resolve a rate using only a single connector's tickers (no cross-exchange merge)."""
        quote = quote or self._quote_token
        connector_prices = {pair: t.price for pair, t in self._tickers.get(connector_name, {}).items()}
        try:
            return find_rate(connector_prices, combine_to_hb_trading_pair(base=base, quote=quote))
        except Exception:
            return None

    # ==================== Ticker Collection ====================

    def _connected_connector_names(self) -> List[str]:
        """Unique connector names currently connected (trading connectors + started data connectors).

        Every connected exchange is collected: dedicated adapters provide price+volume, and the
        generic adapter provides price for the rest. Paper-trade connectors are skipped.
        """
        names = set()
        for account_connectors in self._connector_service.get_all_trading_connectors().values():
            names.update(account_connectors.keys())
        names.update(self._connector_service._data_connectors.keys())
        return [n for n in names if "paper_trade" not in n]

    async def _collect_all_tickers(self):
        """Fetch tickers from every connected exchange concurrently and rebuild the price pool."""
        connector_names = self._connected_connector_names()
        if not connector_names:
            return

        async def _fetch(name: str) -> Tuple[str, Dict[str, Ticker]]:
            connector = self._connector_service.get_best_connector_for_market(name)
            if connector is None:
                return name, {}
            return name, await fetch_tickers(connector, name)

        results = await asyncio.gather(
            *[_fetch(name) for name in connector_names], return_exceptions=True
        )

        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Ticker collection task failed: {result}")
                continue
            name, tickers = result
            if tickers:
                self._tickers[name] = tickers

        self._rebuild_price_pool()

    def _rebuild_price_pool(self):
        """
        Rebuild the merged ``trading_pair -> price`` pool from all connectors' tickers.

        On duplicate pairs across exchanges, the entry with the higher 24h volume wins
        (more liquid market). Externally pushed prices are layered on top last so they are
        never overwritten by ticker data.
        """
        best: Dict[str, Ticker] = {}
        for tickers in self._tickers.values():
            for pair, ticker in tickers.items():
                current = best.get(pair)
                if current is None or self._is_more_liquid(ticker, current):
                    best[pair] = ticker
        merged = {pair: ticker.price for pair, ticker in best.items()}
        merged.update(self._external_prices)
        self._prices = merged

    @staticmethod
    def _is_more_liquid(candidate: Ticker, current: Ticker) -> bool:
        """True if candidate should replace current (higher volume, treating None as 0)."""
        return (candidate.volume or Decimal("0")) > (current.volume or Decimal("0"))

    async def _ticker_collection_loop(self):
        """Background task that periodically refreshes tickers from connected exchanges."""
        while self._is_running:
            try:
                await asyncio.sleep(self._ticker_update_interval)
                await self._collect_all_tickers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ticker collection loop: {e}", exc_info=True)

    # ==================== Trading Rules ====================

    async def get_trading_rules(
            self,
            connector_name: str,
            trading_pairs: Optional[List[str]] = None,
            account_name: Optional[str] = None
    ) -> Dict[str, Dict]:
        """
        Get trading rules for trading pairs.

        Args:
            connector_name: Exchange connector name
            trading_pairs: List of trading pairs (None for all)
            account_name: Optional account name

        Returns:
            Dictionary mapping trading pairs to their rules
        """
        try:
            connector = self._connector_service.get_best_connector_for_market(
                connector_name, account_name
            )

            if not connector:
                return {"error": f"No connector available for {connector_name}"}

            # Ensure trading rules are loaded
            if not connector.trading_rules or len(connector.trading_rules) == 0:
                await connector._update_trading_rules()

            result = {}
            rules_to_process = trading_pairs if trading_pairs else connector.trading_rules.keys()

            for trading_pair in rules_to_process:
                if trading_pair in connector.trading_rules:
                    rule = connector.trading_rules[trading_pair]
                    result[trading_pair] = {
                        "min_order_size": float(rule.min_order_size),
                        "max_order_size": float(rule.max_order_size) if rule.max_order_size else None,
                        "min_price_increment": float(rule.min_price_increment),
                        "min_base_amount_increment": float(rule.min_base_amount_increment),
                        "min_quote_amount_increment": float(rule.min_quote_amount_increment),
                        "min_notional_size": float(rule.min_notional_size),
                        "min_order_value": float(rule.min_order_value),
                        "max_price_significant_digits": float(rule.max_price_significant_digits),
                        "supports_limit_orders": rule.supports_limit_orders,
                        "supports_market_orders": rule.supports_market_orders,
                        "buy_order_collateral_token": rule.buy_order_collateral_token,
                        "sell_order_collateral_token": rule.sell_order_collateral_token,
                    }
                elif trading_pairs:
                    result[trading_pair] = {"error": f"Trading pair {trading_pair} not found"}

            return result

        except Exception as e:
            logger.error(f"Error getting trading rules for {connector_name}: {e}")
            return {"error": str(e)}

    # ==================== Funding Info ====================

    async def get_funding_info(
            self,
            connector_name: str,
            trading_pair: str,
            account_name: Optional[str] = None
    ) -> Dict:
        """
        Get funding information for perpetual trading pairs.

        Args:
            connector_name: Exchange connector name
            trading_pair: Trading pair
            account_name: Optional account name

        Returns:
            Dictionary with funding information
        """
        try:
            connector = self._connector_service.get_best_connector_for_market(
                connector_name, account_name
            )

            if not connector:
                return {"error": f"No connector available for {connector_name}"}

            if hasattr(connector, '_orderbook_ds') and connector._orderbook_ds:
                orderbook_ds = connector._orderbook_ds
                funding_info = await orderbook_ds.get_funding_info(trading_pair)

                if funding_info:
                    return {
                        "trading_pair": trading_pair,
                        "funding_rate": float(funding_info.rate) if funding_info.rate else None,
                        "next_funding_time": float(
                            funding_info.next_funding_utc_timestamp) if funding_info.next_funding_utc_timestamp else None,
                        "mark_price": float(funding_info.mark_price) if funding_info.mark_price else None,
                        "index_price": float(funding_info.index_price) if funding_info.index_price else None,
                    }
                else:
                    return {"error": f"No funding info available for {trading_pair}"}
            else:
                return {"error": f"Funding info not supported for {connector_name}"}

        except Exception as e:
            logger.error(f"Error getting funding info for {connector_name}/{trading_pair}: {e}")
            return {"error": str(e)}

    # ==================== Feed Management ====================

    def get_active_feeds_info(self) -> Dict[str, dict]:
        """Get information about active feeds."""
        current_time = time.time()
        result = {}

        for feed_key, last_access in self._last_access_times.items():
            feed_type, config = self._feed_configs.get(feed_key, (None, None))
            result[feed_key] = {
                "feed_type": feed_type.value if feed_type else "unknown",
                "last_access_time": last_access,
                "seconds_since_access": current_time - last_access,
                "will_expire_in": max(0, self._feed_timeout - (current_time - last_access)),
                "config": str(config)
            }

        return result

    def manually_cleanup_feed(
            self,
            feed_type: FeedType,
            connector: str,
            trading_pair: str,
            interval: str = None
    ):
        """Manually cleanup a specific feed."""
        feed_key = self._generate_feed_key(feed_type, connector, trading_pair, interval)

        if feed_key in self._feed_configs:
            try:
                if feed_type == FeedType.CANDLES and feed_key in self._candle_feeds:
                    self._candle_feeds[feed_key].stop()
                    del self._candle_feeds[feed_key]

                del self._last_access_times[feed_key]
                del self._feed_configs[feed_key]
                logger.info(f"Manually cleaned up feed: {feed_key}")
            except Exception as e:
                logger.error(f"Error manually cleaning up feed {feed_key}: {e}")
        else:
            logger.warning(f"Feed not found for cleanup: {feed_key}")

    # ==================== Internal ====================

    async def _cleanup_loop(self):
        """Background task to cleanup unused feeds."""
        while self._is_running:
            try:
                await self._cleanup_unused_feeds()
                await asyncio.sleep(self._cleanup_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}", exc_info=True)
                await asyncio.sleep(self._cleanup_interval)

    async def _cleanup_unused_feeds(self):
        """Clean up feeds that haven't been accessed within timeout."""
        current_time = time.time()
        feeds_to_remove = []

        for feed_key, last_access_time in self._last_access_times.items():
            if current_time - last_access_time > self._feed_timeout:
                feeds_to_remove.append(feed_key)

        for feed_key in feeds_to_remove:
            try:
                feed_type, config = self._feed_configs[feed_key]

                if feed_type == FeedType.CANDLES and feed_key in self._candle_feeds:
                    self._candle_feeds[feed_key].stop()
                    del self._candle_feeds[feed_key]

                del self._last_access_times[feed_key]
                del self._feed_configs[feed_key]

                logger.info(f"Cleaned up unused {feed_type.value} feed: {feed_key}")

            except Exception as e:
                logger.error(f"Error cleaning up feed {feed_key}: {e}", exc_info=True)

        if feeds_to_remove:
            logger.info(f"Cleaned up {len(feeds_to_remove)} unused market data feeds")

    def _generate_feed_key(
            self,
            feed_type: FeedType,
            connector: str,
            trading_pair: str,
            interval: str = None
    ) -> str:
        """Generate a unique key for a market data feed."""
        if interval:
            return f"{feed_type.value}_{connector}_{trading_pair}_{interval}"
        return f"{feed_type.value}_{connector}_{trading_pair}"

    # ==================== Properties ====================

    @property
    def quote_token(self) -> str:
        """The global quote token everything is valued in."""
        return self._quote_token

    @property
    def connector_service(self) -> "UnifiedConnectorService":
        """Get the connector service instance."""
        return self._connector_service

    # ==================== Order Book Tracker Diagnostics ====================

    def get_order_book_tracker_diagnostics(
            self,
            connector_name: str,
            account_name: Optional[str] = None
    ) -> Dict:
        """
        Get diagnostics for a connector's order book tracker.

        Args:
            connector_name: Exchange connector name
            account_name: Optional account name for trading connector preference

        Returns:
            Dictionary with diagnostic information
        """
        return self._connector_service.get_order_book_tracker_diagnostics(
            connector_name=connector_name,
            account_name=account_name
        )

    async def restart_order_book_tracker(
            self,
            connector_name: str,
            account_name: Optional[str] = None
    ) -> Dict:
        """
        Restart the order book tracker for a connector.

        Args:
            connector_name: Exchange connector name
            account_name: Optional account name for trading connector preference

        Returns:
            Dictionary with restart status
        """
        return await self._connector_service.restart_order_book_tracker(
            connector_name=connector_name,
            account_name=account_name
        )
