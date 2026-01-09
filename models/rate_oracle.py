"""
Pydantic models for the rate oracle router.

These models define the request/response schemas for rate oracle configuration endpoints.
"""

from typing import Optional, List, Dict
from enum import Enum
from pydantic import BaseModel, Field


class RateOracleSourceEnum(str, Enum):
    """Available rate oracle sources."""
    BINANCE = "binance"
    BINANCE_US = "binance_us"
    COIN_GECKO = "coin_gecko"
    COIN_CAP = "coin_cap"
    KUCOIN = "kucoin"
    ASCEND_EX = "ascend_ex"
    GATE_IO = "gate_io"
    COINBASE_ADVANCED_TRADE = "coinbase_advanced_trade"
    CUBE = "cube"
    DEXALOT = "dexalot"
    HYPERLIQUID = "hyperliquid"
    DERIVE = "derive"
    TEGRO = "tegro"


class GlobalTokenConfig(BaseModel):
    """Global token configuration for displaying values."""
    global_token_name: str = Field(
        default="USDT",
        description="The token to use as global quote (e.g., USDT, USD, BTC)"
    )
    global_token_symbol: str = Field(
        default="$",
        description="Symbol to display for the global token"
    )


class RateOracleSourceConfig(BaseModel):
    """Rate oracle source configuration."""
    name: RateOracleSourceEnum = Field(
        default=RateOracleSourceEnum.BINANCE,
        description="The rate oracle source to use for price data"
    )


class RateOracleConfig(BaseModel):
    """Complete rate oracle configuration."""
    rate_oracle_source: RateOracleSourceConfig = Field(
        default_factory=RateOracleSourceConfig,
        description="Rate oracle source configuration"
    )
    global_token: GlobalTokenConfig = Field(
        default_factory=GlobalTokenConfig,
        description="Global token configuration"
    )


class RateOracleConfigResponse(BaseModel):
    """Response for rate oracle configuration GET endpoint."""
    rate_oracle_source: RateOracleSourceConfig = Field(
        description="Current rate oracle source configuration"
    )
    global_token: GlobalTokenConfig = Field(
        description="Current global token configuration"
    )
    available_sources: List[str] = Field(
        description="List of available rate oracle sources"
    )


class RateOracleConfigUpdateRequest(BaseModel):
    """Request model for updating rate oracle configuration."""
    rate_oracle_source: Optional[RateOracleSourceConfig] = Field(
        default=None,
        description="New rate oracle source configuration (optional)"
    )
    global_token: Optional[GlobalTokenConfig] = Field(
        default=None,
        description="New global token configuration (optional)"
    )


class RateOracleConfigUpdateResponse(BaseModel):
    """Response for rate oracle configuration update."""
    success: bool = Field(description="Whether the update was successful")
    message: str = Field(description="Status message")
    config: RateOracleConfig = Field(description="Updated configuration")


class RateRequest(BaseModel):
    """Request for getting rates."""
    trading_pairs: List[str] = Field(
        description="List of trading pairs to get rates for (e.g., ['BTC-USDT', 'ETH-USDT'])"
    )


class RateResponse(BaseModel):
    """Response containing rates for trading pairs."""
    source: str = Field(description="Rate oracle source used")
    quote_token: str = Field(description="Quote token used")
    rates: Dict[str, Optional[float]] = Field(
        description="Mapping of trading pairs to their rates (None if rate not found)"
    )


class SingleRateResponse(BaseModel):
    """Response for a single trading pair rate."""
    trading_pair: str = Field(description="The trading pair")
    rate: Optional[float] = Field(description="The rate (None if not found)")
    source: str = Field(description="Rate oracle source used")
    quote_token: str = Field(description="Quote token used")
