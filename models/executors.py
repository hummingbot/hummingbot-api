"""
Pydantic models for executor API endpoints.

These models wrap Hummingbot's executor configuration types and provide
validation for the REST API.
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

from .pagination import PaginationParams

# ========================================
# Position Hold for Aggregated Tracking
# ========================================


class PositionHold(BaseModel):
    """
    Tracks aggregated position from executors stopped with keep_position=True.

    Similar to hummingbot's PositionHold, this tracks:
    - Separate buy/sell amounts for proper breakeven calculation
    - Matched volume (realized PnL) vs unmatched volume (unrealized PnL)
    - Aggregation across multiple executors on the same trading pair
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    trading_pair: str = Field(description="Trading pair (e.g., 'BTC-USDT')")
    connector_name: str = Field(description="Connector name")
    account_name: str = Field(description="Account name")

    # Buy side tracking
    buy_amount_base: Decimal = Field(default=Decimal("0"), description="Total bought amount in base currency")
    buy_amount_quote: Decimal = Field(default=Decimal("0"), description="Total spent on buys in quote currency")

    # Sell side tracking
    sell_amount_base: Decimal = Field(default=Decimal("0"), description="Total sold amount in base currency")
    sell_amount_quote: Decimal = Field(default=Decimal("0"), description="Total received from sells in quote currency")

    # Realized PnL from matched positions
    realized_pnl_quote: Decimal = Field(default=Decimal("0"), description="Realized PnL from matched buy/sell pairs")

    # Tracking
    executor_ids: List[str] = Field(default_factory=list, description="IDs of executors contributing to this position")
    last_updated: Optional[datetime] = Field(default=None, description="Last update timestamp")

    @computed_field
    @property
    def net_amount_base(self) -> Decimal:
        """Net position in base currency (positive = long, negative = short)."""
        return self.buy_amount_base - self.sell_amount_base

    @computed_field
    @property
    def buy_breakeven_price(self) -> Optional[Decimal]:
        """Average buy price (breakeven for long position)."""
        if self.buy_amount_base > 0:
            return self.buy_amount_quote / self.buy_amount_base
        return None

    @computed_field
    @property
    def sell_breakeven_price(self) -> Optional[Decimal]:
        """Average sell price (breakeven for short position)."""
        if self.sell_amount_base > 0:
            return self.sell_amount_quote / self.sell_amount_base
        return None

    @computed_field
    @property
    def matched_amount_base(self) -> Decimal:
        """Amount that has been matched (min of buy/sell)."""
        return min(self.buy_amount_base, self.sell_amount_base)

    @computed_field
    @property
    def unmatched_amount_base(self) -> Decimal:
        """Absolute unmatched position size."""
        return abs(self.net_amount_base)

    @computed_field
    @property
    def position_side(self) -> Optional[str]:
        """Current position side: LONG, SHORT, or FLAT."""
        if self.net_amount_base > 0:
            return "LONG"
        elif self.net_amount_base < 0:
            return "SHORT"
        return "FLAT"

    def add_fill(
        self,
        side: str,
        amount_base: Decimal,
        amount_quote: Decimal,
        executor_id: Optional[str] = None
    ):
        """
        Add a fill to the position tracking.

        Args:
            side: "BUY" or "SELL"
            amount_base: Amount in base currency
            amount_quote: Amount in quote currency
            executor_id: Optional executor ID to track
        """
        if side.upper() == "BUY":
            self.buy_amount_base += amount_base
            self.buy_amount_quote += amount_quote
        else:
            self.sell_amount_base += amount_base
            self.sell_amount_quote += amount_quote

        # Calculate realized PnL when we have matched volume
        self._calculate_realized_pnl()

        if executor_id and executor_id not in self.executor_ids:
            self.executor_ids.append(executor_id)

        self.last_updated = datetime.utcnow()

    def _calculate_realized_pnl(self):
        """Calculate realized PnL from matched buy/sell pairs using FIFO."""
        matched = self.matched_amount_base
        if matched > 0 and self.buy_amount_base > 0 and self.sell_amount_base > 0:
            # Average prices
            avg_buy = self.buy_amount_quote / self.buy_amount_base
            avg_sell = self.sell_amount_quote / self.sell_amount_base
            # Realized PnL = matched_amount * (avg_sell - avg_buy)
            self.realized_pnl_quote = matched * (avg_sell - avg_buy)

    def get_unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """
        Calculate unrealized PnL for unmatched position.

        Args:
            current_price: Current market price

        Returns:
            Unrealized PnL in quote currency
        """
        if self.net_amount_base > 0:
            # Long position: profit if price goes up
            avg_buy = self.buy_breakeven_price or Decimal("0")
            return self.net_amount_base * (current_price - avg_buy)
        elif self.net_amount_base < 0:
            # Short position: profit if price goes down
            avg_sell = self.sell_breakeven_price or Decimal("0")
            return abs(self.net_amount_base) * (avg_sell - current_price)
        return Decimal("0")

    def merge(self, other: "PositionHold"):
        """Merge another PositionHold into this one."""
        self.buy_amount_base += other.buy_amount_base
        self.buy_amount_quote += other.buy_amount_quote
        self.sell_amount_base += other.sell_amount_base
        self.sell_amount_quote += other.sell_amount_quote

        for eid in other.executor_ids:
            if eid not in self.executor_ids:
                self.executor_ids.append(eid)

        self._calculate_realized_pnl()
        self.last_updated = datetime.utcnow()


