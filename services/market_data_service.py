"""
Market Data Service - Centralized market data access with proper connector integration.

This service provides access to market data (candles, order books, prices, trading rules)
using the UnifiedConnectorService to ensure proper connector usage.
"""
import asyncio
import time
import logging
from typing import Dict, Optional, List, Any, Tuple
from decimal import Decimal
from enum import Enum

from database.connection import AsyncDatabaseManager
from database.repositories import OrderBookRepository
from hummingbot.core.rate_oracle.rate_oracle import RateOracle
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory


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
        rate_oracle: RateOracle,
        db_manager: AsyncDatabaseManager,
        cleanup_interval: int = 300,
        feed_timeout: int = 600
    ):
        """
        Initialize the MarketDataService.

        Args:
            connector_service: UnifiedConnectorService for connector access
            rate_oracle: RateOracle instance for price conversions
            cleanup_interval: How often to run cleanup (seconds, default: 5 minutes)
            feed_timeout: How long to keep unused feeds alive (seconds, default: 10 minutes)
            db_manager: AsyncDatabaseManager for database access
        """
        self._connector_service = connector_service
        self._rate_oracle = rate_oracle
        self._cleanup_interval = cleanup_interval
        self._feed_timeout = feed_timeout
        self._db_manager = db_manager
        # Candle feeds management
        self._candle_feeds: Dict[str, Any] = {}
        self._last_access_times: Dict[str, float] = {}
        self._feed_configs: Dict[str, Tuple[FeedType, Any]] = {}

        # Background tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._is_running = False

        logger.info("MarketDataService initialized")

    # ==================== Lifecycle ====================

    def start(self):
        """Start the market data service."""
        if not self._is_running:
            self._is_running = True
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            self._rate_oracle.start()
            logger.info(
                f"MarketDataService started with cleanup_interval={self._cleanup_interval}s, "
                f"feed_timeout={self._feed_timeout}s"
            )

    def stop(self):
        """Stop the market data service and cleanup all feeds."""
        self._is_running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None

        # Stop all candle feeds
        for feed_key, feed in self._candle_feeds.items():
            try:
                feed.stop()
            except Exception as e:
                logger.error(f"Error stopping candle feed {feed_key}: {e}")

        self._candle_feeds.clear()
        self._last_access_times.clear()
        self._feed_configs.clear()

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

    def get_candles_feed(self, config: CandlesConfig):
        """
        Get or create a candles feed.

        Args:
            config: CandlesConfig for the desired feed

        Returns:
            Candle feed instance
        """
        feed_key = self._generate_feed_key(
            FeedType.CANDLES, config.connector, config.trading_pair, config.interval
        )

        self._last_access_times[feed_key] = time.time()
        self._feed_configs[feed_key] = (FeedType.CANDLES, config)

        if feed_key not in self._candle_feeds:
            feed = CandlesFactory.get_candle(config)
            feed.start()
            self._candle_feeds[feed_key] = feed
            logger.info(f"Created candle feed: {feed_key}")

        return self._candle_feeds[feed_key]

    def get_candles_df(
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

        feed = self.get_candles_feed(config)
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

    def get_rate(self, base: str, quote: str = "USD") -> Optional[Decimal]:
        """
        Get exchange rate from rate oracle.

        Args:
            base: Base currency
            quote: Quote currency (default: USD)

        Returns:
            Exchange rate or None
        """
        try:
            return self._rate_oracle.get_pair_rate(f"{base}-{quote}")
        except Exception as e:
            logger.debug(f"Rate not available for {base}-{quote}: {e}")
            return None

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
                        "next_funding_time": float(funding_info.next_funding_utc_timestamp) if funding_info.next_funding_utc_timestamp else None,
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
    def rate_oracle(self) -> RateOracle:
        """Get the rate oracle instance."""
        return self._rate_oracle

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
    
    async def get_spread_averages(
        self, 
        pairs: List[str],
        connectors: List[str],
        window_hours: int = 24
    ) -> List[Dict]:
        """
        Calculate average spread statistics grouped by trading pair and connector.
        
        Args:
            pairs: List of trading pairs to filter
            connectors: List of connectors to filter
            window_hours: Time window in hours for aggregation
            
        Returns:
            List of spread average data dictionaries
        """
        try:
            # Calculate time window cutoff (convert to milliseconds)
            current_time = int(time.time() * 1000)
            cutoff_time = current_time - (window_hours * 3600 * 1000)
            
            async with self._db_manager.get_session_context() as session:
                orderbook_repo = OrderBookRepository(session)
                
                # Collect samples grouped by (pair, connector) using a dict
                spread_data = []
                for pair in pairs:
                    for connector in connectors:
                        samples = await orderbook_repo.get_spread_samples(
                            pair=pair,
                            connector=connector,
                            start_timestamp=cutoff_time
                        )
                        # Only store if we have samples with spread data
                        spread_values = [float(samples.spread) for samples in samples if samples.spread is not None]
                        if not spread_values:
                            continue

                        avg_spread = sum(spread_values) / len(spread_values)
                        min_spread = min(spread_values)
                        max_spread = max(spread_values)
                        
                        spread_data.append({
                            "pair": pair,
                            "connector": connector,
                            "avg_spread": Decimal(f"{avg_spread:.6f}"),
                            "min_spread": Decimal(f"{min_spread:.6f}"),
                            "max_spread": Decimal(f"{max_spread:.6f}"),
                            "sample_count": len(spread_values)
                        })
                
                logger.debug(f"Calculated spread averages for {len(spread_data)} pairs")
                return spread_data
                
        except Exception as e:
            raise


    async def get_spread_data(
        self,
        pair: str,
        connector: str,
        limit: int = 100
    ) -> Dict:
        """
        Get raw spread samples from database.
        
        Args:
            pair: Trading pair filter
            connector: Connector filter
            limit: Maximum number of records to return
            
        Returns:
            Dictionary with spread data and count
        """
        await self._db_manager.ensure_initialized()
        
        try:
            async with self._db_manager.get_session_context() as session:
                orderbook_repo = OrderBookRepository(session)
                
                # Get spread samples
                samples = await orderbook_repo.get_spread_samples(
                    pair=pair,
                    connector=connector,
                    limit=limit
                )
                
                # Convert to dictionaries
                data = [orderbook_repo.to_dict(sample) for sample in samples]
                
                logger.debug(f"Retrieved {len(data)} spread samples")
                return {
                    "data": data,
                    "count": len(data)
                }
                
        except Exception as e:
            raise
