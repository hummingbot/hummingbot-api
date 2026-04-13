from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


# Script file operations
class Script(BaseModel):
    """Script file content"""
    content: str = Field(description="Script source code")


class ScriptResponse(BaseModel):
    """Response for getting a script"""
    name: str = Field(description="Script name")
    content: str = Field(description="Script source code")


# Script configuration operations
class ScriptConfig(BaseModel):
    """Script configuration content"""
    config_name: str = Field(description="Configuration name")
    script_file_name: str = Field(description="Script file name")
    controllers_config: List[str] = Field(default=[], description="List of controller configurations")
    candles_config: List[Dict[str, Any]] = Field(default=[], description="Candles configuration")
    markets: Dict[str, Any] = Field(default={}, description="Markets configuration")


class ScriptConfigResponse(BaseModel):
    """Response for script configuration with metadata"""
    config_name: str = Field(description="Configuration name")
    script_file_name: str = Field(description="Script file name")
    controllers_config: List[str] = Field(default=[], description="List of controller configurations")
    candles_config: List[Dict[str, Any]] = Field(default=[], description="Candles configuration")
    markets: Dict[str, Any] = Field(default={}, description="Markets configuration")
    error: Optional[str] = Field(None, description="Error message if config is malformed")