class PositionHoldResponse(BaseModel):
    """API response model for PositionHold."""
    trading_pair: str
    connector_name: str
    account_name: str
    buy_amount_base: float
    buy_amount_quote: float
    sell_amount_base: float
    sell_amount_quote: float
    net_amount_base: float
    buy_breakeven_price: Optional[float]
    sell_breakeven_price: Optional[float]
    matched_amount_base: float
    unmatched_amount_base: float
    position_side: Optional[str]
    realized_pnl_quote: float
    unrealized_pnl_quote: Optional[float] = None
    executor_count: int
    executor_ids: List[str]
    last_updated: Optional[str]


class PositionsSummaryResponse(BaseModel):
    """Summary of all held positions."""
    total_positions: int = Field(description="Number of active position holds")
    total_realized_pnl: float = Field(description="Total realized PnL across all positions")
    total_unrealized_pnl: Optional[float] = Field(
        default=None, description="Total unrealized PnL (None if no rates available)"
    )
    positions: List[PositionHoldResponse] = Field(description="List of position holds")


# ========================================
# Executor Type Definitions
# ========================================

EXECUTOR_TYPES = Literal[
    "position_executor",
    "grid_executor",
    "dca_executor",
    "arbitrage_executor",
    "twap_executor",
    "xemm_executor",
    "order_executor",
    "lp_executor"
]


# ========================================
# API Request Models
# ========================================

