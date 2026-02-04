import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from fastapi import HTTPException
from hummingbot.client.config.config_crypt import ETHKeyFileSecretManger
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType

from config import settings
from database import AccountRepository, AsyncDatabaseManager, FundingRepository, OrderRepository, TradeRepository
from services.gateway_client import GatewayClient
from services.gateway_transaction_poller import GatewayTransactionPoller
from utils.file_system import fs_util

# Create module-specific logger
logger = logging.getLogger(__name__)


class AccountTradingInterface:
    """
    ScriptStrategyBase-compatible interface for executor trading.

    This class provides the exact interface that Hummingbot executors expect
    from a strategy object, backed by AccountsService resources.

    IMPORTANT: This class does NOT maintain its own connector cache. Instead, it
    uses the shared ConnectorManager via AccountsService which is the single source
    of truth for all connector instances.

    Executors use the following interface from strategy:
    - current_timestamp: float property
    - buy(connector_name, trading_pair, amount, order_type, price, position_action) -> str
    - sell(connector_name, trading_pair, amount, order_type, price, position_action) -> str
    - cancel(connector_name, trading_pair, order_id) -> str
    - get_active_orders(connector_name) -> List

    ExecutorBase also accesses:
    - connectors: Dict[str, ConnectorBase] (accessed directly in ExecutorBase.__init__)
    """

    def __init__(
        self,
        accounts_service: 'AccountsService',
        account_name: str
    ):
        """
        Initialize AccountTradingInterface.

        Args:
            accounts_service: AccountsService instance for connector access
            account_name: Account to use for connectors
        """
        self._accounts_service = accounts_service
        self._account_name = account_name

        # Track active markets (connector_name -> set of trading_pairs)
        self._markets: Dict[str, Set[str]] = {}

        # Timestamp tracking
        self._current_timestamp: float = time.time()

        # Lock for async operations
        self._lock = asyncio.Lock()

    @property
    def account_name(self) -> str:
        """Return the account name for this trading interface."""
        return self._account_name

    @property
    def connectors(self) -> Dict[str, ConnectorBase]:
        """
        Return connectors for this account from the connector service.

        This returns the actual connectors that are already initialized and running,
        avoiding any duplicate caching or connector management.
        """
        if not self._accounts_service._connector_service:
            return {}
        all_connectors = self._accounts_service._connector_service.get_all_trading_connectors()
        return all_connectors.get(self._account_name, {})

    @property
    def markets(self) -> Dict[str, Set[str]]:
        """Return active markets configuration."""
        return self._markets

    @property
    def current_timestamp(self) -> float:
        """Return current timestamp (updated by control loop)."""
        return self._current_timestamp

    def update_timestamp(self):
        """Update the current timestamp. Called by ExecutorService control loop."""
        self._current_timestamp = time.time()

    async def ensure_connector(self, connector_name: str) -> ConnectorBase:
        """
        Ensure connector is loaded and available.

        This method uses the connector service which already caches connectors.
        It also ensures the MarketDataProvider has access to the connector for
        order book initialization.

        Args:
            connector_name: Name of the connector

        Returns:
            The connector instance
        """
        # Get connector from connector service (already cached there)
        connector = await self._accounts_service._connector_service.get_trading_connector(
            self._account_name,
            connector_name
        )
        return connector

    async def add_market(
        self,
        connector_name: str,
        trading_pair: str,
        order_book_timeout: float = 10.0
    ):
        """
        Add a trading pair to active markets with full order book support.

        This method ensures:
        1. Connector is loaded
        2. Order book is initialized and has valid data
        3. Rate sources are initialized for price feeds

        Args:
            connector_name: Name of the connector
            trading_pair: Trading pair to add
            order_book_timeout: Timeout in seconds to wait for order book data
        """
        await self.ensure_connector(connector_name)

        if connector_name not in self._markets:
            self._markets[connector_name] = set()

        # Check if already tracking this pair
        if trading_pair in self._markets[connector_name]:
            logger.debug(f"Market {connector_name}/{trading_pair} already active")
            return

        self._markets[connector_name].add(trading_pair)

        # Get connector and its order book tracker
        connector = self.connectors.get(connector_name)
        if not connector:
            raise ValueError(f"Connector {connector_name} not available. Check credentials.")
        tracker = connector.order_book_tracker

        # Check if order book already exists, if not initialize it dynamically
        if trading_pair in tracker.order_books:
            logger.debug(f"Order book already exists for {connector_name}/{trading_pair}")
        else:
            logger.debug(f"Order book not found for {connector_name}/{trading_pair}, initializing dynamically")
            market_data_service = self._accounts_service._market_data_service
            if market_data_service:
                try:
                    success = await market_data_service.initialize_order_book(
                        connector_name, trading_pair,
                        account_name=self._account_name,
                        timeout=order_book_timeout
                    )
                    if not success:
                        logger.warning(f"Order book for {connector_name}/{trading_pair} not ready after timeout")
                except Exception as e:
                    logger.warning(f"Exception initializing order book: {e}")

        # Register the trading pair with the connector
        self._register_trading_pair_with_connector(connector, trading_pair)

    async def _wait_for_order_book_ready(
        self,
        tracker,
        trading_pair: str,
        timeout: float = 30.0
    ) -> bool:
        """
        Wait for an order book to have valid data.

        Args:
            tracker: Order book tracker instance
            trading_pair: Trading pair to wait for
            timeout: Maximum time to wait in seconds

        Returns:
            True if order book is ready, False if timeout
        """
        import asyncio
        waited = 0
        interval = 0.5
        while waited < timeout:
            if trading_pair in tracker.order_books:
                ob = tracker.order_books[trading_pair]
                try:
                    bids, asks = ob.snapshot
                    if len(bids) > 0 and len(asks) > 0:
                        logger.info(f"Order book for {trading_pair} is ready with {len(bids)} bids and {len(asks)} asks")
                        return True
                except Exception:
                    pass
            await asyncio.sleep(interval)
            waited += interval
        logger.warning(f"Timeout waiting for {trading_pair} order book to be ready")
        return False

    def _register_trading_pair_with_connector(
        self,
        connector: ConnectorBase,
        trading_pair: str
    ):
        """
        Register a trading pair with the connector's internal structures.

        This is needed for methods like get_order_book() to work properly.
        Different connector types may store trading pairs differently.

        Args:
            connector: The connector instance
            trading_pair: Trading pair to register
        """
        if trading_pair not in connector._trading_pairs:
            connector._trading_pairs.append(trading_pair)
            logger.debug(f"Registered {trading_pair} with connector {type(connector).__name__}")

    async def remove_market(
        self,
        connector_name: str,
        trading_pair: str,
        remove_order_book: bool = True
    ):
        """
        Remove a trading pair from active markets and optionally cleanup order book.

        Args:
            connector_name: Name of the connector
            trading_pair: Trading pair to remove
            remove_order_book: Whether to remove the order book (default True)
        """
        if connector_name not in self._markets:
            return

        self._markets[connector_name].discard(trading_pair)
        if not self._markets[connector_name]:
            del self._markets[connector_name]

        # Remove order book if requested
        if remove_order_book:
            market_data_service = self._accounts_service._market_data_service
            if market_data_service:
                try:
                    success = await market_data_service.remove_trading_pair(
                        connector_name,
                        trading_pair,
                        account_name=self._account_name
                    )
                    if success:
                        logger.info(f"Removed order book for {connector_name}/{trading_pair}")
                    else:
                        logger.debug(f"Order book for {trading_pair} was not being tracked")
                except Exception as e:
                    logger.warning(f"Failed to remove order book for {trading_pair}: {e}")

    # ========================================
    # ScriptStrategyBase-compatible methods
    # These are called by executors via self._strategy.method()
    # ========================================

    def buy(
        self,
        connector_name: str,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal = Decimal("NaN"),
        position_action: PositionAction = PositionAction.NIL
    ) -> str:
        """
        Place a buy order.

        Args:
            connector_name: Name of the connector
            trading_pair: Trading pair
            amount: Order amount in base currency
            order_type: Type of order (LIMIT, MARKET, etc.)
            price: Order price (for limit orders)
            position_action: Position action for perpetuals

        Returns:
            Client order ID
        """
        connector = self.connectors.get(connector_name)
        if not connector:
            raise ValueError(f"Connector {connector_name} not loaded. Call ensure_connector first.")

        return connector.buy(
            trading_pair=trading_pair,
            amount=amount,
            order_type=order_type,
            price=price,
            position_action=position_action
        )

    def sell(
        self,
        connector_name: str,
        trading_pair: str,
        amount: Decimal,
        order_type: OrderType,
        price: Decimal = Decimal("NaN"),
        position_action: PositionAction = PositionAction.NIL
    ) -> str:
        """
        Place a sell order.

        Args:
            connector_name: Name of the connector
            trading_pair: Trading pair
            amount: Order amount in base currency
            order_type: Type of order (LIMIT, MARKET, etc.)
            price: Order price (for limit orders)
            position_action: Position action for perpetuals

        Returns:
            Client order ID
        """
        connector = self.connectors.get(connector_name)
        if not connector:
            raise ValueError(f"Connector {connector_name} not loaded. Call ensure_connector first.")

        return connector.sell(
            trading_pair=trading_pair,
            amount=amount,
            order_type=order_type,
            price=price,
            position_action=position_action
        )

    def cancel(
        self,
        connector_name: str,
        trading_pair: str,
        order_id: str
    ) -> str:
        """
        Cancel an order.

        Args:
            connector_name: Name of the connector
            trading_pair: Trading pair
            order_id: Client order ID to cancel

        Returns:
            Client order ID that was cancelled
        """
        connector = self.connectors.get(connector_name)
        if not connector:
            raise ValueError(f"Connector {connector_name} not loaded. Call ensure_connector first.")

        return connector.cancel(trading_pair=trading_pair, client_order_id=order_id)

    def get_active_orders(self, connector_name: str) -> List:
        """
        Get active orders for a connector.

        Args:
            connector_name: Name of the connector

        Returns:
            List of active in-flight orders
        """
        connector = self.connectors.get(connector_name)
        if not connector:
            return []
        return list(connector.in_flight_orders.values())

    # ========================================
    # Additional helper methods
    # ========================================

    def get_connector(self, connector_name: str) -> Optional[ConnectorBase]:
        """
        Get a connector by name from the shared ConnectorManager.

        Args:
            connector_name: Name of the connector

        Returns:
            The connector instance or None if not loaded
        """
        return self.connectors.get(connector_name)

    def is_connector_loaded(self, connector_name: str) -> bool:
        """
        Check if a connector is loaded in the shared ConnectorManager.

        Args:
            connector_name: Name of the connector

        Returns:
            True if connector is loaded
        """
        return connector_name in self.connectors

    def get_all_trading_pairs(self) -> Dict[str, Set[str]]:
        """
        Get all active trading pairs by connector.

        Returns:
            Dictionary mapping connector names to sets of trading pairs
        """
        return {k: v.copy() for k, v in self._markets.items()}

    async def cleanup(self):
        """
        Cleanup resources. Called when shutting down.

        Note: This does NOT clean up connectors since they are managed by the
        shared ConnectorManager, not by AccountTradingInterface.
        """
        # Clear only local state (markets tracking)
        self._markets.clear()
        logger.info(f"AccountTradingInterface cleanup completed for account {self._account_name}")


