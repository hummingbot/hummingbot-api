from datetime import datetime
from typing import Optional, List, Dict, Any, Callable
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


def paginate_by_cursor(
    items: List[Dict[str, Any]],
    cursor: Optional[str],
    limit: int,
    sort_key: Optional[Callable[[Dict[str, Any]], Any]] = None,
    reverse: bool = False,
) -> PaginatedResponse:
    """
    Apply in-memory cursor-based pagination over items carrying a "_cursor_id" key.

    Each item must already have a "_cursor_id" assigned by the caller. The items are sorted
    (by "_cursor_id" unless a custom sort_key is provided), the page after the cursor is sliced,
    has_more/next_cursor are computed, and "_cursor_id" is stripped from the returned page.

    Args:
        items: Items to paginate, each with a "_cursor_id" key
        cursor: Cursor value ("_cursor_id" of the last item of the previous page), if any
        limit: Number of items per page
        sort_key: Optional sort key; defaults to sorting by "_cursor_id"
        reverse: Whether to sort in descending order

    Returns:
        PaginatedResponse with the page data and pagination metadata
    """
    # Sort for consistent pagination
    items.sort(key=sort_key if sort_key is not None else (lambda x: x.get("_cursor_id", "")), reverse=reverse)

    # Find the item after the cursor
    start_index = 0
    if cursor:
        for i, item in enumerate(items):
            if item.get("_cursor_id") == cursor:
                start_index = i + 1
                break

    # Get page of results
    end_index = start_index + limit
    page_items = items[start_index:end_index]

    # Determine next cursor and has_more
    has_more = end_index < len(items)
    next_cursor = page_items[-1].get("_cursor_id") if page_items and has_more else None

    # Clean up cursor_id from response data
    for item in page_items:
        item.pop("_cursor_id", None)

    return PaginatedResponse(
        data=page_items,
        pagination={
            "limit": limit,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "total_count": len(items),
        },
    )