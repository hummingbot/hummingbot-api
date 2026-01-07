import asyncio
import logging
import time
from decimal import Decimal
from typing import Dict, List, Optional

# Create module-specific logger
logger = logging.getLogger(__name__)

from hummingbot.client.config.config_crypt import ETHKeyFileSecretManger
from hummingbot.client.config.config_helpers import get_connector_class
from hummingbot.client.settings import AllConnectorSettings
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.utils.async_utils import safe_ensure_future

from utils.file_system import fs_util
from utils.hummingbot_api_config_adapter import HummingbotAPIConfigAdapter
from utils.security import BackendAPISecurity


class ConnectorManager:
    """
    Manages the creation and caching of exchange connectors.
    Handles connector configuration and initialization.
    This is the single source of truth for all connector instances.
    """

    def __init__(self, secrets_manager: ETHKeyFileSecretManger, db_manager=None):
        self.secrets_manager = secrets_manager
        self.db_manager = db_manager
        self._connector_cache: Dict[str, ConnectorBase] = {}
        self._orders_recorders: Dict[str, any] = {}
        self._funding_recorders: Dict[str, any] = {}
        self._status_polling_tasks: Dict[str, asyncio.Task] = {}

    async def get_connector(self, account_name: str, connector_name: str):
        """
        Get the connector object for the specified account and connector.
        Uses caching to avoid recreating connectors unnecessarily.
        Ensures proper initialization including position mode setup.

        :param account_name: The name of the account.
        :param connector_name: The name of the connector.
        :return: The connector object.
        """
        cache_key = f"{account_name}:{connector_name}"

        if cache_key in self._connector_cache:
            return self._connector_cache[cache_key]

        # Create connector with full initialization
        connector = await self._create_and_initialize_connector(account_name, connector_name)
        return connector

    def _create_connector(self, account_name: str, connector_name: str):
        """
        Create a new connector instance.

        :param account_name: The name of the account.
        :param connector_name: The name of the connector.
        :return: The connector object.
        """
        BackendAPISecurity.login_account(account_name=account_name, secrets_manager=self.secrets_manager)
        conn_setting = AllConnectorSettings.get_connector_settings()[connector_name]
        keys = BackendAPISecurity.api_keys(connector_name)

        # Debug logging
        logger.info(f"Creating connector {connector_name} for account {account_name}")
        logger.debug(f"API keys retrieved: {list(keys.keys()) if keys else 'None'}")

        init_params = conn_setting.conn_init_parameters(
            trading_pairs=[],
            trading_required=True,
            api_keys=keys,
        )

        # Debug logging
        logger.debug(f"Init params keys: {list(init_params.keys())}")

        connector_class = get_connector_class(connector_name)
        connector = connector_class(**init_params)
        return connector

    def clear_cache(self, account_name: Optional[str] = None, connector_name: Optional[str] = None):
        """
        Clear the connector cache.

        :param account_name: If provided, only clear cache for this account.
        :param connector_name: If provided with account_name, only clear this specific connector.
        """
        if account_name and connector_name:
            cache_key = f"{account_name}:{connector_name}"
            self._connector_cache.pop(cache_key, None)
        elif account_name:
            # Clear all connectors for this account
            keys_to_remove = [k for k in self._connector_cache.keys() if k.startswith(f"{account_name}:")]
            for key in keys_to_remove:
                self._connector_cache.pop(key)
        else:
            # Clear entire cache
            self._connector_cache.clear()

    @staticmethod
    def get_connector_config_map(connector_name: str):
        """
        Get the connector config map for the specified connector with type information.

        :param connector_name: The name of the connector.
        :return: Dictionary mapping field names to their type information.
        """
        from typing import Literal, get_args, get_origin

        connector_config = HummingbotAPIConfigAdapter(AllConnectorSettings.get_connector_config_keys(connector_name))
        fields_info = {}

        for key, field in connector_config.hb_config.model_fields.items():
            if key == "connector":
                continue

            # Get the type annotation
            field_type = field.annotation
            type_name = getattr(field_type, "__name__", str(field_type))
            allowed_values = None

            # Handle Optional and Literal types
            origin = get_origin(field_type)
            args = get_args(field_type)

            if origin is Literal:
                # It's a Literal type, extract the allowed values
                type_name = "Literal"
                allowed_values = list(args)
            elif origin is not None:
                # Handle Union types (Optional is Union[X, None])
                if type(None) in args:
                    # It's an Optional type, get the actual type
                    actual_types = [arg for arg in args if arg is not type(None)]
                    if actual_types:
                        inner_type = actual_types[0]
                        inner_origin = get_origin(inner_type)
                        inner_args = get_args(inner_type)

                        if inner_origin is Literal:
                            # Optional[Literal[...]]
                            type_name = "Literal"
                            allowed_values = list(inner_args)
                        else:
                            type_name = getattr(inner_type, "__name__", str(inner_type))
                else:
                    type_name = str(field_type)

            field_info = {
                "type": type_name,
                "required": field.is_required(),
            }

            if allowed_values is not None:
                field_info["allowed_values"] = allowed_values

            fields_info[key] = field_info

        return fields_info

    async def update_connector_keys(self, account_name: str, connector_name: str, keys: dict):
        """
        Update the API keys for a connector and refresh the connector instance.

        :param account_name: The name of the account.
        :param connector_name: The name of the connector.
        :param keys: Dictionary of API keys to update.
        :return: The updated connector instance.
        """
        BackendAPISecurity.login_account(account_name=account_name, secrets_manager=self.secrets_manager)
        connector_config = HummingbotAPIConfigAdapter(AllConnectorSettings.get_connector_config_keys(connector_name))

        for key, value in keys.items():
            setattr(connector_config, key, value)

        BackendAPISecurity.update_connector_keys(account_name, connector_config)

        # Re-decrypt all credentials to ensure the new keys are available
        BackendAPISecurity.decrypt_all(account_name=account_name)

        # Clear the cache for this connector to force recreation with new keys
        self.clear_cache(account_name, connector_name)

        # Create and return new connector instance
        new_connector = await self.get_connector(account_name, connector_name)

        return new_connector

    def list_account_connectors(self, account_name: str) -> List[str]:
        """
        List all initialized connectors for a specific account.

        :param account_name: The name of the account.
        :return: List of connector names.
        """
        connectors = []
        for cache_key in self._connector_cache.keys():
            acc_name, conn_name = cache_key.split(":", 1)
            if acc_name == account_name:
                connectors.append(conn_name)
        return connectors

    def get_all_connectors(self) -> Dict[str, Dict[str, ConnectorBase]]:
        """
        Get all connectors organized by account.

        :return: Dictionary mapping account names to their connectors.
        """
        result = {}
        for cache_key, connector in self._connector_cache.items():
            account_name, connector_name = cache_key.split(":", 1)
            if account_name not in result:
                result[account_name] = {}
            result[account_name][connector_name] = connector
        return result

    def is_connector_initialized(self, account_name: str, connector_name: str) -> bool:
        """
        Check if a connector is already initialized and cached.

        :param account_name: The name of the account.
        :param connector_name: The name of the connector.
        :return: True if the connector is initialized, False otherwise.
        """
        cache_key = f"{account_name}:{connector_name}"
        return cache_key in self._connector_cache

    async def _create_and_initialize_connector(self, account_name: str, connector_name: str) -> ConnectorBase:
        """
        Create and fully initialize a connector with all necessary setup.
        This includes creating the connector, starting its network, setting up order recording,
        and configuring position mode for perpetual connectors.

        :param account_name: The name of the account.
        :param connector_name: The name of the connector.
        :return: The initialized connector instance.
        """
        cache_key = f"{account_name}:{connector_name}"
        # Create the base connector
        connector = self._create_connector(account_name, connector_name)

        # Initialize symbol map
        await connector._initialize_trading_pair_symbol_map()

        # Update trading rules
        await connector._update_trading_rules()

        # Update initial balances
        await connector._update_balances()

        # Set default position mode to HEDGE for perpetual connectors
        if "_perpetual" in connector_name:
            if PositionMode.HEDGE in connector.supported_position_modes():
                connector.set_position_mode(PositionMode.HEDGE)
            await connector._update_positions()

        self._connector_cache[cache_key] = connector

        # Load existing orders from database before starting network
        if self.db_manager:
            await self._load_existing_orders_from_database(connector, account_name, connector_name)

        # Start order tracking if db_manager is available
        if self.db_manager:
            if cache_key not in self._orders_recorders:
                # Import OrdersRecorder dynamically to avoid circular imports
                from services.orders_recorder import OrdersRecorder

                # Create and start orders recorder
                orders_recorder = OrdersRecorder(self.db_manager, account_name, connector_name)
                orders_recorder.start(connector)
                self._orders_recorders[cache_key] = orders_recorder

            # Start funding tracking for perpetual connectors
            if "_perpetual" in connector_name and cache_key not in self._funding_recorders:
                # Import FundingRecorder dynamically to avoid circular imports
                from services.funding_recorder import FundingRecorder

                # Create and start funding recorder
                funding_recorder = FundingRecorder(self.db_manager, account_name, connector_name)
                funding_recorder.start(connector)
                self._funding_recorders[cache_key] = funding_recorder

        # Start network manually without clock system
        await self._start_connector_network(connector)
        
        # Perform initial update of connector state
        await self._update_connector_state(connector, connector_name, account_name)

        logger.info(f"Initialized connector {connector_name} for account {account_name}")
        return connector

    async def _start_connector_network(self, connector: ConnectorBase):
        """
        Start connector network tasks manually without clock system.
        Based on the original start_network method but without order book tracker.
        """
        try:
            # Stop any existing network tasks
            await self._stop_connector_network(connector)
            
            # Start trading rules polling
            connector._trading_rules_polling_task = safe_ensure_future(connector._trading_rules_polling_loop())

            # Start trading fees polling
            connector._trading_fees_polling_task = safe_ensure_future(connector._trading_fees_polling_loop())

            # Start user stream tracker (websocket connection)
            connector._user_stream_tracker_task = connector._create_user_stream_tracker_task()

            # Start user stream event listener
            connector._user_stream_event_listener_task = safe_ensure_future(connector._user_stream_event_listener())

            # Start lost orders update task
            connector._lost_orders_update_task = safe_ensure_future(connector._lost_orders_update_polling_loop())

            logger.info(f"Started connector network tasks for {connector}")

        except Exception as e:
            logger.error(f"Error starting connector network: {e}")
            raise

    async def _stop_connector_network(self, connector: ConnectorBase):
        """
        Stop connector network tasks.
        """
        try:
            # Stop trading rules polling
            if connector._trading_rules_polling_task:
                connector._trading_rules_polling_task.cancel()
                connector._trading_rules_polling_task = None
                
            # Stop trading fees polling
            if connector._trading_fees_polling_task:
                connector._trading_fees_polling_task.cancel()
                connector._trading_fees_polling_task = None
                
            # Stop status polling
            if connector._status_polling_task:
                connector._status_polling_task.cancel()
                connector._status_polling_task = None
                
            # Stop user stream tracker
            if connector._user_stream_tracker_task:
                connector._user_stream_tracker_task.cancel()
                connector._user_stream_tracker_task = None
                
            # Stop user stream event listener
            if connector._user_stream_event_listener_task:
                connector._user_stream_event_listener_task.cancel()
                connector._user_stream_event_listener_task = None
                
            # Stop lost orders update task
            if connector._lost_orders_update_task:
                connector._lost_orders_update_task.cancel()
                connector._lost_orders_update_task = None
                
        except Exception as e:
            logger.error(f"Error stopping connector network: {e}")

    async def _update_connector_state(self, connector: ConnectorBase, connector_name: str, account_name: str = None):
        """
        Update connector state including balances, orders, positions, and trading rules.
        This function can be called both during initialization and periodically.

        :param connector: The connector instance
        :param connector_name: The name of the connector
        :param account_name: The name of the account (optional, used for order sync)
        """
        try:
            # Update current timestamp
            connector._set_current_timestamp(time.time())

            # Update balances
            await connector._update_balances()

            # Update trading rules
            await connector._update_trading_rules()

            # Update positions for perpetual connectors
            if "_perpetual" in connector_name:
                await connector._update_positions()

            # Update order status for in-flight orders
            if hasattr(connector, '_update_order_status') and connector.in_flight_orders:
                await connector._update_order_status()

                # Sync updated order state to database and cleanup closed orders
                if account_name:
                    await self._sync_orders_to_database(connector, account_name, connector_name)

            logger.debug(f"Updated connector state for {connector_name}")
            
        except Exception as e:
            logger.error(f"Error updating connector state for {connector_name}: {e}")

    async def update_all_connector_states(self):
        """
        Update state for all cached connectors.
        This can be called periodically to refresh connector data.
        """
        for cache_key, connector in self._connector_cache.items():
            account_name, connector_name = cache_key.split(":", 1)
            try:
                await self._update_connector_state(connector, connector_name, account_name)
            except Exception as e:
                logger.error(f"Error updating state for {account_name}/{connector_name}: {e}")

    async def sync_order_state_to_database_for_all_connectors(self):
        """
        Sync connector's in_flight_orders state to database for all connectors.

        The connector's built-in _lost_orders_update_polling_loop already polls the exchange
        and updates in_flight_orders. This method just syncs that state to our database
        and cleans up closed orders. Called every minute.
        """
        for cache_key, connector in self._connector_cache.items():
            account_name, connector_name = cache_key.split(":", 1)
            try:
                # Only process if there are in-flight orders
                if not connector.in_flight_orders:
                    continue

                # Sync connector state to database and cleanup closed orders
                await self._sync_orders_to_database(connector, account_name, connector_name)
                logger.debug(f"Synced order state to DB for {account_name}/{connector_name}")

            except Exception as e:
                logger.error(f"Error syncing order state for {account_name}/{connector_name}: {e}")

    async def _load_existing_orders_from_database(self, connector: ConnectorBase, account_name: str, connector_name: str):
        """
        Load existing active orders from database and add them to connector's in_flight_orders.
        This ensures that orders placed before an API restart can still be managed.

        :param connector: The connector instance to load orders into
        :param account_name: The name of the account
        :param connector_name: The name of the connector
        """
        try:
            # Import OrderRepository dynamically to avoid circular imports
            from database import OrderRepository

            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)

                # Get active orders from database for this account/connector
                active_orders = await order_repo.get_active_orders(account_name=account_name, connector_name=connector_name)

                logger.info(f"Loading {len(active_orders)} existing active orders for {account_name}/{connector_name}")

                for order_record in active_orders:
                    try:
                        # Convert database order to InFlightOrder
                        in_flight_order = self._convert_db_order_to_in_flight_order(order_record)

                        # Add to connector's in_flight_orders
                        connector.in_flight_orders[in_flight_order.client_order_id] = in_flight_order

                        logger.debug(f"Loaded order {in_flight_order.client_order_id} from database into connector")

                    except Exception as e:
                        logger.error(f"Error converting database order {order_record.client_order_id} to InFlightOrder: {e}")
                        continue

                logger.info(
                    f"Successfully loaded {len(connector.in_flight_orders)} in-flight orders for {account_name}/{connector_name}"
                )

        except Exception as e:
            logger.error(f"Error loading existing orders from database for {account_name}/{connector_name}: {e}")

    def _map_order_state_to_status(self, order_state: OrderState) -> str:
        """
        Map Hummingbot OrderState to database status string.

        :param order_state: The OrderState enum value from Hummingbot
        :return: Database status string
        """
        status_mapping = {
            OrderState.PENDING_CREATE: "SUBMITTED",
            OrderState.OPEN: "OPEN",
            OrderState.PENDING_CANCEL: "PENDING_CANCEL",
            OrderState.CANCELED: "CANCELLED",
            OrderState.PARTIALLY_FILLED: "PARTIALLY_FILLED",
            OrderState.FILLED: "FILLED",
            OrderState.FAILED: "FAILED",
            OrderState.PENDING_APPROVAL: "SUBMITTED",
            OrderState.APPROVED: "SUBMITTED",
            OrderState.CREATED: "SUBMITTED",
            OrderState.COMPLETED: "FILLED",
        }
        return status_mapping.get(order_state, "SUBMITTED")

    async def _sync_orders_to_database(self, connector: ConnectorBase, account_name: str, connector_name: str):
        """
        Sync connector's in_flight_orders state to database and cleanup closed orders.

        This method ensures that the database reflects the current state of orders
        as reported by the exchange, and removes terminal orders from in_flight_orders.

        :param connector: The connector instance
        :param account_name: The name of the account
        :param connector_name: The name of the connector
        """
        if not self.db_manager:
            return

        terminal_states = [OrderState.FILLED, OrderState.CANCELED, OrderState.FAILED, OrderState.COMPLETED]
        orders_to_remove = []

        # Create a copy of keys to iterate safely while potentially modifying the dict
        order_ids = list(connector.in_flight_orders.keys())

        for client_order_id in order_ids:
            order = connector.in_flight_orders.get(client_order_id)
            if not order:
                continue

            try:
                # Import OrderRepository dynamically to avoid circular imports
                from database import OrderRepository

                async with self.db_manager.get_session_context() as session:
                    order_repo = OrderRepository(session)
                    db_order = await order_repo.get_order_by_client_id(client_order_id)

                    if db_order:
                        # Map connector state to database status
                        new_status = self._map_order_state_to_status(order.current_state)

                        # Only update if status changed
                        if db_order.status != new_status:
                            await order_repo.update_order_status(client_order_id, new_status)
                            logger.info(f"Synced order {client_order_id} status: {db_order.status} -> {new_status}")

                    # Mark terminal orders for removal from in_flight_orders
                    if order.current_state in terminal_states:
                        orders_to_remove.append(client_order_id)

            except Exception as e:
                logger.error(f"Error syncing order {client_order_id} to database: {e}")

        # Remove terminal orders from in_flight_orders
        for order_id in orders_to_remove:
            connector.in_flight_orders.pop(order_id, None)
            logger.debug(f"Removed closed order {order_id} from in_flight_orders")

        if orders_to_remove:
            logger.info(f"Cleaned up {len(orders_to_remove)} terminal orders from {account_name}/{connector_name}")

    def _convert_db_order_to_in_flight_order(self, order_record) -> InFlightOrder:
        """
        Convert a database Order record to a Hummingbot InFlightOrder object.

        :param order_record: Database Order model instance
        :return: InFlightOrder instance
        """
        # Map database status to OrderState
        status_mapping = {
            "SUBMITTED": OrderState.PENDING_CREATE,
            "OPEN": OrderState.OPEN,
            "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
            "FILLED": OrderState.FILLED,
            "CANCELLED": OrderState.CANCELED,
            "FAILED": OrderState.FAILED,
        }

        # Get the appropriate OrderState
        order_state = status_mapping.get(order_record.status, OrderState.PENDING_CREATE)

        # Convert string enums to proper enum instances
        try:
            order_type = OrderType[order_record.order_type]
        except (KeyError, ValueError):
            logger.warning(f"Unknown order type '{order_record.order_type}', defaulting to LIMIT")
            order_type = OrderType.LIMIT

        try:
            trade_type = TradeType[order_record.trade_type]
        except (KeyError, ValueError):
            logger.warning(f"Unknown trade type '{order_record.trade_type}', defaulting to BUY")
            trade_type = TradeType.BUY

        # Convert creation timestamp - use order creation time or current time as fallback
        creation_timestamp = order_record.created_at.timestamp() if order_record.created_at else time.time()

        # Create InFlightOrder instance
        in_flight_order = InFlightOrder(
            client_order_id=order_record.client_order_id,
            trading_pair=order_record.trading_pair,
            order_type=order_type,
            trade_type=trade_type,
            amount=Decimal(str(order_record.amount)),
            creation_timestamp=creation_timestamp,
            price=Decimal(str(order_record.price)) if order_record.price else None,
            exchange_order_id=order_record.exchange_order_id,
            initial_state=order_state,
            leverage=1,  # Default leverage
            position=PositionAction.NIL,  # Default position action
        )

        # Update current state and filled amount if order has progressed
        in_flight_order.current_state = order_state
        if order_record.filled_amount:
            in_flight_order.executed_amount_base = Decimal(str(order_record.filled_amount))
        if order_record.average_fill_price:
            in_flight_order.last_executed_quantity = Decimal(str(order_record.filled_amount or 0))
            in_flight_order.last_executed_price = Decimal(str(order_record.average_fill_price))

        return in_flight_order

    async def stop_connector(self, account_name: str, connector_name: str):
        """
        Stop a connector and its associated services.

        :param account_name: The name of the account.
        :param connector_name: The name of the connector.
        """
        cache_key = f"{account_name}:{connector_name}"

        # Stop order recorder if exists
        if cache_key in self._orders_recorders:
            try:
                await self._orders_recorders[cache_key].stop()
                del self._orders_recorders[cache_key]
                logger.info(f"Stopped order recorder for {account_name}/{connector_name}")
            except Exception as e:
                logger.error(f"Error stopping order recorder for {account_name}/{connector_name}: {e}")

        # Stop funding recorder if exists
        if cache_key in self._funding_recorders:
            try:
                await self._funding_recorders[cache_key].stop()
                del self._funding_recorders[cache_key]
                logger.info(f"Stopped funding recorder for {account_name}/{connector_name}")
            except Exception as e:
                logger.error(f"Error stopping funding recorder for {account_name}/{connector_name}: {e}")

        # Stop manual status polling task if exists
        if cache_key in self._status_polling_tasks:
            try:
                self._status_polling_tasks[cache_key].cancel()
                del self._status_polling_tasks[cache_key]
                logger.info(f"Stopped manual status polling for {account_name}/{connector_name}")
            except Exception as e:
                logger.error(f"Error stopping manual status polling for {account_name}/{connector_name}: {e}")

        # Stop connector network if exists
        if cache_key in self._connector_cache:
            try:
                connector = self._connector_cache[cache_key]
                await self._stop_connector_network(connector)
                logger.info(f"Stopped connector network for {account_name}/{connector_name}")
            except Exception as e:
                logger.error(f"Error stopping connector network for {account_name}/{connector_name}: {e}")

    async def stop_all_connectors(self):
        """
        Stop all connectors and their associated services.
        """
        # Get all account/connector pairs
        pairs = [(k.split(":", 1)[0], k.split(":", 1)[1]) for k in self._connector_cache.keys()]

        # Stop each connector
        for account_name, connector_name in pairs:
            await self.stop_connector(account_name, connector_name)

    def list_available_credentials(self, account_name: str) -> List[str]:
        """
        List all available connector credentials for an account.

        :param account_name: The name of the account.
        :return: List of connector names that have credentials.
        """
        try:
            files = fs_util.list_files(f"credentials/{account_name}/connectors")
            return [file.replace(".yml", "") for file in files if file.endswith(".yml")]
        except FileNotFoundError:
            return []