class AccountsService:
    """
    This class is responsible for managing all the accounts that are connected to the trading system. It is responsible
    to initialize all the connectors that are connected to each account, keep track of the balances of each account and
    update the balances of each account.
    """
    default_quotes = {
        "hyperliquid": "USD",
        "hyperliquid_perpetual": "USDC",
        "xrpl": "RLUSD",
        "kraken": "USD",
    }
    gateway_default_pricing_connector = {
        "ethereum": "uniswap/router",
        "solana": "jupiter/router",
    }
    potential_wrapped_tokens = ["ETH", "SOL", "BNB", "POL", "AVAX", "FTM", "ONE", "GLMR", "MOVR"]
    
    # Cache for storing last successful prices by trading pair
    _last_known_prices = {}

    def __init__(self,
                 account_update_interval: int = 5,
                 default_quote: str = "USDT",
                 gateway_url: str = "http://localhost:15888"):
        """
        Initialize the AccountsService.

        Args:
            account_update_interval: How often to update account states in minutes (default: 5)
            default_quote: Default quote currency for trading pairs (default: "USDT")
            gateway_url: URL for Gateway service (default: "http://localhost:15888")
        """
        self.secrets_manager = ETHKeyFileSecretManger(settings.security.config_password)
        self.accounts_state = {}
        self.update_account_state_interval = account_update_interval * 60
        self.order_status_poll_interval = 60  # Poll order status every 1 minute
        self.default_quote = default_quote
        self._update_account_state_task: Optional[asyncio.Task] = None
        self._order_status_polling_task: Optional[asyncio.Task] = None

        # Database setup for account states and orders
        self.db_manager = AsyncDatabaseManager(settings.database.url)
        self._db_initialized = False

        # Services injected from main.py
        self._connector_service = None  # UnifiedConnectorService
        self._market_data_service = None  # MarketDataService
        self._trading_service = None  # TradingService

        # Initialize Gateway client
        self.gateway_client = GatewayClient(gateway_url)

        # Initialize Gateway transaction poller
        self.gateway_tx_poller = GatewayTransactionPoller(
            db_manager=self.db_manager,
            gateway_client=self.gateway_client,
            poll_interval=10,  # Poll every 10 seconds for transactions
            position_poll_interval=60,  # Poll every 1 minute for positions
            max_retry_age=3600  # Stop retrying after 1 hour
        )
        self._gateway_poller_started = False

        # Trading interfaces per account (for executor use)
        self._trading_interfaces: Dict[str, AccountTradingInterface] = {}

    def get_trading_interface(self, account_name: str) -> AccountTradingInterface:
        """
        Get or create a trading interface for the specified account.

        This interface provides ScriptStrategyBase-compatible methods
        that executors can use for trading operations.

        Args:
            account_name: Account to get trading interface for

        Returns:
            AccountTradingInterface instance for the account
        """
        if account_name not in self._trading_interfaces:
            self._trading_interfaces[account_name] = AccountTradingInterface(
                accounts_service=self,
                account_name=account_name
            )
        return self._trading_interfaces[account_name]

    async def ensure_db_initialized(self):
        """Ensure database is initialized before using it."""
        if not self._db_initialized:
            await self.db_manager.create_tables()
            self._db_initialized = True
    
    def get_accounts_state(self):
        return self.accounts_state

    def get_default_market(self, token: str, connector_name: str) -> str:
        if token.startswith("LD") and token != "LDO":
            # These tokens are staked in binance earn
            token = token[2:]
        quote = self.default_quotes.get(connector_name, self.default_quote)
        return f"{token}-{quote}"

    def start(self):
        """
        Start the loop that updates the account state at a fixed interval.
        Note: Balance updates are now handled by manual connector state updates.
        :return:
        """
        # Start the update loop which will call check_all_connectors
        self._update_account_state_task = asyncio.create_task(self.update_account_state_loop())

        # Start order status polling loop (every 1 minute)
        self._order_status_polling_task = asyncio.create_task(self.order_status_polling_loop())
        logger.info("Order status polling started (1 minute interval)")

        # Start Gateway transaction poller
        if not self._gateway_poller_started:
            asyncio.create_task(self._start_gateway_poller())
            self._gateway_poller_started = True
            logger.info("Gateway transaction poller startup initiated")

    async def _start_gateway_poller(self):
        """Start the Gateway transaction poller (async helper)."""
        try:
            await self.gateway_tx_poller.start()
            logger.info("Gateway transaction poller started successfully")
        except Exception as e:
            logger.error(f"Error starting Gateway transaction poller: {e}", exc_info=True)

    async def stop(self):
        """
        Stop all accounts service tasks and cleanup resources.
        This is the main cleanup method that should be called during application shutdown.
        """
        logger.info("Stopping AccountsService...")

        # Stop the account state update loop
        if self._update_account_state_task:
            self._update_account_state_task.cancel()
            self._update_account_state_task = None
            logger.info("Stopped account state update loop")

        # Stop the order status polling loop
        if self._order_status_polling_task:
            self._order_status_polling_task.cancel()
            self._order_status_polling_task = None
            logger.info("Stopped order status polling loop")

        # Stop Gateway transaction poller
        if self._gateway_poller_started:
            try:
                await self.gateway_tx_poller.stop()
                logger.info("Gateway transaction poller stopped")
                self._gateway_poller_started = False
            except Exception as e:
                logger.error(f"Error stopping Gateway transaction poller: {e}", exc_info=True)

        # Cleanup trading interfaces
        for interface in self._trading_interfaces.values():
            await interface.cleanup()
        self._trading_interfaces.clear()
        logger.info("Cleaned up trading interfaces")

        # Stop all connectors through the connector service
        if self._connector_service:
            await self._connector_service.stop_all()

        logger.info("AccountsService stopped successfully")

    async def _refresh_and_get_tokens_info(self, connector, connector_name: str, account_name: str) -> List[Dict]:
        """Refresh connector state from exchange, then get token info with prices.

        Combines the connector state refresh and token info retrieval into a
        single awaitable so both can run in parallel across all connectors.
        """
        if self._connector_service:
            try:
                await self._connector_service._update_connector_state(connector, connector_name, account_name)
            except Exception as e:
                logger.error(f"Error refreshing {connector_name}, using stale data: {e}")
        return await self._get_connector_tokens_info(connector, connector_name)

    async def update_account_state_loop(self):
        """
        The loop that updates the account state at a fixed interval.
        Performs connector state refresh + token info retrieval in a single parallel pass.
        """
        while True:
            try:
                await self.check_all_connectors()

                # Single parallel pass: refresh connector state + get token info + gateway
                all_connectors = self._connector_service.get_all_trading_connectors() if self._connector_service else {}
                tasks = []
                task_meta = []  # (account_name, connector_name)

                for account_name, connectors in all_connectors.items():
                    if account_name not in self.accounts_state:
                        self.accounts_state[account_name] = {}
                    for connector_name, connector in connectors.items():
                        tasks.append(self._refresh_and_get_tokens_info(connector, connector_name, account_name))
                        task_meta.append((account_name, connector_name))

                has_connector_tasks = len(tasks) > 0
                tasks.append(self._update_gateway_balances())
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process connector results (last result is always gateway)
                connector_results = results[:-1] if has_connector_tasks else []
                for (account_name, connector_name), result in zip(task_meta, connector_results):
                    if isinstance(result, Exception):
                        logger.error(f"Error updating {connector_name} in {account_name}: {result}")
                        self.accounts_state[account_name][connector_name] = []
                    else:
                        self.accounts_state[account_name][connector_name] = result

                gw_result = results[-1]
                if isinstance(gw_result, Exception):
                    logger.error(f"Error updating gateway balances: {gw_result}")

                await self.dump_account_state()
            except Exception as e:
                logger.error(f"Error updating account state: {e}")
            finally:
                await asyncio.sleep(self.update_account_state_interval)

    async def order_status_polling_loop(self):
        """
        Sync order state to database for all connectors at a frequent interval (1 minute).

        The connector's built-in _lost_orders_update_polling_loop already polls the exchange.
        This loop just syncs that state to our database and cleans up closed orders.
        """
        while True:
            try:
                if self._connector_service:
                    await self._connector_service.sync_all_orders_to_database()
            except Exception as e:
                logger.error(f"Error syncing order state to database: {e}")
            finally:
                await asyncio.sleep(self.order_status_poll_interval)

    async def dump_account_state(self):
        """
        Save the current account state to the database.
        All account/connector combinations from the same snapshot will use the same timestamp.
        :return:
        """
        await self.ensure_db_initialized()
        
        try:
            # Generate a single timestamp for this entire snapshot
            snapshot_timestamp = datetime.now(timezone.utc)
            
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                
                # Save each account-connector combination with the same timestamp
                for account_name, connectors in self.accounts_state.items():
                    for connector_name, tokens_info in connectors.items():
                        if tokens_info:  # Only save if there's token data
                            await repository.save_account_state(account_name, connector_name, tokens_info, snapshot_timestamp)
                            
        except Exception as e:
            logger.error(f"Error saving account state to database: {e}")
            # Re-raise the exception since we no longer have a fallback
            raise

    async def load_account_state_history(self,
                                        limit: Optional[int] = None,
                                        cursor: Optional[str] = None,
                                        start_time: Optional[datetime] = None,
                                        end_time: Optional[datetime] = None,
                                        interval: str = "5m"):
        """
        Load the account state history from the database with pagination and interval sampling.

        Args:
            limit: Maximum number of records to return
            cursor: Cursor for pagination
            start_time: Start time filter
            end_time: End time filter
            interval: Sampling interval (5m, 15m, 30m, 1h, 4h, 12h, 1d)

        :return: Tuple of (data, next_cursor, has_more).
        """
        await self.ensure_db_initialized()

        try:
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                return await repository.get_account_state_history(
                    limit=limit,
                    cursor=cursor,
                    start_time=start_time,
                    end_time=end_time,
                    interval=interval
                )
        except Exception as e:
            logger.error(f"Error loading account state history from database: {e}")
            # Return empty result since we no longer have a fallback
            return [], None, False

    async def check_all_connectors(self):
        """
        Check all available credentials for all accounts and ensure connectors are initialized.
        This method is idempotent - it only initializes missing connectors.
        """
        for account_name in self.list_accounts():
            await self._ensure_account_connectors_initialized(account_name)

    async def _ensure_account_connectors_initialized(self, account_name: str):
        """
        Ensure all connectors for a specific account are initialized.
        This delegates to the connector service for actual initialization.

        :param account_name: The name of the account to initialize connectors for.
        """
        if not self._connector_service:
            return

        # Initialize missing connectors
        for connector_name in self._connector_service.list_available_credentials(account_name):
            try:
                # Only initialize if connector doesn't exist
                if not self._connector_service.is_trading_connector_initialized(account_name, connector_name):
                    # Get connector will now handle all initialization
                    await self._connector_service.get_trading_connector(account_name, connector_name)
            except Exception as e:
                logger.error(f"Error initializing connector {connector_name} for account {account_name}: {e}")

    async def update_account_state(
        self,
        skip_gateway: bool = False,
        account_names: Optional[List[str]] = None,
        connector_names: Optional[List[str]] = None
    ):
        """Update account state for filtered connectors and optionally Gateway wallets.

        Args:
            skip_gateway: If True, skip Gateway wallet balance updates for faster CEX-only queries.
            account_names: If provided, only update these accounts. If None, update all accounts.
            connector_names: If provided, only update these connectors. If None, update all connectors.
                            For Gateway, this filters by chain-network (e.g., 'solana-mainnet-beta').
        """
        all_connectors = self._connector_service.get_all_trading_connectors() if self._connector_service else {}

        # Prepare parallel tasks
        tasks = []
        task_meta = []  # (account_name, connector_name)

        for account_name, connectors in all_connectors.items():
            # Filter by account_names if specified
            if account_names and account_name not in account_names:
                continue

            if account_name not in self.accounts_state:
                self.accounts_state[account_name] = {}
            for connector_name, connector in connectors.items():
                # Filter by connector_names if specified
                if connector_names and connector_name not in connector_names:
                    continue

                tasks.append(self._get_connector_tokens_info(connector, connector_name))
                task_meta.append((account_name, connector_name))

        # Execute connectors + gateway in parallel (unless skip_gateway is True)
        if skip_gateway:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            # Pass connector_names filter to gateway for chain-network filtering
            results = await asyncio.gather(
                *tasks,
                self._update_gateway_balances(chain_networks=connector_names),
                return_exceptions=True
            )
            # Remove gateway result from processing (it handles its own state internally)
            results = results[:-1]

        # Process results
        for (account_name, connector_name), result in zip(task_meta, results):
            if isinstance(result, Exception):
                logger.error(f"Error updating balances for connector {connector_name} in account {account_name}: {result}")
                self.accounts_state[account_name][connector_name] = []
            else:
                self.accounts_state[account_name][connector_name] = result

    async def _get_connector_tokens_info(self, connector, connector_name: str) -> List[Dict]:
        """Get token info from a connector instance using RateOracle cached prices.

        Tries the RateOracle (instant, in-memory) first for each token.
        Only falls back to a batch exchange call for tokens the oracle can't price.
        """
        balances = [{"token": key, "units": value} for key, value in connector.get_all_balances().items() if
                    value != Decimal("0") and key not in settings.banned_tokens]

        tokens_info = []
        missing_pairs = []  # trading pairs the oracle can't price
        missing_indices = []  # indices into tokens_info that need patching

        for balance in balances:
            token = balance["token"]
            if "USD" in token:
                price = Decimal("1")
            else:
                # Try RateOracle first (instant, cached)
                rate = None
                if self._market_data_service:
                    rate = self._market_data_service.get_rate(token, "USDT")
                if rate and rate > 0:
                    price = rate
                else:
                    # Queue for fallback batch fetch from exchange
                    market = self.get_default_market(token, connector_name)
                    missing_pairs.append(market)
                    missing_indices.append(len(tokens_info))
                    price = None  # resolved below

            tokens_info.append({
                "token": token,
                "units": float(balance["units"]),
                "price": float(price) if price is not None else 0.0,
                "value": float(price * balance["units"]) if price is not None else 0.0,
                "available_units": float(connector.get_available_balance(token))
            })

        # Batch-fetch only the missing prices from the exchange
        if missing_pairs:
            fallback_prices = await self._safe_get_last_traded_prices(connector, missing_pairs)
            for pair_idx, info_idx in enumerate(missing_indices):
                market = missing_pairs[pair_idx]
                price = Decimal(str(fallback_prices.get(market, 0)))
                tokens_info[info_idx]["price"] = float(price)
                tokens_info[info_idx]["value"] = float(price * Decimal(str(tokens_info[info_idx]["units"])))

        return tokens_info
    
    async def _safe_get_last_traded_prices(self, connector, trading_pairs, timeout=10):
        """Safely get last traded prices with timeout and error handling.
        Fetches each pair individually via gather so one bad pair doesn't kill the rest."""

        async def _fetch_single(pair):
            return pair, await connector._get_last_traded_price(trading_pair=pair)

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[_fetch_single(p) for p in trading_pairs], return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error(f"Timeout getting last traded prices for trading pairs {trading_pairs}")
            return self._get_fallback_prices(trading_pairs)

        last_traded = {}
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Failed to get price for a pair: {result}")
                continue
            pair, price = result
            if price and price > 0:
                self._last_known_prices[pair] = price
            last_traded[pair] = price

        # Fill in fallbacks for any pairs that failed
        for pair in trading_pairs:
            if pair not in last_traded:
                if pair in self._last_known_prices:
                    last_traded[pair] = self._last_known_prices[pair]
                    logger.info(f"Using cached price {self._last_known_prices[pair]} for {pair}")
                else:
                    last_traded[pair] = Decimal("0")
                    logger.warning(f"No cached price available for {pair}, using 0")

        return last_traded
    
    def _get_fallback_prices(self, trading_pairs):
        """Get fallback prices using cached values, only setting to 0 if no previous price exists."""
        fallback_prices = {}
        for pair in trading_pairs:
            if pair in self._last_known_prices:
                fallback_prices[pair] = self._last_known_prices[pair]
                logger.info(f"Using cached price {self._last_known_prices[pair]} for {pair}")
            else:
                fallback_prices[pair] = Decimal("0")
                logger.warning(f"No cached price available for {pair}, using 0")
        return fallback_prices

    def get_connector_config_map(self, connector_name: str):
        """
        Get the connector config map for the specified connector.
        :param connector_name: The name of the connector.
        :return: The connector config map.
        """
        from services.unified_connector_service import UnifiedConnectorService
        return UnifiedConnectorService.get_connector_config_map(connector_name)

    async def add_credentials(self, account_name: str, connector_name: str, credentials: dict):
        """
        Add or update connector credentials and initialize the connector with validation.

        :param account_name: The name of the account.
        :param connector_name: The name of the connector.
        :param credentials: Dictionary containing the connector credentials.
        :raises Exception: If credentials are invalid or connector cannot be initialized.
        """
        if not self._connector_service:
            raise HTTPException(status_code=500, detail="Connector service not initialized")

        try:
            # Update the connector keys (this saves the credentials to file and validates them)
            connector = await self._connector_service.update_connector_keys(account_name, connector_name, credentials)

            await self.update_account_state()
        except Exception as e:
            logger.error(f"Error adding connector credentials for account {account_name}: {e}")
            await self.delete_credentials(account_name, connector_name)
            raise e

    @staticmethod
    def list_accounts():
        """
        List all the accounts that are connected to the trading system.
        :return: List of accounts.
        """
        return fs_util.list_folders('credentials')

    @staticmethod
    def list_credentials(account_name: str):
        """
        List all the credentials that are connected to the specified account.
        :param account_name: The name of the account.
        :return: List of credentials.
        """
        try:
            return [file for file in fs_util.list_files(f'credentials/{account_name}/connectors') if
                    file.endswith('.yml')]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    async def delete_credentials(self, account_name: str, connector_name: str):
        """
        Delete the credentials of the specified connector for the specified account.
        :param account_name:
        :param connector_name:
        :return:
        """
        # Delete credentials file if it exists
        if fs_util.path_exists(f"credentials/{account_name}/connectors/{connector_name}.yml"):
            fs_util.delete_file(directory=f"credentials/{account_name}/connectors", file_name=f"{connector_name}.yml")

        # Always perform cleanup regardless of file existence
        if self._connector_service:
            # Stop the connector if it's running
            await self._connector_service.stop_trading_connector(account_name, connector_name)
            # Clear the connector from cache
            self._connector_service.clear_trading_connector(account_name, connector_name)

        # Remove from account state
        if account_name in self.accounts_state and connector_name in self.accounts_state[account_name]:
            self.accounts_state[account_name].pop(connector_name)

    def add_account(self, account_name: str):
        """
        Add a new account.
        :param account_name:
        :return:
        """
        # Check if account already exists by looking at folders
        if account_name in self.list_accounts():
            raise HTTPException(status_code=400, detail="Account already exists.")
        
        files_to_copy = ["conf_client.yml", "conf_fee_overrides.yml", "hummingbot_logs.yml", ".password_verification"]
        fs_util.create_folder('credentials', account_name)
        fs_util.create_folder(f'credentials/{account_name}', "connectors")
        for file in files_to_copy:
            fs_util.copy_file(f"credentials/master_account/{file}", f"credentials/{account_name}/{file}")
        
        # Initialize account state
        self.accounts_state[account_name] = {}

    async def delete_account(self, account_name: str):
        """
        Delete the specified account.
        :param account_name:
        :return:
        """
        # Stop all connectors for this account
        if self._connector_service:
            for connector_name in self._connector_service.list_account_connectors(account_name):
                await self._connector_service.stop_trading_connector(account_name, connector_name)
            # Clear all connectors for this account from cache
            self._connector_service.clear_trading_connector(account_name)

        # Delete account folder
        fs_util.delete_folder('credentials', account_name)

        # Remove from account state
        if account_name in self.accounts_state:
            self.accounts_state.pop(account_name)
    
    async def get_account_current_state(self, account_name: str) -> Dict[str, List[Dict]]:
        """
        Get current state for a specific account from database.
        """
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                return await repository.get_account_current_state(account_name)
        except Exception as e:
            logger.error(f"Error getting account current state: {e}")
            # Fallback to in-memory state
            return self.accounts_state.get(account_name, {})
    
    async def get_account_state_history(self,
                                        account_name: str,
                                        limit: Optional[int] = None,
                                        cursor: Optional[str] = None,
                                        start_time: Optional[datetime] = None,
                                        end_time: Optional[datetime] = None,
                                        interval: str = "5m"):
        """
        Get historical state for a specific account with pagination and interval sampling.

        Args:
            account_name: Account name to filter by
            limit: Maximum number of records to return
            cursor: Cursor for pagination
            start_time: Start time filter
            end_time: End time filter
            interval: Sampling interval (5m, 15m, 30m, 1h, 4h, 12h, 1d)
        """
        await self.ensure_db_initialized()

        try:
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                return await repository.get_account_state_history(
                    account_name=account_name,
                    limit=limit,
                    cursor=cursor,
                    start_time=start_time,
                    end_time=end_time,
                    interval=interval
                )
        except Exception as e:
            logger.error(f"Error getting account state history: {e}")
            return [], None, False
    
    async def get_connector_current_state(self, account_name: str, connector_name: str) -> List[Dict]:
        """
        Get current state for a specific connector.
        """
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                return await repository.get_connector_current_state(account_name, connector_name)
        except Exception as e:
            logger.error(f"Error getting connector current state: {e}")
            # Fallback to in-memory state
            return self.accounts_state.get(account_name, {}).get(connector_name, [])
    
    async def get_connector_state_history(self, 
                                          account_name: str, 
                                          connector_name: str, 
                                          limit: Optional[int] = None,
                                          cursor: Optional[str] = None,
                                          start_time: Optional[datetime] = None,
                                          end_time: Optional[datetime] = None):
        """
        Get historical state for a specific connector with pagination.
        """
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                return await repository.get_account_state_history(
                    account_name=account_name, 
                    connector_name=connector_name,
                    limit=limit,
                    cursor=cursor,
                    start_time=start_time,
                    end_time=end_time
                )
        except Exception as e:
            logger.error(f"Error getting connector state history: {e}")
            return [], None, False
    
    async def get_all_unique_tokens(self) -> List[str]:
        """
        Get all unique tokens across all accounts and connectors.
        """
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                return await repository.get_all_unique_tokens()
        except Exception as e:
            logger.error(f"Error getting unique tokens: {e}")
            # Fallback to in-memory state
            tokens = set()
            for account_data in self.accounts_state.values():
                for connector_data in account_data.values():
                    for token_info in connector_data:
                        tokens.add(token_info.get("token"))
            return sorted(list(tokens))
    
    async def get_token_current_state(self, token: str) -> List[Dict]:
        """
        Get current state of a specific token across all accounts.
        """
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                return await repository.get_token_current_state(token)
        except Exception as e:
            logger.error(f"Error getting token current state: {e}")
            return []
    
    async def get_portfolio_value(self, account_name: Optional[str] = None) -> Dict[str, any]:
        """
        Get total portfolio value, optionally filtered by account.
        """
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                repository = AccountRepository(session)
                return await repository.get_portfolio_value(account_name)
        except Exception as e:
            logger.error(f"Error getting portfolio value: {e}")
            # Fallback to in-memory calculation
            portfolio = {"accounts": {}, "total_value": 0}
            
            accounts_to_process = [account_name] if account_name else self.accounts_state.keys()
            
            for acc_name in accounts_to_process:
                account_value = 0
                if acc_name in self.accounts_state:
                    for connector_data in self.accounts_state[acc_name].values():
                        for token_info in connector_data:
                            account_value += token_info.get("value", 0)
                    portfolio["accounts"][acc_name] = account_value
                    portfolio["total_value"] += account_value
            
            return portfolio
    
    def get_portfolio_distribution(self, account_name: Optional[str] = None) -> Dict[str, any]:
        """
        Get portfolio distribution by tokens with percentages.
        """
        try:
            # Get accounts to process
            accounts_to_process = [account_name] if account_name else list(self.accounts_state.keys())
            
            # Aggregate all tokens across accounts and connectors
            token_values = {}
            total_value = 0
            
            for acc_name in accounts_to_process:
                if acc_name in self.accounts_state:
                    for connector_name, connector_data in self.accounts_state[acc_name].items():
                        for token_info in connector_data:
                            token = token_info.get("token", "")
                            value = token_info.get("value", 0)
                            
                            if token not in token_values:
                                token_values[token] = {
                                    "token": token,
                                    "total_value": 0,
                                    "total_units": 0,
                                    "accounts": {}
                                }
                            
                            token_values[token]["total_value"] += value
                            token_values[token]["total_units"] += token_info.get("units", 0)
                            total_value += value
                            
                            # Track by account
                            if acc_name not in token_values[token]["accounts"]:
                                token_values[token]["accounts"][acc_name] = {
                                    "value": 0,
                                    "units": 0,
                                    "connectors": {}
                                }
                            
                            token_values[token]["accounts"][acc_name]["value"] += value
                            token_values[token]["accounts"][acc_name]["units"] += token_info.get("units", 0)
                            
                            # Track by connector within account
                            if connector_name not in token_values[token]["accounts"][acc_name]["connectors"]:
                                token_values[token]["accounts"][acc_name]["connectors"][connector_name] = {
                                    "value": 0,
                                    "units": 0
                                }
                            
                            token_values[token]["accounts"][acc_name]["connectors"][connector_name]["value"] += value
                            token_values[token]["accounts"][acc_name]["connectors"][connector_name]["units"] += token_info.get("units", 0)
            
            # Calculate percentages
            distribution = []
            for token_data in token_values.values():
                percentage = (token_data["total_value"] / total_value * 100) if total_value > 0 else 0
                
                token_dist = {
                    "token": token_data["token"],
                    "total_value": round(token_data["total_value"], 6),
                    "total_units": token_data["total_units"],
                    "percentage": round(percentage, 4),
                    "accounts": {}
                }
                
                # Add account-level percentages
                for acc_name, acc_data in token_data["accounts"].items():
                    acc_percentage = (acc_data["value"] / total_value * 100) if total_value > 0 else 0
                    token_dist["accounts"][acc_name] = {
                        "value": round(acc_data["value"], 6),
                        "units": acc_data["units"],
                        "percentage": round(acc_percentage, 4),
                        "connectors": {}
                    }
                    
                    # Add connector-level data
                    for conn_name, conn_data in acc_data["connectors"].items():
                        token_dist["accounts"][acc_name]["connectors"][conn_name] = {
                            "value": round(conn_data["value"], 6),
                            "units": conn_data["units"]
                        }
                
                distribution.append(token_dist)
            
            # Sort by value (descending)
            distribution.sort(key=lambda x: x["total_value"], reverse=True)
            
            return {
                "total_portfolio_value": round(total_value, 6),
                "token_count": len(distribution),
                "distribution": distribution,
                "account_filter": account_name if account_name else "all_accounts"
            }
            
        except Exception as e:
            logger.error(f"Error calculating portfolio distribution: {e}")
            return {
                "total_portfolio_value": 0,
                "token_count": 0,
                "distribution": [],
                "account_filter": account_name if account_name else "all_accounts",
                "error": str(e)
            }
    
    def get_account_distribution(self) -> Dict[str, any]:
        """
        Get portfolio distribution by accounts with percentages.
        """
        try:
            account_values = {}
            total_value = 0
            
            for acc_name, account_data in self.accounts_state.items():
                account_value = 0
                connector_values = {}
                
                for connector_name, connector_data in account_data.items():
                    connector_value = 0
                    for token_info in connector_data:
                        value = token_info.get("value", 0)
                        connector_value += value
                        account_value += value
                    
                    connector_values[connector_name] = round(connector_value, 6)
                
                account_values[acc_name] = {
                    "total_value": round(account_value, 6),
                    "connectors": connector_values
                }
                total_value += account_value
            
            # Calculate percentages
            distribution = []
            for acc_name, acc_data in account_values.items():
                percentage = (acc_data["total_value"] / total_value * 100) if total_value > 0 else 0
                
                connector_dist = {}
                for conn_name, conn_value in acc_data["connectors"].items():
                    conn_percentage = (conn_value / total_value * 100) if total_value > 0 else 0
                    connector_dist[conn_name] = {
                        "value": conn_value,
                        "percentage": round(conn_percentage, 4)
                    }
                
                distribution.append({
                    "account": acc_name,
                    "total_value": acc_data["total_value"],
                    "percentage": round(percentage, 4),
                    "connectors": connector_dist
                })
            
            # Sort by value (descending)
            distribution.sort(key=lambda x: x["total_value"], reverse=True)
            
            return {
                "total_portfolio_value": round(total_value, 6),
                "account_count": len(distribution),
                "distribution": distribution
            }
            
        except Exception as e:
            logger.error(f"Error calculating account distribution: {e}")
            return {
                "total_portfolio_value": 0,
                "account_count": 0,
                "distribution": [],
                "error": str(e)
            }
    
    async def place_trade(self, account_name: str, connector_name: str, trading_pair: str,
                         trade_type: TradeType, amount: Decimal, order_type: OrderType = OrderType.LIMIT,
                         price: Optional[Decimal] = None, position_action: PositionAction = PositionAction.OPEN) -> str:
        """
        Place a trade using the specified account and connector.

        Args:
            account_name: Name of the account to trade with
            connector_name: Name of the connector/exchange
            trading_pair: Trading pair (e.g., BTC-USDT)
            trade_type: "BUY" or "SELL"
            amount: Amount to trade
            order_type: "LIMIT", "MARKET", or "LIMIT_MAKER"
            price: Price for limit orders (required for LIMIT and LIMIT_MAKER)
            position_action: Position action for perpetual contracts (OPEN/CLOSE)

        Returns:
            Client order ID assigned by the connector

        Raises:
            HTTPException: If account, connector not found, or trade fails
        """
        # Validate account exists
        if account_name not in self.list_accounts():
            raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")

        if not self._connector_service:
            raise HTTPException(status_code=500, detail="Connector service not initialized")

        connector = await self._connector_service.get_trading_connector(account_name, connector_name)
        
        # Validate price for limit orders
        if order_type in [OrderType.LIMIT, OrderType.LIMIT_MAKER] and price is None:
            raise HTTPException(status_code=400, detail="Price is required for LIMIT and LIMIT_MAKER orders")
        
        # Check if trading rules are loaded
        if not connector.trading_rules:
            raise HTTPException(
                status_code=503, 
                detail=f"Trading rules not yet loaded for {connector_name}. Please try again in a moment."
            )
        
        # Validate trading pair and get trading rule
        if trading_pair not in connector.trading_rules:
            available_pairs = list(connector.trading_rules.keys())[:10]  # Show first 10
            more_text = f" (and {len(connector.trading_rules) - 10} more)" if len(connector.trading_rules) > 10 else ""
            raise HTTPException(
                status_code=400, 
                detail=f"Trading pair '{trading_pair}' not supported on {connector_name}. "
                       f"Available pairs: {available_pairs}{more_text}"
            )
        
        trading_rule = connector.trading_rules[trading_pair]
        
        # Validate order type is supported
        if order_type not in connector.supported_order_types():
            supported_types = [ot.name for ot in connector.supported_order_types()]
            raise HTTPException(status_code=400, detail=f"Order type '{order_type.name}' not supported. Supported types: {supported_types}")
        
        # Quantize amount according to trading rules
        quantized_amount = connector.quantize_order_amount(trading_pair, amount)
        
        # Validate minimum order size
        if quantized_amount < trading_rule.min_order_size:
            raise HTTPException(
                status_code=400, 
                detail=f"Order amount {quantized_amount} is below minimum order size {trading_rule.min_order_size} for {trading_pair}"
            )
        
        # Calculate and validate notional size
        if order_type in [OrderType.LIMIT, OrderType.LIMIT_MAKER]:
            quantized_price = connector.quantize_order_price(trading_pair, price)
            notional_size = quantized_price * quantized_amount
        else:
            # For market orders without price, get current market price for validation
            if self._market_data_service:
                try:
                    prices = await self._market_data_service.get_prices(connector_name, [trading_pair])
                    if trading_pair in prices and "error" not in prices:
                        price = Decimal(str(prices[trading_pair]))
                except Exception as e:
                    logger.error(f"Error getting market price for {trading_pair}: {e}")
            notional_size = price * quantized_amount if price else Decimal("0")
            
        if notional_size < trading_rule.min_notional_size:
            raise HTTPException(
                status_code=400,
                detail=f"Order notional value {notional_size} is below minimum notional size {trading_rule.min_notional_size} for {trading_pair}. "
                       f"Increase the amount or price to meet the minimum requirement."
            )
        


        try:
            # Place the order using the connector with quantized values
            # (position_action will be ignored by non-perpetual connectors)
            if trade_type == TradeType.BUY:
                order_id = connector.buy(
                    trading_pair=trading_pair,
                    amount=quantized_amount,
                    order_type=order_type,
                    price=price or Decimal("1"),
                    position_action=position_action
                )
            else:
                order_id = connector.sell(
                    trading_pair=trading_pair,
                    amount=quantized_amount,
                    order_type=order_type,
                    price=price or Decimal("1"),
                    position_action=position_action
                )

            logger.info(f"Placed {trade_type} order for {amount} {trading_pair} on {connector_name} (Account: {account_name}). Order ID: {order_id}")
            return order_id
            
        except HTTPException:
            # Re-raise HTTP exceptions as-is
            raise
        except Exception as e:
            logger.error(f"Failed to place {trade_type} order: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to place trade: {str(e)}")
    
    async def get_connector_instance(self, account_name: str, connector_name: str):
        """
        Get a connector instance for direct access.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector

        Returns:
            Connector instance

        Raises:
            HTTPException: If account or connector not found
        """
        if account_name not in self.list_accounts():
            raise HTTPException(status_code=404, detail=f"Account '{account_name}' not found")

        if not self._connector_service:
            raise HTTPException(status_code=500, detail="Connector service not initialized")

        return await self._connector_service.get_trading_connector(account_name, connector_name)

    async def _get_perpetual_connector(self, account_name: str, connector_name: str):
        """
        Get a perpetual connector instance with validation.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)

        Returns:
            Perpetual connector instance

        Raises:
            HTTPException: If connector is not perpetual or not found
        """
        if "_perpetual" not in connector_name:
            raise HTTPException(status_code=400, detail=f"Connector '{connector_name}' is not a perpetual connector")
        return await self.get_connector_instance(account_name, connector_name)

    async def get_active_orders(self, account_name: str, connector_name: str) -> Dict[str, any]:
        """
        Get active orders for a specific connector.
        
        Args:
            account_name: Name of the account
            connector_name: Name of the connector
            
        Returns:
            Dictionary of active orders
        """
        connector = await self.get_connector_instance(account_name, connector_name)
        return {order_id: order.to_json() for order_id, order in connector.in_flight_orders.items()}
    
    async def cancel_order(self, account_name: str, connector_name: str, client_order_id: str) -> str:
        """
        Cancel an active order.
        
        Args:
            account_name: Name of the account
            connector_name: Name of the connector
            client_order_id: Client order ID to cancel
            
        Returns:
            Client order ID that was cancelled
            
        Raises:
            HTTPException: 404 if order not found, 500 if cancellation fails
        """
        connector = await self.get_connector_instance(account_name, connector_name)
        
        # Check if order exists in in-flight orders
        if client_order_id not in connector.in_flight_orders:
            raise HTTPException(status_code=404, detail=f"Order '{client_order_id}' not found in active orders")
        
        try:
            result = connector.cancel(trading_pair="NA", client_order_id=client_order_id)
            logger.info(f"Initiated cancellation for order {client_order_id} on {connector_name} (Account: {account_name})")
            return result
        except Exception as e:
            logger.error(f"Failed to initiate cancellation for order {client_order_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to initiate order cancellation: {str(e)}")
    
    async def set_leverage(self, account_name: str, connector_name: str,
                          trading_pair: str, leverage: int) -> Dict[str, str]:
        """
        Set leverage for a specific trading pair on a perpetual connector.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)
            trading_pair: Trading pair to set leverage for
            leverage: Leverage value (typically 1-125)

        Returns:
            Dictionary with success status and message

        Raises:
            HTTPException: If account/connector not found, not perpetual, or operation fails
        """
        connector = await self._get_perpetual_connector(account_name, connector_name)

        if not hasattr(connector, '_execute_set_leverage'):
            raise HTTPException(status_code=400, detail=f"Connector '{connector_name}' does not support leverage setting")
        
        try:
            await connector._execute_set_leverage(trading_pair, leverage)
            message = f"Leverage for {trading_pair} set to {leverage} on {connector_name}"
            logger.info(f"Set leverage for {trading_pair} to {leverage} on {connector_name} (Account: {account_name})")
            return {"status": "success", "message": message}
            
        except Exception as e:
            logger.error(f"Failed to set leverage for {trading_pair} to {leverage}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to set leverage: {str(e)}")

    async def set_position_mode(self, account_name: str, connector_name: str,
                               position_mode: PositionMode) -> Dict[str, str]:
        """
        Set position mode for a perpetual connector.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)
            position_mode: PositionMode.HEDGE or PositionMode.ONEWAY

        Returns:
            Dictionary with success status and message

        Raises:
            HTTPException: If account/connector not found, not perpetual, or operation fails
        """
        connector = await self._get_perpetual_connector(account_name, connector_name)

        # Check if the requested position mode is supported
        supported_modes = connector.supported_position_modes()
        if position_mode not in supported_modes:
            supported_values = [mode.value for mode in supported_modes]
            raise HTTPException(
                status_code=400, 
                detail=f"Position mode '{position_mode.value}' not supported. Supported modes: {supported_values}"
            )
        
        try:
            # Try to call the method - it might be sync or async
            result = connector.set_position_mode(position_mode)
            # If it's a coroutine, await it
            if asyncio.iscoroutine(result):
                await result
            
            message = f"Position mode set to {position_mode.value} on {connector_name}"
            logger.info(f"Set position mode to {position_mode.value} on {connector_name} (Account: {account_name})")
            return {"status": "success", "message": message}
            
        except Exception as e:
            logger.error(f"Failed to set position mode to {position_mode.value}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to set position mode: {str(e)}")

    async def get_position_mode(self, account_name: str, connector_name: str) -> Dict[str, str]:
        """
        Get current position mode for a perpetual connector.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)

        Returns:
            Dictionary with current position mode

        Raises:
            HTTPException: If account/connector not found, not perpetual, or operation fails
        """
        connector = await self._get_perpetual_connector(account_name, connector_name)

        if not hasattr(connector, 'position_mode'):
            raise HTTPException(status_code=400, detail=f"Connector '{connector_name}' does not support position mode")
        
        try:
            current_mode = connector.position_mode
            return {
                "position_mode": current_mode.value if current_mode else "UNKNOWN",
                "connector": connector_name,
                "account": account_name
            }
            
        except Exception as e:
            logger.error(f"Failed to get position mode: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get position mode: {str(e)}")

    async def get_orders(self, account_name: Optional[str] = None, connector_name: Optional[str] = None,
                        trading_pair: Optional[str] = None, status: Optional[str] = None,
                        start_time: Optional[int] = None, end_time: Optional[int] = None,
                        limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get order history using OrderRepository."""
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)
                orders = await order_repo.get_orders(
                    account_name=account_name,
                    connector_name=connector_name,
                    trading_pair=trading_pair,
                    status=status,
                    start_time=start_time,
                    end_time=end_time,
                    limit=limit,
                    offset=offset
                )
                return [order_repo.to_dict(order) for order in orders]
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []

    async def get_active_orders_history(self, account_name: Optional[str] = None, connector_name: Optional[str] = None,
                                       trading_pair: Optional[str] = None) -> List[Dict]:
        """Get active orders from database using OrderRepository."""
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)
                orders = await order_repo.get_active_orders(
                    account_name=account_name,
                    connector_name=connector_name,
                    trading_pair=trading_pair
                )
                return [order_repo.to_dict(order) for order in orders]
        except Exception as e:
            logger.error(f"Error getting active orders: {e}")
            return []

    async def get_orders_summary(self, account_name: Optional[str] = None, start_time: Optional[int] = None,
                                end_time: Optional[int] = None) -> Dict:
        """Get order summary statistics using OrderRepository."""
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)
                return await order_repo.get_orders_summary(
                    account_name=account_name,
                    start_time=start_time,
                    end_time=end_time
                )
        except Exception as e:
            logger.error(f"Error getting orders summary: {e}")
            return {
                "total_orders": 0,
                "filled_orders": 0,
                "cancelled_orders": 0,
                "failed_orders": 0,
                "active_orders": 0,
                "fill_rate": 0,
            }

    async def get_trades(self, account_name: Optional[str] = None, connector_name: Optional[str] = None,
                        trading_pair: Optional[str] = None, trade_type: Optional[str] = None,
                        start_time: Optional[int] = None, end_time: Optional[int] = None,
                        limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get trade history using TradeRepository."""
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                trade_repo = TradeRepository(session)
                trade_order_pairs = await trade_repo.get_trades_with_orders(
                    account_name=account_name,
                    connector_name=connector_name,
                    trading_pair=trading_pair,
                    trade_type=trade_type,
                    start_time=start_time,
                    end_time=end_time,
                    limit=limit,
                    offset=offset
                )
                return [trade_repo.to_dict(trade, order) for trade, order in trade_order_pairs]
        except Exception as e:
            logger.error(f"Error getting trades: {e}")
            return []

    async def get_account_positions(self, account_name: str, connector_name: str) -> List[Dict]:
        """
        Get current positions for a specific perpetual connector.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)

        Returns:
            List of position dictionaries

        Raises:
            HTTPException: If account/connector not found or not perpetual
        """
        connector = await self._get_perpetual_connector(account_name, connector_name)

        if not hasattr(connector, 'account_positions'):
            raise HTTPException(status_code=400, detail=f"Connector '{connector_name}' does not support position tracking")
        
        try:
            # Force position update to ensure current market prices are used
            await connector._update_positions()
            
            positions = []
            raw_positions = connector.account_positions
            
            for trading_pair, position_info in raw_positions.items():
                # Convert position data to dict format
                position_dict = {
                    "account_name": account_name,
                    "connector_name": connector_name,
                    "trading_pair": position_info.trading_pair,
                    "side": position_info.position_side.name if hasattr(position_info, 'position_side') else "UNKNOWN",
                    "amount": float(position_info.amount) if hasattr(position_info, 'amount') else 0.0,
                    "entry_price": float(position_info.entry_price) if hasattr(position_info, 'entry_price') else None,
                    "unrealized_pnl": float(position_info.unrealized_pnl) if hasattr(position_info, 'unrealized_pnl') else None,
                    "leverage": float(position_info.leverage) if hasattr(position_info, 'leverage') else None,
                }
                
                # Only include positions with non-zero amounts
                if position_dict["amount"] != 0:
                    positions.append(position_dict)
            
            return positions
            
        except Exception as e:
            logger.error(f"Failed to get positions for {connector_name}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get positions: {str(e)}")

    async def get_funding_payments(self, account_name: str, connector_name: str = None, 
                                  trading_pair: str = None, limit: int = 100) -> List[Dict]:
        """
        Get funding payment history for an account.
        
        Args:
            account_name: Name of the account
            connector_name: Optional connector name filter
            trading_pair: Optional trading pair filter
            limit: Maximum number of records to return
            
        Returns:
            List of funding payment dictionaries
        """
        await self.ensure_db_initialized()
        
        try:
            async with self.db_manager.get_session_context() as session:
                funding_repo = FundingRepository(session)
                funding_payments = await funding_repo.get_funding_payments(
                    account_name=account_name,
                    connector_name=connector_name,
                    trading_pair=trading_pair,
                    limit=limit
                )
                return [funding_repo.to_dict(payment) for payment in funding_payments]
                
        except Exception as e:
            logger.error(f"Error getting funding payments: {e}")
            return []

    async def get_total_funding_fees(self, account_name: str, connector_name: str,
                                   trading_pair: str) -> Dict:
        """
        Get total funding fees for a specific trading pair.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector
            trading_pair: Trading pair to get fees for

        Returns:
            Dictionary with total funding fees information
        """
        await self.ensure_db_initialized()

        try:
            async with self.db_manager.get_session_context() as session:
                funding_repo = FundingRepository(session)
                return await funding_repo.get_total_funding_fees(
                    account_name=account_name,
                    connector_name=connector_name,
                    trading_pair=trading_pair
                )

        except Exception as e:
            logger.error(f"Error getting total funding fees: {e}")
            return {
                "total_funding_fees": 0,
                "payment_count": 0,
                "fee_currency": None,
                "error": str(e)
            }

    # ============================================
    # Gateway Wallet Management Methods
    # ============================================

    async def _update_gateway_balances(self, chain_networks: Optional[List[str]] = None):
        """Update Gateway wallet balances in master_account state.

        Only queries the defaultWallet on each network in defaultNetworks for each chain.
        This is more efficient than querying all wallets on all networks.

        Args:
            chain_networks: If provided, only update these chain-network combinations
                           (e.g., ['solana-mainnet-beta', 'ethereum-mainnet']).
                           If None, update all defaultNetworks for each chain.
        """
        try:
            # Check if Gateway is available
            if not await self.gateway_client.ping():
                logger.debug("Gateway service is not available, skipping wallet balance update")
                return

            # Get all available chains
            chains_result = await self.gateway_client.get_chains()
            if not chains_result or "chains" not in chains_result:
                logger.error("Could not get chains from Gateway")
                return

            known_chains = {c["chain"] for c in chains_result["chains"]}

            # Ensure master_account exists in accounts_state
            if "master_account" not in self.accounts_state:
                self.accounts_state["master_account"] = {}

            # Collect all balance query tasks for parallel execution
            balance_tasks = []
            task_metadata = []  # Store (chain, network, address) for each task

            # For each chain, get its config with defaultWallet and defaultNetworks
            for chain_info in chains_result["chains"]:
                chain = chain_info["chain"]
                networks = chain_info.get("networks", [])

                if not networks:
                    logger.debug(f"Chain '{chain}' has no networks configured, skipping")
                    continue

                # Get merged config using chain-network namespace (e.g., solana-mainnet-beta)
                # This returns both chain-level fields (defaultWallet, defaultNetworks) and network fields
                first_network = networks[0]
                try:
                    config = await self.gateway_client.get_config(f"{chain}-{first_network}")
                except Exception as e:
                    logger.warning(f"Could not get config for '{chain}-{first_network}': {e}")
                    continue

                default_wallet = config.get("defaultWallet")
                default_networks = config.get("defaultNetworks", [])

                if not default_wallet:
                    logger.debug(f"Chain '{chain}' missing defaultWallet, skipping")
                    continue

                if not default_networks:
                    # Fall back to defaultNetwork (singular) if defaultNetworks not set
                    default_network = config.get("defaultNetwork")
                    if default_network:
                        default_networks = [default_network]
                    else:
                        logger.debug(f"Chain '{chain}' missing defaultNetworks, skipping")
                        continue

                # Create balance tasks for each default network
                for network in default_networks:
                    chain_network_key = f"{chain}-{network}"

                    # Filter by chain_networks if specified
                    if chain_networks and chain_network_key not in chain_networks:
                        continue

                    balance_tasks.append(self.get_gateway_balances(chain, default_wallet, network=network))
                    task_metadata.append((chain, network, default_wallet))

            # Build set of active chain-network keys
            active_chain_networks = {f"{chain}-{network}" for chain, network, _ in task_metadata}

            # Execute all balance queries in parallel
            if balance_tasks:
                results = await asyncio.gather(*balance_tasks, return_exceptions=True)

                # Process results
                for result, (chain, network, address) in zip(results, task_metadata):
                    chain_network = f"{chain}-{network}"

                    if isinstance(result, Exception):
                        logger.error(f"Error updating Gateway balances for {chain}-{network} wallet {address}: {result}")
                        # Store empty list for error state
                        self.accounts_state["master_account"][chain_network] = []
                    elif result:
                        # Only store if there are actual balances (non-empty list)
                        self.accounts_state["master_account"][chain_network] = result
                    else:
                        # Store empty list to indicate we checked this network
                        self.accounts_state["master_account"][chain_network] = []

            # Only remove stale keys if we're doing a full update (no filter)
            # When filtering, we don't want to remove keys that weren't in the filter
            if not chain_networks:
                # Remove stale gateway chain-network keys (default network/wallet changed or no longer configured)
                # Gateway keys follow pattern: chain-network (e.g., "solana-mainnet-beta", "ethereum-mainnet")
                stale_keys = []
                for key in self.accounts_state["master_account"]:
                    # Check if key looks like a gateway chain-network (contains hyphen and matches chain pattern)
                    if "-" in key and key not in active_chain_networks:
                        # Verify it's a gateway key by checking if chain part matches known chains
                        chain_part = key.split("-")[0]
                        if chain_part in known_chains:
                            stale_keys.append(key)

                for key in stale_keys:
                    logger.info(f"Removing stale Gateway balance data for {key} (no longer default network)")
                    del self.accounts_state["master_account"][key]

        except Exception as e:
            logger.error(f"Error updating Gateway balances: {e}")

    async def get_gateway_wallets(self) -> List[Dict]:
        """
        Get all wallets from Gateway. Gateway manages its own encrypted wallets.

        Returns:
            List of wallet information from Gateway
        """
        if not await self.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        try:
            wallets = await self.gateway_client.get_wallets()
            return wallets
        except Exception as e:
            logger.error(f"Error getting Gateway wallets: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get wallets: {str(e)}")

    async def add_gateway_wallet(self, chain: str, private_key: str) -> Dict:
        """
        Add a wallet to Gateway. Gateway handles encryption internally.

        Args:
            chain: Blockchain chain (e.g., 'solana', 'ethereum')
            private_key: Wallet private key

        Returns:
            Dictionary with wallet information from Gateway
        """
        if not await self.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        try:
            result = await self.gateway_client.add_wallet(chain, private_key, set_default=True)

            if "error" in result:
                raise HTTPException(status_code=400, detail=f"Gateway error: {result['error']}")

            logger.info(f"Added {chain} wallet {result.get('address')} to Gateway")
            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding Gateway wallet: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to add wallet: {str(e)}")

    async def remove_gateway_wallet(self, chain: str, address: str) -> Dict:
        """
        Remove a wallet from Gateway.

        Args:
            chain: Blockchain chain
            address: Wallet address to remove

        Returns:
            Success message
        """
        if not await self.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        try:
            result = await self.gateway_client.remove_wallet(chain, address)

            if "error" in result:
                raise HTTPException(status_code=400, detail=f"Gateway error: {result['error']}")

            logger.info(f"Removed {chain} wallet {address} from Gateway")
            return {"success": True, "message": f"Successfully removed {chain} wallet"}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error removing Gateway wallet: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to remove wallet: {str(e)}")

    async def get_gateway_balances(self, chain: str, address: str, network: Optional[str] = None, tokens: Optional[List[str]] = None) -> List[Dict]:
        """
        Get Gateway wallet balances with pricing from rate sources.

        Args:
            chain: Blockchain chain
            address: Wallet address
            network: Optional network name (if not provided, uses default network for chain)
            tokens: Optional list of token symbols to query

        Returns:
            List of token balance dictionaries with prices from rate sources
        """
        if not await self.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        try:
            # Get default network for chain if not provided
            if not network:
                network = await self.gateway_client.get_default_network(chain)
            if not network:
                raise HTTPException(status_code=400, detail=f"Could not determine network for chain '{chain}'")

            # Get balances from Gateway
            balances_response = await self.gateway_client.get_balances(chain, network, address, tokens=tokens)

            if "error" in balances_response:
                raise HTTPException(status_code=400, detail=f"Gateway error: {balances_response['error']}")

            # Format balances list
            balances = balances_response.get("balances", {})
            balances_list = []

            for token, balance in balances.items():
                if balance and float(balance) > 0:
                    balances_list.append({
                        "token": token,
                        "units": Decimal(str(balance))
                    })

            # Get prices for tokens
            unique_tokens = [b["token"] for b in balances_list]
            all_prices = {}

            # Fetch prices for Gateway tokens
            if unique_tokens:
                try:
                    fetched_prices = await self._fetch_gateway_prices_immediate(
                        chain, network, unique_tokens
                    )
                    for token, price in fetched_prices.items():
                        if price > 0:
                            all_prices[token] = price
                except Exception as e:
                    logger.warning(f"Error fetching gateway prices: {e}")

            # Format final result with prices
            formatted_balances = []
            for balance in balances_list:
                token = balance["token"]
                if "USD" in token:
                    price = Decimal("1")
                else:
                    # all_prices is now keyed by token name directly
                    price = Decimal(str(all_prices.get(token, 0)))

                formatted_balances.append({
                    "token": token,
                    "units": float(balance["units"]),
                    "price": float(price),
                    "value": float(price * balance["units"]),
                    "available_units": float(balance["units"])
                })

            return formatted_balances

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting Gateway balances: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get balances: {str(e)}")

    async def _fetch_gateway_prices_immediate(self, chain: str, network: str,
                                               tokens: List[str]) -> Dict[str, Decimal]:
        """
        Fetch prices immediately from Gateway for the given tokens.
        This is used to get prices right away instead of waiting for the background update task.

        Uses the same pricing connector resolution as MarketDataProvider.update_rates_task():
        - solana -> jupiter/router
        - ethereum -> uniswap/router

        Args:
            chain: Blockchain chain (e.g., 'solana', 'ethereum')
            network: Network name (e.g., 'mainnet-beta', 'mainnet')
            tokens: List of token symbols to get prices for

        Returns:
            Dictionary mapping token symbol to price in USDC
        """
        from hummingbot.core.data_type.common import TradeType
        from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient
        from hummingbot.core.rate_oracle.rate_oracle import RateOracle

        gateway_client = GatewayHttpClient.get_instance()
        rate_oracle = RateOracle.get_instance()
        prices = {}

        # Resolve pricing connector based on chain (same logic as MarketDataProvider)
        pricing_connector = self.gateway_default_pricing_connector.get(chain)
        if not pricing_connector:
            logger.warning(f"No pricing connector configured for chain '{chain}', skipping immediate price fetch")
            return prices

        # Create tasks for all tokens in parallel
        tasks = []
        task_tokens = []
        quote_asset = "USDC"

        for token in tokens:
            # Skip same-token quotes (e.g., USDC/USDC) - price is always 1
            if token.upper() == quote_asset.upper():
                prices[token] = Decimal("1")
                rate_oracle.set_price(f"{token}-{quote_asset}", Decimal("1"))
                logger.debug(f"Skipping same-token quote for {token}, price=1")
                continue

            try:
                task = gateway_client.get_price(
                    chain=chain,
                    network=network,
                    connector=pricing_connector,
                    base_asset=token,
                    quote_asset=quote_asset,
                    amount=Decimal("1"),
                    side=TradeType.SELL
                )
                tasks.append(task)
                task_tokens.append(token)
            except Exception as e:
                logger.warning(f"Error preparing price request for {token}: {e}")
                continue

        if tasks:
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for token, result in zip(task_tokens, results):
                    if isinstance(result, Exception):
                        logger.warning(f"Error fetching price for {token}: {result}")
                    elif result and "price" in result:
                        price = Decimal(str(result["price"]))
                        prices[token] = price
                        # Also update the rate oracle so future lookups can find it
                        trading_pair = f"{token}-USDC"
                        rate_oracle.set_price(trading_pair, price)
                        logger.debug(f"Fetched immediate price for {token}: {price} USDC")
            except Exception as e:
                logger.error(f"Error fetching gateway prices: {e}", exc_info=True)

        return prices

    def get_unwrapped_token(self, token: str) -> str:
        """Get the unwrapped version of a wrapped token symbol (e.g., WSOL -> SOL)."""
        if token.startswith("W") and token[1:] in self.potential_wrapped_tokens:
            return token[1:]
        return token