"""
Pydantic models for the archived bots router.

These models define the request/response schemas for archived bot analysis endpoints.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


class OrderStatus(str, Enum):
    """Order status enumeration"""
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class DatabaseStatus(BaseModel):
    """Database status information"""
    db_path: str = Field(description="Path to the database file")
    status: Dict[str, Any] = Field(description="Database health status")
    healthy: bool = Field(description="Whether the database is healthy")


class BotSummary(BaseModel):
    """Summary information for an archived bot"""
    bot_name: str = Field(description="Name of the bot")
    start_time: Optional[datetime] = Field(default=None, description="Bot start time")
    end_time: Optional[datetime] = Field(default=None, description="Bot end time")
    total_trades: int = Field(default=0, description="Total number of trades")
    total_orders: int = Field(default=0, description="Total number of orders")
    markets: List[str] = Field(default_factory=list, description="List of traded markets")
    strategies: List[str] = Field(default_factory=list, description="List of strategies used")


class PerformanceMetrics(BaseModel):
    """Performance metrics for an archived bot"""
    total_pnl: float = Field(description="Total profit and loss")
    total_volume: float = Field(description="Total trading volume")
    avg_return: float = Field(description="Average return per trade")
    win_rate: float = Field(description="Percentage of winning trades")
    sharpe_ratio: Optional[float] = Field(default=None, description="Sharpe ratio")
    max_drawdown: Optional[float] = Field(default=None, description="Maximum drawdown")
    total_trades: int = Field(description="Total number of trades")


class TradeDetail(BaseModel):
    """Detailed trade information"""
    id: Optional[int] = Field(default=None, description="Trade ID")
    config_file_path: str = Field(description="Configuration file path")
    strategy: str = Field(description="Strategy name")
    connector_name: str = Field(description="Connector name")
    trading_pair: str = Field(description="Trading pair")
    base_asset: str = Field(description="Base asset")
    quote_asset: str = Field(description="Quote asset")
    timestamp: datetime = Field(description="Trade timestamp")
    order_id: str = Field(description="Order ID")
    trade_type: str = Field(description="Trade type (BUY/SELL)")
    price: float = Field(description="Trade price")
    amount: float = Field(description="Trade amount")
    trade_fee: Dict[str, float] = Field(description="Trade fees")
    exchange_trade_id: str = Field(description="Exchange trade ID")
    leverage: Optional[int] = Field(default=None, description="Leverage used")
    position: Optional[str] = Field(default=None, description="Position type")


class OrderDetail(BaseModel):
    """Detailed order information"""
    id: Optional[int] = Field(default=None, description="Order ID")
    client_order_id: str = Field(description="Client order ID")
    exchange_order_id: Optional[str] = Field(default=None, description="Exchange order ID")
    trading_pair: str = Field(description="Trading pair")
    status: OrderStatus = Field(description="Order status")
    order_type: str = Field(description="Order type")
    amount: float = Field(description="Order amount")
    price: Optional[float] = Field(default=None, description="Order price")
    creation_timestamp: datetime = Field(description="Order creation time")
    last_update_timestamp: Optional[datetime] = Field(default=None, description="Last update time")
    filled_amount: Optional[float] = Field(default=None, description="Filled amount")
    leverage: Optional[int] = Field(default=None, description="Leverage used")
    position: Optional[str] = Field(default=None, description="Position type")


class ExecutorInfo(BaseModel):
    """Executor information"""
    controller_id: str = Field(description="Controller ID")
    timestamp: datetime = Field(description="Timestamp")
    type: str = Field(description="Executor type")
    controller_config: Dict[str, Any] = Field(description="Controller configuration")
    net_pnl_flat: float = Field(description="Net PnL in flat terms")
    net_pnl_pct: float = Field(description="Net PnL percentage")
    total_executors: int = Field(description="Total number of executors")
    total_amount: float = Field(description="Total amount")
    total_spent: float = Field(description="Total spent")


class ArchivedBotListResponse(BaseModel):
    """Response for listing archived bots"""
    bots: List[str] = Field(description="List of archived bot database paths")
    count: int = Field(description="Total number of archived bots")


class BotPerformanceResponse(BaseModel):
    """Response for bot performance analysis"""
    bot_name: str = Field(description="Bot name")
    metrics: PerformanceMetrics = Field(description="Performance metrics")
    period_start: Optional[datetime] = Field(default=None, description="Analysis period start")
    period_end: Optional[datetime] = Field(default=None, description="Analysis period end")


class TradeHistoryResponse(BaseModel):
    """Response for trade history"""
    trades: List[TradeDetail] = Field(description="List of trades")
    total: int = Field(description="Total number of trades")
    page: int = Field(description="Current page")
    page_size: int = Field(description="Page size")


class OrderHistoryResponse(BaseModel):
    """Response for order history"""
    orders: List[OrderDetail] = Field(description="List of orders")
    total: int = Field(description="Total number of orders")
    page: int = Field(description="Current page")
    page_size: int = Field(description="Page size")
    filtered_by_status: Optional[OrderStatus] = Field(default=None, description="Status filter applied")


class ExecutorsResponse(BaseModel):
    """Response for executors information"""
    executors: List[ExecutorInfo] = Field(description="List of executors")
    total: int = Field(description="Total number of executors")