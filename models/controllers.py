from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from enum import Enum


class ControllerType(str, Enum):
    """Types of controllers available"""
    DIRECTIONAL_TRADING = "directional_trading"
    MARKET_MAKING = "market_making"
    GENERIC = "generic"


# Controller file operations
class Controller(BaseModel):
    """Controller file content"""
    content: str = Field(description="Controller source code")
    type: Optional[ControllerType] = Field(None, description="Controller type (optional for flexibility)")


class ControllerResponse(BaseModel):
    """Response for getting a controller"""
    name: str = Field(description="Controller name")
    type: str = Field(description="Controller type")
    content: str = Field(description="Controller source code")


# Controller configuration operations
class ControllerConfig(BaseModel):
    """Controller configuration"""
    controller_name: str = Field(description="Controller name")
    controller_type: str = Field(description="Controller type")
    connector_name: Optional[str] = Field(None, description="Connector name")
    trading_pair: Optional[str] = Field(None, description="Trading pair")
    total_amount_quote: Optional[float] = Field(None, description="Total amount in quote currency")


class ControllerConfigResponse(BaseModel):
    """Response for controller configuration with metadata"""
    config_name: str = Field(description="Configuration name")
    controller_name: str = Field(description="Controller name")
    controller_type: str = Field(description="Controller type")
    connector_name: Optional[str] = Field(None, description="Connector name")
    trading_pair: Optional[str] = Field(None, description="Trading pair")
    total_amount_quote: Optional[float] = Field(None, description="Total amount in quote currency")
    error: Optional[str] = Field(None, description="Error message if config is malformed")


# Bot-specific controller configurations
class BotControllerConfig(BaseModel):
    """Controller configuration for a specific bot"""
    config_name: str = Field(description="Configuration name")
    config_data: Dict[str, Any] = Field(description="Configuration data")