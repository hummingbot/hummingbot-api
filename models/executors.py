"""
Pydantic models for executor API endpoints.

These models wrap Hummingbot's executor configuration types and provide
validation for the REST API.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .pagination import PaginationParams


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
    "order_executor"
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
    include_completed: bool = Field(
        default=False,
        description="Include recently completed executors"
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
    """Summary of all executors."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total_active": 5,
                "total_completed": 23,
                "total_pnl_quote": 1234.56,
                "total_volume_quote": 50000.00,
                "by_type": {"position_executor": 3, "grid_executor": 2},
                "by_connector": {"binance_perpetual": 4, "binance": 1},
                "by_status": {"RUNNING": 5, "TERMINATED": 23}
            }
        }
    )

    total_active: int = Field(description="Number of active executors")
    total_completed: int = Field(description="Number of completed executors")
    total_pnl_quote: float = Field(description="Total PnL across all executors")
    total_volume_quote: float = Field(description="Total volume across all executors")
    by_type: Dict[str, int] = Field(description="Executor count by type")
    by_connector: Dict[str, int] = Field(description="Executor count by connector")
    by_status: Dict[str, int] = Field(description="Executor count by status")


class DeleteExecutorResponse(BaseModel):
    """Response after deleting an executor from tracking."""
    message: str = Field(description="Success message")
    executor_id: str = Field(description="Executor identifier that was removed")
