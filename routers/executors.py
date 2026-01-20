"""
Executor Router - REST API endpoints for dynamic executor management.

This router enables running Hummingbot executors directly via API
without Docker containers or full strategy setup.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from deps import get_executor_service
from models.executors import (
    CreateExecutorRequest,
    CreateExecutorResponse,
    DeleteExecutorResponse,
    ExecutorDetailResponse,
    ExecutorFilterRequest,
    ExecutorResponse,
    ExecutorsSummaryResponse,
    StopExecutorRequest,
    StopExecutorResponse,
)
from models.pagination import PaginatedResponse
from services.executor_service import ExecutorService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Executors"], prefix="/executors")


@router.post("/", response_model=CreateExecutorResponse, status_code=status.HTTP_201_CREATED)
async def create_executor(
    request: CreateExecutorRequest,
    executor_service: ExecutorService = Depends(get_executor_service)
):
    """
    Create and start a new executor.

    Supported executor types:
    - **position_executor**: Single position with triple barrier (stop loss, take profit, time limit)
    - **grid_executor**: Grid trading with multiple levels
    - **dca_executor**: Dollar-cost averaging with multiple entry points
    - **twap_executor**: Time-weighted average price execution
    - **arbitrage_executor**: Cross-exchange arbitrage
    - **xemm_executor**: Cross-exchange market making
    - **order_executor**: Simple order execution

    The `executor_config` must include:
    - `type`: One of the executor types above
    - `connector_name`: Exchange connector (e.g., "binance", "binance_perpetual")
    - `trading_pair`: Trading pair (e.g., "BTC-USDT")
    - Additional type-specific configuration

    Returns the created executor ID and initial status.
    """
    try:
        result = await executor_service.create_executor(
            executor_config=request.executor_config,
            account_name=request.account_name
        )
        return CreateExecutorResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating executor: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error creating executor: {str(e)}")


@router.post("/search", response_model=PaginatedResponse)
async def list_executors(
    filter_request: ExecutorFilterRequest,
    executor_service: ExecutorService = Depends(get_executor_service)
):
    """
    Get list of active executors with optional filtering.

    Filters:
    - `account_names`: Filter by specific accounts
    - `connector_names`: Filter by connectors
    - `trading_pairs`: Filter by trading pairs
    - `executor_types`: Filter by executor types
    - `status`: Filter by status (RUNNING, TERMINATED, etc.)
    - `include_completed`: Include recently completed executors

    Returns paginated list of executor summaries.
    """
    try:
        # Get filtered executors
        executors = executor_service.get_executors(
            account_name=filter_request.account_names[0] if filter_request.account_names else None,
            connector_name=filter_request.connector_names[0] if filter_request.connector_names else None,
            trading_pair=filter_request.trading_pairs[0] if filter_request.trading_pairs else None,
            executor_type=filter_request.executor_types[0] if filter_request.executor_types else None,
            status=filter_request.status,
            include_completed=filter_request.include_completed
        )

        # Apply additional multi-value filters
        if filter_request.account_names and len(filter_request.account_names) > 1:
            executors = [e for e in executors if e.get("account_name") in filter_request.account_names]
        if filter_request.connector_names and len(filter_request.connector_names) > 1:
            executors = [e for e in executors if e.get("connector_name") in filter_request.connector_names]
        if filter_request.trading_pairs and len(filter_request.trading_pairs) > 1:
            executors = [e for e in executors if e.get("trading_pair") in filter_request.trading_pairs]
        if filter_request.executor_types and len(filter_request.executor_types) > 1:
            executors = [e for e in executors if e.get("executor_type") in filter_request.executor_types]

        # Apply cursor-based pagination
        start_idx = 0
        if filter_request.cursor:
            for i, ex in enumerate(executors):
                if ex.get("executor_id") == filter_request.cursor:
                    start_idx = i + 1
                    break

        end_idx = start_idx + filter_request.limit
        page_data = executors[start_idx:end_idx]
        has_more = end_idx < len(executors)
        next_cursor = page_data[-1]["executor_id"] if page_data and has_more else None

        return PaginatedResponse(
            data=page_data,
            pagination={
                "limit": filter_request.limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "total_count": len(executors)
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing executors: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error listing executors: {str(e)}")


@router.get("/summary", response_model=ExecutorsSummaryResponse)
async def get_executors_summary(
    executor_service: ExecutorService = Depends(get_executor_service)
):
    """
    Get summary statistics for all executors.

    Returns aggregate information including:
    - Total active/completed executor counts
    - Total PnL and volume
    - Breakdown by executor type, connector, and status
    """
    try:
        summary = executor_service.get_summary()
        return ExecutorsSummaryResponse(**summary)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting executor summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting summary: {str(e)}")


@router.get("/{executor_id}", response_model=ExecutorDetailResponse)
async def get_executor(
    executor_id: str,
    executor_service: ExecutorService = Depends(get_executor_service)
):
    """
    Get detailed information about a specific executor.

    Returns full executor information including:
    - Current status and PnL
    - Full configuration
    - Executor-specific custom information
    """
    try:
        executor = executor_service.get_executor(executor_id)

        if not executor:
            raise HTTPException(status_code=404, detail=f"Executor {executor_id} not found")

        return ExecutorDetailResponse(**executor)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting executor {executor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting executor: {str(e)}")


@router.post("/{executor_id}/stop", response_model=StopExecutorResponse)
async def stop_executor(
    executor_id: str,
    request: StopExecutorRequest,
    executor_service: ExecutorService = Depends(get_executor_service)
):
    """
    Stop an active executor.

    Options:
    - `keep_position`: If true, keeps any open position (for position executors).
      If false, the executor will attempt to close all positions before stopping.

    Returns confirmation of the stop action.
    """
    try:
        result = await executor_service.stop_executor(
            executor_id=executor_id,
            keep_position=request.keep_position
        )
        return StopExecutorResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error stopping executor {executor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error stopping executor: {str(e)}")


@router.delete("/{executor_id}", response_model=DeleteExecutorResponse)
async def delete_executor(
    executor_id: str,
    executor_service: ExecutorService = Depends(get_executor_service)
):
    """
    Remove an executor from tracking.

    The executor must be already stopped/completed. This removes it from
    the active tracking list but preserves database records for historical queries.

    Returns success message if removed.
    """
    try:
        # Check if executor exists
        executor = executor_service.get_executor(executor_id)
        if not executor:
            raise HTTPException(status_code=404, detail=f"Executor {executor_id} not found")

        # Check if still active
        if executor.get("is_active", False):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete active executor. Stop it first using POST /executors/{executor_id}/stop"
            )

        # Remove from tracking
        removed = executor_service.remove_completed_executor(executor_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"Executor {executor_id} not found in completed list")

        return DeleteExecutorResponse(
            message=f"Executor {executor_id} removed from tracking",
            executor_id=executor_id
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting executor {executor_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error deleting executor: {str(e)}")


@router.get("/types/available")
async def get_available_executor_types():
    """
    Get list of available executor types with descriptions.

    Returns information about each supported executor type.
    """
    return {
        "executor_types": [
            {
                "type": "position_executor",
                "description": "Single position with triple barrier (stop loss, take profit, time limit)",
                "use_case": "Directional trading with risk management"
            },
            {
                "type": "grid_executor",
                "description": "Grid trading with multiple buy/sell levels",
                "use_case": "Range-bound market trading"
            },
            {
                "type": "dca_executor",
                "description": "Dollar-cost averaging with multiple entry points",
                "use_case": "Gradual position building"
            },
            {
                "type": "twap_executor",
                "description": "Time-weighted average price execution",
                "use_case": "Large order execution with minimal market impact"
            },
            {
                "type": "arbitrage_executor",
                "description": "Cross-exchange price arbitrage",
                "use_case": "Exploiting price differences between exchanges"
            },
            {
                "type": "xemm_executor",
                "description": "Cross-exchange market making",
                "use_case": "Providing liquidity across exchanges"
            },
            {
                "type": "order_executor",
                "description": "Simple order execution with retry logic",
                "use_case": "Basic order placement with reliability"
            }
        ]
    }


def _extract_field_info(schema: dict, definitions: dict) -> list:
    """
    Extract field information from a JSON schema.

    Returns list of field dicts with: name, type, description, required, default, constraints
    """
    fields = []
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    for field_name, field_schema in properties.items():
        # Skip internal fields
        if field_name.startswith("_"):
            continue

        field_info = {
            "name": field_name,
            "required": field_name in required_fields,
        }

        # Resolve $ref if present
        if "$ref" in field_schema:
            ref_path = field_schema["$ref"].split("/")[-1]
            if ref_path in definitions:
                field_schema = {**definitions[ref_path], **field_schema}
                del field_schema["$ref"]

        # Handle anyOf (usually Optional types)
        if "anyOf" in field_schema:
            types = []
            for option in field_schema["anyOf"]:
                if "$ref" in option:
                    ref_name = option["$ref"].split("/")[-1]
                    types.append(ref_name)
                elif option.get("type") == "null":
                    field_info["required"] = False
                else:
                    types.append(option.get("type", "any"))
            field_info["type"] = types[0] if len(types) == 1 else f"Union[{', '.join(types)}]"
        elif "allOf" in field_schema:
            # Handle allOf (usually inheritance)
            refs = [opt["$ref"].split("/")[-1] for opt in field_schema["allOf"] if "$ref" in opt]
            field_info["type"] = refs[0] if refs else "object"
        elif "enum" in field_schema:
            field_info["type"] = "enum"
            field_info["enum_values"] = field_schema["enum"]
        elif "type" in field_schema:
            field_info["type"] = field_schema["type"]
        else:
            field_info["type"] = "any"

        # Extract description
        if "description" in field_schema:
            field_info["description"] = field_schema["description"]
        elif "title" in field_schema:
            field_info["description"] = field_schema["title"]

        # Extract default value
        if "default" in field_schema:
            field_info["default"] = field_schema["default"]

        # Extract constraints
        constraints = {}
        if "minimum" in field_schema:
            constraints["minimum"] = field_schema["minimum"]
        if "maximum" in field_schema:
            constraints["maximum"] = field_schema["maximum"]
        if "exclusiveMinimum" in field_schema:
            constraints["exclusive_minimum"] = field_schema["exclusiveMinimum"]
        if "exclusiveMaximum" in field_schema:
            constraints["exclusive_maximum"] = field_schema["exclusiveMaximum"]
        if "minLength" in field_schema:
            constraints["min_length"] = field_schema["minLength"]
        if "maxLength" in field_schema:
            constraints["max_length"] = field_schema["maxLength"]
        if "pattern" in field_schema:
            constraints["pattern"] = field_schema["pattern"]
        if "ge" in field_schema:
            constraints["ge"] = field_schema["ge"]
        if "le" in field_schema:
            constraints["le"] = field_schema["le"]
        if "gt" in field_schema:
            constraints["gt"] = field_schema["gt"]
        if "lt" in field_schema:
            constraints["lt"] = field_schema["lt"]

        if constraints:
            field_info["constraints"] = constraints

        fields.append(field_info)

    return fields


@router.get("/types/{executor_type}/config")
async def get_executor_config_schema(executor_type: str):
    """
    Get configuration schema for a specific executor type.

    Returns detailed information about each configuration field including:
    - **name**: Field name
    - **type**: Data type (str, int, Decimal, enum, etc.)
    - **description**: Field description
    - **required**: Whether the field is required
    - **default**: Default value if any
    - **constraints**: Validation constraints (min, max, pattern, etc.)
    - **enum_values**: Possible values for enum types

    Also returns nested type definitions for complex fields.
    """
    from services.executor_service import ExecutorService

    if executor_type not in ExecutorService.EXECUTOR_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown executor type '{executor_type}'. Valid types: {list(ExecutorService.EXECUTOR_REGISTRY.keys())}"
        )

    _, config_class = ExecutorService.EXECUTOR_REGISTRY[executor_type]

    try:
        # Get JSON schema from pydantic model
        schema = config_class.model_json_schema()
        definitions = schema.get("$defs", {})

        # Extract field information
        fields = _extract_field_info(schema, definitions)

        # Extract nested type definitions
        nested_types = {}
        for def_name, def_schema in definitions.items():
            if "properties" in def_schema:
                nested_types[def_name] = {
                    "description": def_schema.get("description", def_schema.get("title", "")),
                    "fields": _extract_field_info(def_schema, definitions)
                }
            elif "enum" in def_schema:
                nested_types[def_name] = {
                    "type": "enum",
                    "values": def_schema["enum"],
                    "description": def_schema.get("description", def_schema.get("title", ""))
                }

        return {
            "executor_type": executor_type,
            "config_class": config_class.__name__,
            "description": schema.get("description", schema.get("title", "")),
            "fields": fields,
            "nested_types": nested_types
        }

    except Exception as e:
        logger.error(f"Error extracting config schema for {executor_type}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error extracting config schema: {str(e)}"
        )
