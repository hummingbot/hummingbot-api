from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class PaginationParams(BaseModel):
    """Common pagination parameters."""
    limit: int = Field(default=100, ge=1, le=1000, description="Number of items per page")
    cursor: Optional[str] = Field(None, description="Cursor for next page")


class TimeRangePaginationParams(BaseModel):
    """Time-based pagination parameters for trading endpoints using integer timestamps."""
    limit: int = Field(default=100, ge=1, le=1000, description="Number of items per page")
    start_time: Optional[int] = Field(None, description="Start time as Unix timestamp in milliseconds")
    end_time: Optional[int] = Field(None, description="End time as Unix timestamp in milliseconds")
    cursor: Optional[str] = Field(None, description="Cursor for next page")
    

class PaginatedResponse(BaseModel):
    """Generic paginated response."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "data": [],
                "pagination": {
                    "limit": 100,
                    "has_more": True,
                    "next_cursor": "2024-01-10T12:00:00",
                    "total_count": 500
                }
            }
        }
    )
    
    data: List[Dict[str, Any]]
    pagination: Dict[str, Any]