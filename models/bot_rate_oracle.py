"""
Pydantic models for the bot client defaults endpoints.

The API itself prices through the in-house ticker pool, but deployed bots still run
hummingbot's RateOracle, configured from the conf_client.yml copied out of their
credentials profile. These models describe that persisted configuration, plus the one
other conf_client.yml knob operators tune per profile -- rate_limits_share_pct, the share
of each exchange's API rate limit a bot instance is allowed to spend.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class GlobalTokenConfig(BaseModel):
    """Global token configuration for displaying values."""
    global_token_name: Optional[str] = Field(
        default=None,
        description="The token to use as global quote (e.g., USDT, USD, BTC)"
    )
    global_token_symbol: Optional[str] = Field(
        default=None,
        description="Symbol to display for the global token"
    )

    @field_validator("global_token_name")
    @classmethod
    def _reject_blank_token_name(cls, v: Optional[str]) -> Optional[str]:
        # A blank quote token would make every cross-rate lookup resolve to nothing.
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("global_token_name must not be blank")
        return v


class RateOracleSourceConfig(BaseModel):
    """Rate oracle source configuration."""
    name: str = Field(description="The rate oracle source deployed bots use for price data")


class RateOracleConfig(BaseModel):
    """Bot client defaults persisted in an account's conf_client.yml."""
    account_name: str = Field(description="Credentials profile the configuration belongs to")
    rate_oracle_source: RateOracleSourceConfig
    global_token: GlobalTokenConfig
    # Bounds mirror hummingbot's own ClientConfigMap.rate_limits_share_pct (gt=0, le=100):
    # the file is read back by hummingbot inside the bot container, so a value it would
    # reject must not be writable here.
    rate_limits_share_pct: float = Field(
        default=100.0,
        gt=0,
        le=100,
        description="Percentage of each exchange's API rate limit allocated to a bot instance"
    )


class RateOracleConfigResponse(RateOracleConfig):
    """Response for the rate oracle configuration GET endpoint."""
    available_sources: List[str] = Field(description="Rate oracle sources that can be configured")


class RateOracleConfigUpdateRequest(BaseModel):
    """Request model for updating the rate oracle configuration."""
    rate_oracle_source: Optional[RateOracleSourceConfig] = Field(
        default=None,
        description="New rate oracle source configuration (optional)"
    )
    global_token: Optional[GlobalTokenConfig] = Field(
        default=None,
        description="New global token configuration (optional)"
    )
    rate_limits_share_pct: Optional[float] = Field(
        default=None,
        gt=0,
        le=100,
        description="New share of each exchange's API rate limit for a bot instance, 1-100 (optional)"
    )


class RateOracleConfigUpdateResponse(BaseModel):
    """Response for a bot client defaults update."""
    success: bool = Field(description="Whether the update was successful")
    message: str = Field(description="Status message")
    config: RateOracleConfig = Field(description="Updated configuration")
