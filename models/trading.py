from typing import Dict, List, Optional, Any, Literal
from pydantic import BaseModel, Field, field_validator
from decimal import Decimal
from datetime import datetime
from hummingbot.core.data_type.common import OrderType, TradeType, PositionAction
from .pagination import PaginationParams, TimeRangePaginationParams


class TradeRequest(BaseModel):
    """Request model for placing trades"""
    account_name: str = Field(description="Name of the account to trade with")
    connector_name: str = Field(description="Name of the connector/exchange")
    trading_pair: str = Field(description="Trading pair (e.g., BTC-USDT)")
    trade_type: Literal["BUY", "SELL"] = Field(description="Whether to buy or sell")
    amount: Decimal = Field(description="Amount to trade", gt=0)
    order_type: Literal["LIMIT", "MARKET", "LIMIT_MAKER"] = Field(default="LIMIT", description="Type of order")
    price: Optional[Decimal] = Field(default=None, description="Price for limit orders")
    position_action: Literal["OPEN", "CLOSE"] = Field(default="OPEN", description="Position action for perpetual contracts (OPEN/CLOSE)")

    @field_validator('trade_type')
    @classmethod
    def validate_trade_type(cls, v):
        """Validate that trade_type is a valid TradeType enum name."""
        try:
            return TradeType[v].name
        except KeyError:
            valid_types = [t.name for t in TradeType]
            raise ValueError(f"Invalid trade_type '{v}'. Must be one of: {valid_types}")

    @field_validator('order_type')
    @classmethod
    def validate_order_type(cls, v):
        """Validate that order_type is a valid OrderType enum name."""
        try:
            return OrderType[v].name
        except KeyError:
            valid_types = [t.name for t in OrderType]
            raise ValueError(f"Invalid order_type '{v}'. Must be one of: {valid_types}")

    @field_validator('position_action')
    @classmethod
    def validate_position_action(cls, v):
        """Validate that position_action is a valid PositionAction enum name."""
        try:
            return PositionAction[v].name
        except KeyError:
            valid_actions = [a.name for a in PositionAction]
            raise ValueError(f"Invalid position_action '{v}'. Must be one of: {valid_actions}")


class TradeResponse(BaseModel):
    """Response model for trade execution"""
    order_id: str = Field(description="Client order ID assigned by the connector")
    account_name: str = Field(description="Account used for the trade")
    connector_name: str = Field(description="Connector used for the trade")
    trading_pair: str = Field(description="Trading pair")
    trade_type: str = Field(description="Trade type")
    amount: Decimal = Field(description="Trade amount")
    order_type: str = Field(description="Order type")
    price: Optional[Decimal] = Field(description="Order price")
    status: str = Field(default="submitted", description="Order status")


class TokenInfo(BaseModel):
    """Information about a token balance"""
    token: str = Field(description="Token symbol")
    balance: Decimal = Field(description="Token balance")
    value_usd: Optional[Decimal] = Field(None, description="USD value of the balance")


class ConnectorBalance(BaseModel):
    """Balance information for a connector"""
    connector_name: str = Field(description="Name of the connector")
    tokens: List[TokenInfo] = Field(description="List of token balances")


class AccountBalance(BaseModel):
    """Balance information for an account"""
    account_name: str = Field(description="Name of the account")
    connectors: List[ConnectorBalance] = Field(description="List of connector balances")


class PortfolioState(BaseModel):
    """Complete portfolio state across all accounts"""
    accounts: List[AccountBalance] = Field(description="List of account balances")
    timestamp: datetime = Field(description="Timestamp of the portfolio state")


class OrderInfo(BaseModel):
    """Information about an order"""
    order_id: str = Field(description="Order ID")
    client_order_id: str = Field(description="Client order ID")
    account_name: str = Field(description="Account name")
    connector_name: str = Field(description="Connector name")
    trading_pair: str = Field(description="Trading pair")
    order_type: str = Field(description="Order type")
    trade_type: str = Field(description="Trade type (BUY/SELL)")
    amount: Decimal = Field(description="Order amount")
    price: Optional[Decimal] = Field(description="Order price")
    filled_amount: Decimal = Field(description="Filled amount")
    status: str = Field(description="Order status")
    creation_timestamp: datetime = Field(description="Order creation time")
    last_update_timestamp: datetime = Field(description="Last update time")


