"""
Pydantic models for the connectors router.

These models define the request/response schemas for connector-related endpoints.
"""

from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field


class ConnectorInfo(BaseModel):
    """Information about a connector"""
    name: str = Field(description="Connector name")
    is_perpetual: bool = Field(default=False, description="Whether the connector supports perpetual trading")
    supported_order_types: Optional[List[str]] = Field(default=None, description="Supported order types")


class ConnectorConfigMapResponse(BaseModel):
    """Response for connector configuration requirements"""
    connector_name: str = Field(description="Name of the connector")
    config_fields: List[str] = Field(description="List of required configuration fields")


class TradingRule(BaseModel):
    """Trading rules for a specific trading pair"""
    min_order_size: float = Field(description="Minimum order size")
    max_order_size: float = Field(description="Maximum order size")
    min_price_increment: float = Field(description="Minimum price increment")
    min_base_amount_increment: float = Field(description="Minimum base amount increment")
    min_quote_amount_increment: float = Field(description="Minimum quote amount increment")
    min_notional_size: float = Field(description="Minimum notional size")
    min_order_value: float = Field(description="Minimum order value")
    max_price_significant_digits: float = Field(description="Maximum price significant digits")
    supports_limit_orders: bool = Field(description="Whether limit orders are supported")
    supports_market_orders: bool = Field(description="Whether market orders are supported")
    buy_order_collateral_token: str = Field(description="Collateral token for buy orders")
    sell_order_collateral_token: str = Field(description="Collateral token for sell orders")


class ConnectorTradingRulesResponse(BaseModel):
    """Response for connector trading rules"""
    connector: str = Field(description="Connector name")
    trading_pairs: Optional[List[str]] = Field(default=None, description="Filtered trading pairs if provided")
    rules: Dict[str, TradingRule] = Field(description="Trading rules by trading pair")


class ConnectorOrderTypesResponse(BaseModel):
    """Response for supported order types"""
    connector: str = Field(description="Connector name")
    supported_order_types: List[str] = Field(description="List of supported order types")


class ConnectorListResponse(BaseModel):
    """Response for list of available connectors"""
    connectors: List[str] = Field(description="List of available connector names")
    count: int = Field(description="Total number of connectors")