from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class LeverageRequest(BaseModel):
    """Request model for setting leverage on perpetual connectors"""
    trading_pair: str = Field(description="Trading pair (e.g., BTC-USDT)")
    leverage: int = Field(description="Leverage value (typically 1-125)", ge=1, le=125)


class PositionModeRequest(BaseModel):
    """Request model for setting position mode on perpetual connectors"""
    position_mode: str = Field(description="Position mode (HEDGE or ONEWAY)")
    trading_pair: Optional[str] = Field(
        default=None,
        description="Pair to register on the connector before switching. Position-mode "
                    "implementations apply the switch through the connector's trading "
                    "pairs, so at least one registered pair is required.")


class CredentialRequest(BaseModel):
    """Request model for adding connector credentials"""
    credentials: Dict[str, Any] = Field(description="Connector credentials dictionary")
