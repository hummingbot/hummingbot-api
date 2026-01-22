"""
ExecutorService manages executor lifecycle and orchestration.
This service enables running Hummingbot executors directly via API
without Docker containers or full strategy setup.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Type

from fastapi import HTTPException

from hummingbot.strategy_v2.executors.arbitrage_executor.arbitrage_executor import ArbitrageExecutor
from hummingbot.strategy_v2.executors.arbitrage_executor.data_types import ArbitrageExecutorConfig
from hummingbot.strategy_v2.executors.data_types import ExecutorConfigBase
from hummingbot.strategy_v2.executors.dca_executor.dca_executor import DCAExecutor
from hummingbot.strategy_v2.executors.dca_executor.data_types import DCAExecutorConfig
from hummingbot.strategy_v2.executors.executor_base import ExecutorBase
from hummingbot.strategy_v2.executors.grid_executor.data_types import GridExecutorConfig
from hummingbot.strategy_v2.executors.grid_executor.grid_executor import GridExecutor
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.executors.twap_executor.data_types import TWAPExecutorConfig
from hummingbot.strategy_v2.executors.twap_executor.twap_executor import TWAPExecutor
from hummingbot.strategy_v2.executors.xemm_executor.data_types import XEMMExecutorConfig
from hummingbot.strategy_v2.executors.xemm_executor.xemm_executor import XEMMExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType, TrackedOrder

from database import AsyncDatabaseManager
from models.executors import PositionHold
from services.trading_service import TradingService, AccountTradingInterface

logger = logging.getLogger(__name__)


def _json_default(obj):
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, Enum):
        return obj.name
    if isinstance(obj, TrackedOrder):
        return {
            "order_id": obj.order_id,
            "price": float(obj.price) if obj.price else None,
            "executed_amount_base": float(obj.executed_amount_base) if obj.executed_amount_base else 0.0,
            "executed_amount_quote": float(obj.executed_amount_quote) if obj.executed_amount_quote else 0.0,
            "is_filled": obj.is_filled if hasattr(obj, 'is_filled') else False,
            "is_open": obj.is_open if hasattr(obj, 'is_open') else False,
        }
    # Handle Pydantic models
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(mode='json')
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class ExecutorService:
    """
    Service for managing trading executors without Docker containers.

    This service provides:
    - Dynamic executor creation for any market/connector
    - Executor lifecycle management (start, stop, cleanup)
    - Real-time executor status monitoring
    - Database persistence of executor state and history
    """

    # Mapping of executor type strings to (executor_class, config_class)
    EXECUTOR_REGISTRY: Dict[str, tuple[Type[ExecutorBase], Type[ExecutorConfigBase]]] = {
        "position_executor": (PositionExecutor, PositionExecutorConfig),
        "grid_executor": (GridExecutor, GridExecutorConfig),
        "dca_executor": (DCAExecutor, DCAExecutorConfig),
        "arbitrage_executor": (ArbitrageExecutor, ArbitrageExecutorConfig),
        "twap_executor": (TWAPExecutor, TWAPExecutorConfig),
        "xemm_executor": (XEMMExecutor, XEMMExecutorConfig),
        "order_executor": (OrderExecutor, OrderExecutorConfig),
    }

    def __init__(
        self,
        trading_service: TradingService,
        db_manager: AsyncDatabaseManager,
        default_account: str = "master_account",
        update_interval: float = 1.0,
        max_retries: int = 10
    ):
        """
        Initialize ExecutorService.

        Args:
            trading_service: TradingService for trading operations and interfaces
            db_manager: AsyncDatabaseManager for persistence
            default_account: Default account to use
            update_interval: Executor update interval in seconds
            max_retries: Maximum retries for executor operations
        """
        self._trading_service = trading_service
        self.db_manager = db_manager
        self.default_account = default_account
        self.update_interval = update_interval
        self.max_retries = max_retries

        # Trading interfaces per account (lazy initialized via TradingService)
        self._trading_interfaces: Dict[str, AccountTradingInterface] = {}

        # Active executors: executor_id -> executor instance
        self._active_executors: Dict[str, ExecutorBase] = {}

        # Executor metadata: executor_id -> metadata dict
        self._executor_metadata: Dict[str, Dict[str, Any]] = {}

        # Completed executors (kept for a period for queries)
        self._completed_executors: Dict[str, Dict[str, Any]] = {}

        # Position holds: key = "account_name|connector_name|trading_pair"
        # Tracks aggregated positions from executors stopped with keep_position=True
        self._positions_held: Dict[str, PositionHold] = {}

        # Control loop task
        self._control_loop_task: Optional[asyncio.Task] = None
        self._is_running = False

    def start(self):
        """Start the executor service control loop."""
        if not self._is_running:
            self._is_running = True
            self._control_loop_task = asyncio.create_task(self._control_loop())
            logger.info("ExecutorService started")

    async def stop(self):
        """Stop the executor service and all active executors."""
        self._is_running = False

        if self._control_loop_task:
            self._control_loop_task.cancel()
            try:
                await self._control_loop_task
            except asyncio.CancelledError:
                pass
            self._control_loop_task = None

        # Stop all active executors
        for executor_id in list(self._active_executors.keys()):
            try:
                executor = self._active_executors.get(executor_id)
                if executor:
                    executor.stop()
            except Exception as e:
                logger.error(f"Error stopping executor {executor_id}: {e}")

        # Clear active executors
        self._active_executors.clear()
        self._executor_metadata.clear()

        # Cleanup trading interfaces
        for trading_interface in self._trading_interfaces.values():
            await trading_interface.cleanup()
        self._trading_interfaces.clear()

        logger.info("ExecutorService stopped")

    async def _control_loop(self):
        """Main control loop that updates all active executors."""
        while self._is_running:
            try:
                # Update timestamps for all trading interfaces via TradingService
                self._trading_service.update_all_timestamps()

                # Check for completed executors
                completed_ids = []
                for executor_id, executor in self._active_executors.items():
                    if executor.is_closed:
                        completed_ids.append(executor_id)

                # Handle completed executors
                for executor_id in completed_ids:
                    await self._handle_executor_completion(executor_id)

            except Exception as e:
                logger.error(f"Error in executor control loop: {e}", exc_info=True)

            await asyncio.sleep(self.update_interval)

    def _get_trading_interface(self, account_name: str) -> AccountTradingInterface:
        """Get or create an AccountTradingInterface for the account."""
        if account_name not in self._trading_interfaces:
            self._trading_interfaces[account_name] = self._trading_service.get_trading_interface(account_name)
        return self._trading_interfaces[account_name]

    async def create_executor(
        self,
        executor_config: Dict[str, Any],
        account_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create and start a new executor.

        Args:
            executor_config: Executor configuration dictionary (must include 'type')
            account_name: Account to use (defaults to master_account)

        Returns:
            Dictionary with executor_id and initial status
        """
        account = account_name or self.default_account

        # Get executor type from config
        executor_type = executor_config.get("type")
        if not executor_type:
            raise HTTPException(
                status_code=400,
                detail="executor_config must include 'type' field"
            )

        # Validate executor type
        if executor_type not in self.EXECUTOR_REGISTRY:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid executor type '{executor_type}'. Valid types: {list(self.EXECUTOR_REGISTRY.keys())}"
            )

        # Get trading interface for this account
        trading_interface = self._get_trading_interface(account)

        # Extract connector and trading pair from config
        connector_name = executor_config.get("connector_name")
        trading_pair = executor_config.get("trading_pair")

        if not connector_name:
            raise HTTPException(status_code=400, detail="connector_name is required in executor_config")
        if not trading_pair:
            raise HTTPException(status_code=400, detail="trading_pair is required in executor_config")

        # Ensure connector and market are ready
        await trading_interface.add_market(connector_name, trading_pair)

        # Set timestamp if not provided (required for time-based features like time_limit)
        if "timestamp" not in executor_config or executor_config["timestamp"] is None:
            executor_config["timestamp"] = trading_interface.current_timestamp

        # Create typed executor config
        executor_class, config_class = self.EXECUTOR_REGISTRY[executor_type]
        try:
            typed_config = config_class(**executor_config)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid executor config: {str(e)}"
            )

        # Create the executor instance
        try:
            executor = executor_class(
                strategy=trading_interface,
                config=typed_config,
                update_interval=self.update_interval,
                max_retries=self.max_retries
            )
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to create executor: {str(e)}"
            )

        # Store executor and metadata
        executor_id = typed_config.id
        self._active_executors[executor_id] = executor
        self._executor_metadata[executor_id] = {
            "account_name": account,
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "executor_type": executor_type,
            "created_at": datetime.now(timezone.utc),
            "config": executor_config
        }

        # Start the executor
        executor.start()

        # Persist to database
        await self._persist_executor_created(executor_id, executor)

        logger.info(f"Created {executor_type} executor {executor_id} for {connector_name}/{trading_pair}")

        return {
            "executor_id": executor_id,
            "executor_type": executor_type,
            "connector_name": connector_name,
            "trading_pair": trading_pair,
            "status": executor.status.name,
            "created_at": self._executor_metadata[executor_id]["created_at"].isoformat()
        }

    def get_executors(
        self,
        account_name: Optional[str] = None,
        connector_name: Optional[str] = None,
        trading_pair: Optional[str] = None,
        executor_type: Optional[str] = None,
        status: Optional[str] = None,
        include_completed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get list of executors with optional filtering.

        Args:
            account_name: Filter by account name
            connector_name: Filter by connector name
            trading_pair: Filter by trading pair
            executor_type: Filter by executor type
            status: Filter by status
            include_completed: Include recently completed executors

        Returns:
            List of executor information dictionaries
        """
        result = []

        # Process active executors
        for executor_id, executor in self._active_executors.items():
            metadata = self._executor_metadata.get(executor_id, {})

            # Apply filters
            if account_name and metadata.get("account_name") != account_name:
                continue
            if connector_name and metadata.get("connector_name") != connector_name:
                continue
            if trading_pair and metadata.get("trading_pair") != trading_pair:
                continue
            if executor_type and metadata.get("executor_type") != executor_type:
                continue
            if status and executor.status.name != status:
                continue

            result.append(self._format_executor_info(executor_id, executor))

        # Include completed executors if requested
        if include_completed:
            for executor_id, completed_info in self._completed_executors.items():
                # Apply same filters to completed executors
                if account_name and completed_info.get("account_name") != account_name:
                    continue
                if connector_name and completed_info.get("connector_name") != connector_name:
                    continue
                if trading_pair and completed_info.get("trading_pair") != trading_pair:
                    continue
                if executor_type and completed_info.get("executor_type") != executor_type:
                    continue

                result.append(completed_info)

        return result

    def get_executor(self, executor_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a specific executor.

        Args:
            executor_id: The executor ID

        Returns:
            Detailed executor information or None if not found
        """
        # Check active executors first
        executor = self._active_executors.get(executor_id)
        if executor:
            return self._format_executor_info(executor_id, executor)

        # Check completed executors
        completed_info = self._completed_executors.get(executor_id)
        if completed_info:
            return completed_info

        return None

    async def stop_executor(
        self,
        executor_id: str,
        keep_position: bool = False
    ) -> Dict[str, Any]:
        """
        Stop an active executor.

        Args:
            executor_id: The executor ID to stop
            keep_position: Whether to keep the position open

        Returns:
            Dictionary with stop confirmation
        """
        executor = self._active_executors.get(executor_id)
        if not executor:
            raise HTTPException(status_code=404, detail=f"Executor {executor_id} not found")

        if executor.is_closed:
            raise HTTPException(status_code=400, detail=f"Executor {executor_id} is already closed")

        # Trigger early stop
        try:
            executor.early_stop(keep_position=keep_position)
        except Exception as e:
            logger.error(f"Error stopping executor {executor_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Error stopping executor: {str(e)}")

        logger.info(f"Initiated stop for executor {executor_id} (keep_position={keep_position})")

        return {
            "executor_id": executor_id,
            "status": "stopping",
            "keep_position": keep_position
        }

    async def _handle_executor_completion(self, executor_id: str):
        """Handle cleanup when an executor completes."""
        executor = self._active_executors.get(executor_id)
        if not executor:
            return

        metadata = self._executor_metadata.get(executor_id, {})

        # Format final executor info
        final_info = self._format_executor_info(executor_id, executor)
        final_info["completed_at"] = datetime.now(timezone.utc).isoformat()

        # Store in completed executors
        self._completed_executors[executor_id] = final_info

        # Check if this is a POSITION_HOLD close type (keep_position=True)
        if executor.close_type == CloseType.POSITION_HOLD:
            await self._aggregate_position_hold(executor_id, executor, metadata)

        # Persist final state to database
        await self._persist_executor_completed(executor_id, executor)

        # Remove from active executors
        del self._active_executors[executor_id]
        if executor_id in self._executor_metadata:
            del self._executor_metadata[executor_id]

        close_type = executor.close_type.name if executor.close_type else "UNKNOWN"
        logger.info(f"Executor {executor_id} completed with close_type: {close_type}")

    def _format_executor_info(
        self,
        executor_id: str,
        executor: ExecutorBase
    ) -> Dict[str, Any]:
        """Format executor information for API response.

        Uses Pydantic's model_dump(mode='json') for automatic serialization
        of Decimal, Enum, and other complex types.
        """
        metadata = self._executor_metadata.get(executor_id, {})

        try:
            # Use model_dump() then our custom serializer to handle TrackedOrder etc.
            executor_info = executor.executor_info
            result = json.loads(json.dumps(executor_info.model_dump(), default=_json_default))

            # Add our metadata (not part of ExecutorInfo model)
            result["executor_id"] = executor_id
            result["executor_type"] = metadata.get("executor_type")
            result["account_name"] = metadata.get("account_name")
            result["created_at"] = metadata.get("created_at").isoformat() if metadata.get("created_at") else None

            # Ensure connector_name and trading_pair from metadata take precedence
            if metadata.get("connector_name"):
                result["connector_name"] = metadata.get("connector_name")
            if metadata.get("trading_pair"):
                result["trading_pair"] = metadata.get("trading_pair")

            return result

        except Exception as e:
            # Fallback when executor_info validation fails (e.g., timestamp=None)
            logger.warning(f"Error accessing executor_info for {executor_id}: {e}")

            # Try to get real values directly from executor
            try:
                is_trading = executor.is_trading if hasattr(executor, 'is_trading') else False
            except Exception:
                is_trading = False

            try:
                raw_custom_info = executor.get_custom_info() if hasattr(executor, 'get_custom_info') else None
                # Convert to JSON-safe format (handles Decimals, Enums, etc.)
                if raw_custom_info:
                    custom_info = json.loads(json.dumps(raw_custom_info, default=_json_default))
                else:
                    custom_info = None
            except Exception:
                custom_info = None

            try:
                net_pnl_quote = float(executor.net_pnl_quote) if hasattr(executor, 'net_pnl_quote') else 0.0
                net_pnl_pct = float(executor.net_pnl_pct) if hasattr(executor, 'net_pnl_pct') else 0.0
                cum_fees_quote = float(executor.cum_fees_quote) if hasattr(executor, 'cum_fees_quote') else 0.0
                filled_amount_quote = float(executor.filled_amount_quote) if hasattr(executor, 'filled_amount_quote') else 0.0
            except Exception:
                net_pnl_quote = 0.0
                net_pnl_pct = 0.0
                cum_fees_quote = 0.0
                filled_amount_quote = 0.0

            return {
                "executor_id": executor_id,
                "executor_type": metadata.get("executor_type"),
                "account_name": metadata.get("account_name"),
                "connector_name": metadata.get("connector_name"),
                "trading_pair": metadata.get("trading_pair"),
                "side": None,
                "status": executor.status.name if hasattr(executor, 'status') else "UNKNOWN",
                "is_active": not executor.is_closed if hasattr(executor, 'is_closed') else True,
                "is_trading": is_trading,
                "timestamp": None,
                "created_at": metadata.get("created_at").isoformat() if metadata.get("created_at") else None,
                "close_type": executor.close_type.name if hasattr(executor, 'close_type') and executor.close_type else None,
                "close_timestamp": None,
                "controller_id": None,
                "net_pnl_quote": net_pnl_quote,
                "net_pnl_pct": net_pnl_pct,
                "cum_fees_quote": cum_fees_quote,
                "filled_amount_quote": filled_amount_quote,
                "config": metadata.get("config"),
                "custom_info": custom_info,
            }

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics for all executors.

        Returns:
            Dictionary with aggregate statistics
        """
        executors = self.get_executors(include_completed=True)

        active_count = sum(1 for e in executors if e.get("is_active", False))
        completed_count = len(executors) - active_count
        total_pnl = sum(e.get("net_pnl_quote", 0) for e in executors)
        total_volume = sum(e.get("filled_amount_quote", 0) for e in executors)

        by_type: Dict[str, int] = {}
        by_connector: Dict[str, int] = {}
        by_status: Dict[str, int] = {}

        for e in executors:
            ex_type = e.get("executor_type", "unknown")
            connector = e.get("connector_name", "unknown")
            status = e.get("status", "unknown")

            by_type[ex_type] = by_type.get(ex_type, 0) + 1
            by_connector[connector] = by_connector.get(connector, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "total_active": active_count,
            "total_completed": completed_count,
            "total_pnl_quote": total_pnl,
            "total_volume_quote": total_volume,
            "by_type": by_type,
            "by_connector": by_connector,
            "by_status": by_status
        }

    async def _persist_executor_created(self, executor_id: str, executor: ExecutorBase):
        """Persist executor creation to database."""
        if not self.db_manager:
            return

        try:
            metadata = self._executor_metadata.get(executor_id, {})

            async with self.db_manager.get_session_context() as session:
                from database.repositories.executor_repository import ExecutorRepository
                repo = ExecutorRepository(session)

                await repo.create_executor(
                    executor_id=executor_id,
                    executor_type=metadata.get("executor_type"),
                    account_name=metadata.get("account_name"),
                    connector_name=metadata.get("connector_name"),
                    trading_pair=metadata.get("trading_pair"),
                    config=json.dumps(metadata.get("config", {}), default=_json_default),
                    status=executor.status.name
                )

            logger.debug(f"Persisted executor {executor_id} creation to database")

        except Exception as e:
            logger.error(f"Error persisting executor creation: {e}")

    async def _persist_executor_completed(self, executor_id: str, executor: ExecutorBase):
        """Persist executor completion to database."""
        if not self.db_manager:
            return

        try:
            # Try to get executor_info, handle validation errors (e.g., timestamp=None)
            try:
                executor_info = executor.executor_info
                status_name = executor_info.status.name
                close_type = executor_info.close_type.name if executor_info.close_type else None
                net_pnl_quote = executor_info.net_pnl_quote
                net_pnl_pct = executor_info.net_pnl_pct
                cum_fees_quote = executor_info.cum_fees_quote
                filled_amount_quote = executor_info.filled_amount_quote
                custom_info = executor_info.custom_info
            except Exception as e:
                # Fallback when executor_info validation fails
                logger.debug(f"Error accessing executor_info for persistence: {e}")
                status_name = executor.status.name if hasattr(executor, 'status') else "UNKNOWN"
                close_type = executor.close_type.name if hasattr(executor, 'close_type') and executor.close_type else None
                net_pnl_quote = Decimal("0")
                net_pnl_pct = Decimal("0")
                cum_fees_quote = Decimal("0")
                filled_amount_quote = Decimal("0")
                custom_info = None

            async with self.db_manager.get_session_context() as session:
                from database.repositories.executor_repository import ExecutorRepository
                repo = ExecutorRepository(session)

                await repo.update_executor(
                    executor_id=executor_id,
                    status=status_name,
                    close_type=close_type,
                    net_pnl_quote=net_pnl_quote,
                    net_pnl_pct=net_pnl_pct,
                    cum_fees_quote=cum_fees_quote,
                    filled_amount_quote=filled_amount_quote,
                    final_state=json.dumps(custom_info, default=_json_default) if custom_info else None
                )

            logger.debug(f"Persisted executor {executor_id} completion to database")

        except Exception as e:
            logger.error(f"Error persisting executor completion: {e}")

    def remove_completed_executor(self, executor_id: str) -> bool:
        """
        Remove a completed executor from tracking.

        Args:
            executor_id: The executor ID to remove

        Returns:
            True if removed, False if not found
        """
        if executor_id in self._completed_executors:
            del self._completed_executors[executor_id]
            return True
        return False

    # ========================================
    # Position Hold Tracking Methods
    # ========================================

    def _get_position_key(
        self,
        account_name: str,
        connector_name: str,
        trading_pair: str
    ) -> str:
        """Generate a unique key for position tracking."""
        return f"{account_name}|{connector_name}|{trading_pair}"

    async def _aggregate_position_hold(
        self,
        executor_id: str,
        executor: ExecutorBase,
        metadata: Dict[str, Any]
    ):
        """
        Aggregate position data from an executor stopped with keep_position=True.

        This extracts the filled amounts from the executor and adds them to
        the aggregated position tracking.
        """
        account_name = metadata.get("account_name", self.default_account)
        connector_name = metadata.get("connector_name", "")
        trading_pair = metadata.get("trading_pair", "")

        if not connector_name or not trading_pair:
            logger.warning(f"Cannot aggregate position for executor {executor_id}: missing connector/pair info")
            return

        position_key = self._get_position_key(account_name, connector_name, trading_pair)

        # Get or create position hold
        if position_key not in self._positions_held:
            self._positions_held[position_key] = PositionHold(
                trading_pair=trading_pair,
                connector_name=connector_name,
                account_name=account_name
            )

        position = self._positions_held[position_key]

        # Extract filled amounts from executor
        try:
            # Try to get executor info
            try:
                executor_info = executor.executor_info
                custom_info = executor_info.custom_info or {}
            except Exception:
                custom_info = executor.get_custom_info() if hasattr(executor, 'get_custom_info') else {}

            # Get side from config or custom_info
            config = metadata.get("config", {})
            side = config.get("side", custom_info.get("side", "BUY"))

            # Extract filled amounts - try different sources
            filled_amount_base = Decimal("0")
            filled_amount_quote = Decimal("0")

            # Try from executor attributes directly
            if hasattr(executor, 'filled_amount_base'):
                filled_amount_base = Decimal(str(executor.filled_amount_base or 0))
            if hasattr(executor, 'filled_amount_quote'):
                filled_amount_quote = Decimal(str(executor.filled_amount_quote or 0))

            # Fallback to custom_info
            if filled_amount_base == 0 and custom_info:
                filled_amount_base = Decimal(str(custom_info.get("filled_amount_base", 0)))
            if filled_amount_quote == 0 and custom_info:
                filled_amount_quote = Decimal(str(custom_info.get("filled_amount_quote", 0)))

            # For grid executors, aggregate from held_position_orders
            if metadata.get("executor_type") == "grid_executor" and custom_info:
                buy_filled_base = Decimal("0")
                buy_filled_quote = Decimal("0")
                sell_filled_base = Decimal("0")
                sell_filled_quote = Decimal("0")

                # held_position_orders contains the orders kept when keep_position=True
                held_orders = custom_info.get("held_position_orders", [])

                for order in held_orders:
                    if isinstance(order, dict):
                        trade_type = order.get("trade_type", "BUY")
                        exec_base = Decimal(str(order.get("executed_amount_base", 0)))
                        exec_quote = Decimal(str(order.get("executed_amount_quote", 0)))

                        if trade_type == "BUY":
                            buy_filled_base += exec_base
                            buy_filled_quote += exec_quote
                        else:
                            sell_filled_base += exec_base
                            sell_filled_quote += exec_quote

                # Add buy and sell fills separately
                if buy_filled_base > 0:
                    position.add_fill("BUY", buy_filled_base, buy_filled_quote, executor_id)
                if sell_filled_base > 0:
                    position.add_fill("SELL", sell_filled_base, sell_filled_quote, executor_id)

                logger.info(
                    f"Aggregated grid executor {executor_id} to position {position_key}: "
                    f"buy={buy_filled_base} base, sell={sell_filled_base} base"
                )

            elif filled_amount_base > 0:
                # For non-grid executors with a single side
                position.add_fill(side, filled_amount_base, filled_amount_quote, executor_id)
                logger.info(
                    f"Aggregated executor {executor_id} to position {position_key}: "
                    f"{side} {filled_amount_base} base @ {filled_amount_quote} quote"
                )
            else:
                logger.debug(f"Executor {executor_id} has no filled amounts to aggregate")

        except Exception as e:
            logger.error(f"Error aggregating position for executor {executor_id}: {e}", exc_info=True)

    def get_positions_held(
        self,
        account_name: Optional[str] = None,
        connector_name: Optional[str] = None,
        trading_pair: Optional[str] = None
    ) -> List[PositionHold]:
        """
        Get held positions with optional filtering.

        Args:
            account_name: Filter by account name
            connector_name: Filter by connector name
            trading_pair: Filter by trading pair

        Returns:
            List of PositionHold objects matching the filters
        """
        positions = []

        for position in self._positions_held.values():
            # Apply filters
            if account_name and position.account_name != account_name:
                continue
            if connector_name and position.connector_name != connector_name:
                continue
            if trading_pair and position.trading_pair != trading_pair:
                continue

            # Only include positions with actual volume
            if position.buy_amount_base > 0 or position.sell_amount_base > 0:
                positions.append(position)

        return positions

    def get_position_held(
        self,
        account_name: str,
        connector_name: str,
        trading_pair: str
    ) -> Optional[PositionHold]:
        """
        Get a specific held position.

        Args:
            account_name: Account name
            connector_name: Connector name
            trading_pair: Trading pair

        Returns:
            PositionHold or None if not found
        """
        position_key = self._get_position_key(account_name, connector_name, trading_pair)
        return self._positions_held.get(position_key)

    def clear_position_held(
        self,
        account_name: str,
        connector_name: str,
        trading_pair: str
    ) -> bool:
        """
        Clear a specific held position (after manual close or full exit).

        Args:
            account_name: Account name
            connector_name: Connector name
            trading_pair: Trading pair

        Returns:
            True if cleared, False if not found
        """
        position_key = self._get_position_key(account_name, connector_name, trading_pair)
        if position_key in self._positions_held:
            del self._positions_held[position_key]
            logger.info(f"Cleared position hold for {position_key}")
            return True
        return False

    def get_positions_summary(self) -> Dict[str, Any]:
        """
        Get summary of all held positions.

        Returns:
            Dictionary with total positions, PnL, and position list
        """
        positions = self.get_positions_held()
        total_realized_pnl = sum(float(p.realized_pnl_quote) for p in positions)

        return {
            "total_positions": len(positions),
            "total_realized_pnl": total_realized_pnl,
            "positions": [
                {
                    "trading_pair": p.trading_pair,
                    "connector_name": p.connector_name,
                    "account_name": p.account_name,
                    "buy_amount_base": float(p.buy_amount_base),
                    "buy_amount_quote": float(p.buy_amount_quote),
                    "sell_amount_base": float(p.sell_amount_base),
                    "sell_amount_quote": float(p.sell_amount_quote),
                    "net_amount_base": float(p.net_amount_base),
                    "buy_breakeven_price": float(p.buy_breakeven_price) if p.buy_breakeven_price else None,
                    "sell_breakeven_price": float(p.sell_breakeven_price) if p.sell_breakeven_price else None,
                    "matched_amount_base": float(p.matched_amount_base),
                    "unmatched_amount_base": float(p.unmatched_amount_base),
                    "position_side": p.position_side,
                    "realized_pnl_quote": float(p.realized_pnl_quote),
                    "executor_count": len(p.executor_ids),
                    "executor_ids": p.executor_ids,
                    "last_updated": p.last_updated.isoformat() if p.last_updated else None
                }
                for p in positions
            ]
        }