class CreateExecutorRequest(BaseModel):
    """Request to create a new executor."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_name": "master_account",
                "executor_config": {
                    "type": "position_executor",
                    "connector_name": "binance_perpetual",
                    "trading_pair": "BTC-USDT",
                    "side": "BUY",
                    "amount": "0.01",
                    "leverage": 10,
                    "triple_barrier_config": {
                        "stop_loss": "0.02",
                        "take_profit": "0.04",
                        "time_limit": 3600
                    }
                }
            }
        }
    )

    account_name: Optional[str] = Field(
        None,
        description="Account name to use (defaults to master_account)"
    )
    executor_config: Dict[str, Any] = Field(
        ...,
        description="Executor configuration. Must include 'type' field and executor-specific parameters."
    )


class StopExecutorRequest(BaseModel):
    """Request to stop an executor."""
    keep_position: bool = Field(
        default=False,
        description="Whether to keep the position open (for position executors)"
    )


class ExecutorFilterRequest(PaginationParams):
    """Request to filter and list executors."""
    account_names: Optional[List[str]] = Field(
        None,
        description="Filter by account names"
    )
    connector_names: Optional[List[str]] = Field(
        None,
        description="Filter by connector names"
    )
    trading_pairs: Optional[List[str]] = Field(
        None,
        description="Filter by trading pairs"
    )
    executor_types: Optional[List[EXECUTOR_TYPES]] = Field(
        None,
        description="Filter by executor types"
    )
    status: Optional[str] = Field(
        None,
        description="Filter by status (RUNNING, TERMINATED, etc.)"
    )


# ========================================
# API Response Models
# ========================================

class ExecutorResponse(BaseModel):
    """Response for a single executor (summary view)."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "executor_id": "abc123...",
                "executor_type": "position_executor",
                "account_name": "master_account",
                "connector_name": "binance_perpetual",
                "trading_pair": "BTC-USDT",
                "side": "BUY",
                "status": "RUNNING",
                "is_active": True,
                "is_trading": True,
                "timestamp": 1705315800.0,
                "created_at": "2024-01-15T10:30:00Z",
                "close_type": None,
                "close_timestamp": None,
                "controller_id": None,
                "net_pnl_quote": 125.50,
                "net_pnl_pct": 2.5,
                "cum_fees_quote": 1.25,
                "filled_amount_quote": 5000.0
            }
        }
    )

    executor_id: str = Field(description="Unique executor identifier")
    executor_type: Optional[str] = Field(description="Type of executor")
    account_name: Optional[str] = Field(description="Account name")
    connector_name: Optional[str] = Field(description="Connector name")
    trading_pair: Optional[str] = Field(description="Trading pair")
    side: Optional[str] = Field(None, description="Trade side (BUY/SELL) if applicable")
    status: str = Field(description="Current status (RUNNING, TERMINATED, etc.)")
    is_active: bool = Field(description="Whether the executor is active")
    is_trading: bool = Field(description="Whether the executor has open trades")
    timestamp: Optional[float] = Field(None, description="Creation timestamp (Unix)")
    created_at: Optional[str] = Field(None, description="Creation timestamp (ISO format)")
    close_type: Optional[str] = Field(None, description="How the executor was closed (if applicable)")
    close_timestamp: Optional[float] = Field(None, description="Close timestamp (Unix)")
    controller_id: Optional[str] = Field(None, description="ID of the controller that spawned this executor")
    net_pnl_quote: float = Field(description="Net PnL in quote currency")
    net_pnl_pct: float = Field(description="Net PnL percentage")
    cum_fees_quote: float = Field(description="Cumulative fees in quote currency")
    filled_amount_quote: float = Field(description="Total filled amount in quote currency")
    error_count: int = Field(default=0, description="Number of ERROR-level log entries captured")
    last_error: Optional[str] = Field(default=None, description="Most recent error message, if any")


class ExecutorDetailResponse(ExecutorResponse):
    """Detailed response for a single executor."""
    config: Optional[Dict[str, Any]] = Field(
        None,
        description="Full executor configuration"
    )
    custom_info: Optional[Dict[str, Any]] = Field(
        None,
        description="Executor-specific custom information"
    )


class CreateExecutorResponse(BaseModel):
    """Response after creating an executor."""
    executor_id: str = Field(description="Unique executor identifier")
    executor_type: str = Field(description="Type of executor created")
    connector_name: str = Field(description="Connector name")
    trading_pair: str = Field(description="Trading pair")
    status: str = Field(description="Initial status")
    created_at: str = Field(description="Creation timestamp (ISO format)")


class StopExecutorResponse(BaseModel):
    """Response after stopping an executor."""
    executor_id: str = Field(description="Executor identifier")
    status: str = Field(description="New status (usually 'stopping')")
    keep_position: bool = Field(description="Whether position was kept open")


class ExecutorsSummaryResponse(BaseModel):
    """Summary of active executors."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total_active": 5,
                "total_pnl_quote": 1234.56,
                "total_volume_quote": 50000.00,
                "by_type": {"position_executor": 3, "grid_executor": 2},
                "by_connector": {"binance_perpetual": 4, "binance": 1},
                "by_status": {"RUNNING": 5}
            }
        }
    )

    total_active: int = Field(description="Number of active executors")
    total_pnl_quote: float = Field(description="Total PnL across active executors")
    total_volume_quote: float = Field(description="Total volume across active executors")
    by_type: Dict[str, int] = Field(description="Executor count by type")
    by_connector: Dict[str, int] = Field(description="Executor count by connector")
    by_status: Dict[str, int] = Field(description="Executor count by status")


class ExecutorLogEntry(BaseModel):
    """A single log entry from an executor."""
    timestamp: str = Field(description="ISO-format timestamp")
    level: str = Field(description="Log level (DEBUG, INFO, WARNING, ERROR)")
    message: str = Field(description="Log message")
    exc_info: Optional[str] = Field(default=None, description="Exception traceback if present")


class ExecutorLogsResponse(BaseModel):
    """Response for executor log entries."""
    executor_id: str = Field(description="Executor identifier")
    logs: List[ExecutorLogEntry] = Field(description="Log entries")
    total_count: int = Field(description="Total number of log entries (before limit)")