class ActiveOrdersResponse(BaseModel):
    """Response for active orders"""
    orders: Dict[str, OrderInfo] = Field(description="Dictionary of active orders")


class OrderSummary(BaseModel):
    """Summary statistics for orders"""
    total_orders: int = Field(description="Total number of orders")
    filled_orders: int = Field(description="Number of filled orders")
    cancelled_orders: int = Field(description="Number of cancelled orders")
    fill_rate: float = Field(description="Order fill rate percentage")
    total_volume_base: Decimal = Field(description="Total volume in base currency")
    total_volume_quote: Decimal = Field(description="Total volume in quote currency")
    avg_fill_time: Optional[float] = Field(description="Average fill time in seconds")


class TradeInfo(BaseModel):
    """Information about a trade fill"""
    trade_id: str = Field(description="Trade ID")
    order_id: str = Field(description="Associated order ID")
    account_name: str = Field(description="Account name")
    connector_name: str = Field(description="Connector name")
    trading_pair: str = Field(description="Trading pair")
    trade_type: str = Field(description="Trade type (BUY/SELL)")
    amount: Decimal = Field(description="Trade amount")
    price: Decimal = Field(description="Trade price")
    fee: Decimal = Field(description="Trade fee")
    timestamp: datetime = Field(description="Trade timestamp")


class TradingRulesInfo(BaseModel):
    """Trading rules for a trading pair"""
    trading_pair: str = Field(description="Trading pair")
    min_order_size: Decimal = Field(description="Minimum order size")
    max_order_size: Optional[Decimal] = Field(description="Maximum order size")
    min_price_increment: Decimal = Field(description="Minimum price increment")
    min_base_amount_increment: Decimal = Field(description="Minimum base amount increment")
    min_quote_amount_increment: Decimal = Field(description="Minimum quote amount increment")


class OrderTypesResponse(BaseModel):
    """Response for supported order types"""
    connector: str = Field(description="Connector name")
    supported_order_types: List[str] = Field(description="List of supported order types")


class OrderFilterRequest(TimeRangePaginationParams):
    """Request model for filtering orders with multiple criteria"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")
    trading_pairs: Optional[List[str]] = Field(default=None, description="List of trading pairs to filter by")
    status: Optional[str] = Field(default=None, description="Order status filter")


class ActiveOrderFilterRequest(PaginationParams):
    """Request model for filtering active orders"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")
    trading_pairs: Optional[List[str]] = Field(default=None, description="List of trading pairs to filter by")


class PositionFilterRequest(PaginationParams):
    """Request model for filtering positions"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")


class FundingPaymentFilterRequest(TimeRangePaginationParams):
    """Request model for filtering funding payments"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")
    trading_pair: Optional[str] = Field(default=None, description="Filter by trading pair")


class TradeFilterRequest(TimeRangePaginationParams):
    """Request model for filtering trades"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")
    trading_pairs: Optional[List[str]] = Field(default=None, description="List of trading pairs to filter by")
    trade_types: Optional[List[str]] = Field(default=None, description="List of trade types to filter by (BUY/SELL)")


class PortfolioStateFilterRequest(BaseModel):
    """Request model for filtering portfolio state"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")
    skip_gateway: bool = Field(default=False, description="Skip Gateway wallet balance updates for faster CEX-only queries")
    refresh: bool = Field(default=False, description="If True, refresh balances before returning. If False, return cached state")


class PortfolioHistoryFilterRequest(TimeRangePaginationParams):
    """Request model for filtering portfolio history"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")
    interval: Optional[str] = Field(
        default="5m",
        description="Data sampling interval: 5m, 15m, 30m, 1h, 4h, 12h, 1d. Default is 5m (raw data)"
    )

    @field_validator('interval')
    @classmethod
    def validate_interval(cls, v):
        """Validate that interval is a supported value."""
        valid_intervals = ["5m", "15m", "30m", "1h", "4h", "12h", "1d"]
        if v not in valid_intervals:
            raise ValueError(f"Invalid interval '{v}'. Must be one of: {valid_intervals}")
        return v


class PortfolioDistributionFilterRequest(BaseModel):
    """Request model for filtering portfolio distribution"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")


class AccountsDistributionFilterRequest(BaseModel):
    """Request model for filtering accounts distribution"""
    account_names: Optional[List[str]] = Field(default=None, description="List of account names to filter by")
    connector_names: Optional[List[str]] = Field(default=None, description="List of connector names to filter by")