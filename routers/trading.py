import logging
import math

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

# Create module-specific logger
logger = logging.getLogger(__name__)
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType
from pydantic import BaseModel
from starlette import status

from deps import get_accounts_service, get_market_data_feed_manager
from models import (
    ActiveOrderFilterRequest,
    FundingPaymentFilterRequest,
    OrderFilterRequest,
    PaginatedResponse,
    PositionFilterRequest,
    TradeFilterRequest,
    TradeRequest,
    TradeResponse,
)
from models.accounts import LeverageRequest, PositionModeRequest
from services.accounts_service import AccountsService

router = APIRouter(tags=["Trading"], prefix="/trading")


# Trade Execution
@router.post("/orders", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
async def place_trade(
    trade_request: TradeRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
    market_data_manager=Depends(get_market_data_feed_manager),
):
    """
    Place a buy or sell order using a specific account and connector.

    Args:
        trade_request: Trading request with account, connector, trading pair, type, amount, etc.
        accounts_service: Injected accounts service
        market_data_manager: Market data manager for price fetching

    Returns:
        TradeResponse with order ID and trading details

    Raises:
        HTTPException: 400 for invalid parameters, 404 for account/connector not found, 500 for trade execution errors
    """
    try:
        # Convert string names to enum instances
        trade_type_enum = TradeType[trade_request.trade_type]
        order_type_enum = OrderType[trade_request.order_type]
        position_action_enum = PositionAction[trade_request.position_action]

        order_id = await accounts_service.place_trade(
            account_name=trade_request.account_name,
            connector_name=trade_request.connector_name,
            trading_pair=trade_request.trading_pair,
            trade_type=trade_type_enum,
            amount=trade_request.amount,
            order_type=order_type_enum,
            price=trade_request.price,
            position_action=position_action_enum,
            market_data_manager=market_data_manager,
        )

        return TradeResponse(
            order_id=order_id,
            account_name=trade_request.account_name,
            connector_name=trade_request.connector_name,
            trading_pair=trade_request.trading_pair,
            trade_type=trade_request.trade_type,
            amount=trade_request.amount,
            order_type=trade_request.order_type,
            price=trade_request.price,
            status="submitted",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error placing trade: {str(e)}")


@router.post("/{account_name}/{connector_name}/orders/{client_order_id}/cancel")
async def cancel_order(
    account_name: str,
    connector_name: str,
    client_order_id: str,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Cancel a specific order by its client order ID.

    Args:
        account_name: Name of the account
        connector_name: Name of the connector
        client_order_id: Client order ID to cancel
        trading_pair: Trading pair for the order
        accounts_service: Injected accounts service

    Returns:
        Success message with cancelled order ID

    Raises:
        HTTPException: 404 if account/connector not found, 500 for cancellation errors
    """
    try:
        cancelled_order_id = await accounts_service.cancel_order(
            account_name=account_name, connector_name=connector_name, client_order_id=client_order_id
        )
        return {"message": f"Order cancellation initiated for {cancelled_order_id}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error cancelling order: {str(e)}")


@router.post("/positions", response_model=PaginatedResponse)
async def get_positions(filter_request: PositionFilterRequest, accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Get current positions across all or filtered perpetual connectors.

    This endpoint fetches real-time position data directly from the connectors,
    including unrealized PnL, leverage, funding fees, and margin information.

    Args:
        filter_request: JSON payload with filtering criteria

    Returns:
        Paginated response with position data and pagination metadata

    Raises:
        HTTPException: 500 if there's an error fetching positions
    """
    try:
        all_positions = []
        all_connectors = accounts_service.connector_manager.get_all_connectors()

        # Filter accounts
        accounts_to_check = filter_request.account_names if filter_request.account_names else list(all_connectors.keys())

        for account_name in accounts_to_check:
            if account_name in all_connectors:
                # Filter connectors
                connectors_to_check = (
                    filter_request.connector_names
                    if filter_request.connector_names
                    else list(all_connectors[account_name].keys())
                )

                for connector_name in connectors_to_check:
                    # Only fetch positions from perpetual connectors
                    if connector_name in all_connectors[account_name] and "_perpetual" in connector_name:
                        try:
                            positions = await accounts_service.get_account_positions(account_name, connector_name)
                            # Add cursor-friendly identifier to each position
                            for position in positions:
                                position["_cursor_id"] = f"{account_name}:{connector_name}:{position.get('trading_pair', '')}"
                            all_positions.extend(positions)
                        except Exception as e:
                            # Log error but continue with other connectors
                            import logging

                            logger.warning(f"Failed to get positions for {account_name}/{connector_name}: {e}")

        # Sort by cursor_id for consistent pagination
        all_positions.sort(key=lambda x: x.get("_cursor_id", ""))

        # Apply cursor-based pagination
        start_index = 0
        if filter_request.cursor:
            # Find the position after the cursor
            for i, position in enumerate(all_positions):
                if position.get("_cursor_id") == filter_request.cursor:
                    start_index = i + 1
                    break

        # Get page of results
        end_index = start_index + filter_request.limit
        page_positions = all_positions[start_index:end_index]

        # Determine next cursor and has_more
        has_more = end_index < len(all_positions)
        next_cursor = page_positions[-1].get("_cursor_id") if page_positions and has_more else None

        # Clean up cursor_id from response data
        for position in page_positions:
            position.pop("_cursor_id", None)

        return PaginatedResponse(
            data=page_positions,
            pagination={
                "limit": filter_request.limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "total_count": len(all_positions),
            },
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching positions: {str(e)}")


# Active Orders Management - Real-time from connectors
@router.post("/orders/active", response_model=PaginatedResponse)
async def get_active_orders(
    filter_request: ActiveOrderFilterRequest, accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get active (in-flight) orders across all or filtered accounts and connectors.

    This endpoint fetches real-time active orders directly from the connectors' in_flight_orders property,
    providing current order status, fill amounts, and other live order data.

    Args:
        filter_request: JSON payload with filtering criteria

    Returns:
        Paginated response with active order data and pagination metadata

    Raises:
        HTTPException: 500 if there's an error fetching orders
    """
    try:
        all_active_orders = []
        all_connectors = accounts_service.connector_manager.get_all_connectors()

        # Use filter request values
        accounts_to_check = filter_request.account_names if filter_request.account_names else list(all_connectors.keys())

        for account_name in accounts_to_check:
            if account_name in all_connectors:
                # Filter connectors
                connectors_to_check = (
                    filter_request.connector_names
                    if filter_request.connector_names
                    else list(all_connectors[account_name].keys())
                )

                for connector_name in connectors_to_check:
                    if connector_name in all_connectors[account_name]:
                        try:
                            connector = all_connectors[account_name][connector_name]
                            # Get in-flight orders directly from connector
                            in_flight_orders = connector.in_flight_orders

                            for client_order_id, order in in_flight_orders.items():
                                # Apply trading pair filter if specified
                                if filter_request.trading_pairs and order.trading_pair not in filter_request.trading_pairs:
                                    continue

                                # Convert to standardized format to match orders search response
                                standardized_order = _standardize_in_flight_order_response(order, account_name, connector_name)
                                standardized_order["_cursor_id"] = client_order_id  # Use client_order_id as cursor
                                all_active_orders.append(standardized_order)

                        except Exception as e:
                            # Log error but continue with other connectors
                            import logging

                            logger.warning(f"Failed to get active orders for {account_name}/{connector_name}: {e}")

        # Sort by cursor_id for consistent pagination
        all_active_orders.sort(key=lambda x: x.get("_cursor_id", ""))

        # Apply cursor-based pagination
        start_index = 0
        if filter_request.cursor:
            # Find the order after the cursor
            for i, order in enumerate(all_active_orders):
                if order.get("_cursor_id") == filter_request.cursor:
                    start_index = i + 1
                    break

        # Get page of results
        end_index = start_index + filter_request.limit
        page_orders = all_active_orders[start_index:end_index]

        # Determine next cursor and has_more
        has_more = end_index < len(all_active_orders)
        next_cursor = page_orders[-1].get("_cursor_id") if page_orders and has_more else None

        # Clean up cursor_id from response data
        for order in page_orders:
            order.pop("_cursor_id", None)

        return PaginatedResponse(
            data=page_orders,
            pagination={
                "limit": filter_request.limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "total_count": len(all_active_orders),
            },
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching active orders: {str(e)}")


# Historical Order Management - From registry/database
@router.post("/orders/search", response_model=PaginatedResponse)
async def get_orders(filter_request: OrderFilterRequest, accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Get historical order data across all or filtered accounts from the database/registry.

    Args:
        filter_request: JSON payload with filtering criteria

    Returns:
        Paginated response with historical order data and pagination metadata
    """
    try:
        all_orders = []

        # Determine which accounts to query
        if filter_request.account_names:
            accounts_to_check = filter_request.account_names
        else:
            # Get all accounts
            all_connectors = accounts_service.connector_manager.get_all_connectors()
            accounts_to_check = list(all_connectors.keys())

        # Collect orders from all specified accounts
        for account_name in accounts_to_check:
            try:
                orders = await accounts_service.get_orders(
                    account_name=account_name,
                    connector_name=(
                        filter_request.connector_names[0]
                        if filter_request.connector_names and len(filter_request.connector_names) == 1
                        else None
                    ),
                    trading_pair=(
                        filter_request.trading_pairs[0]
                        if filter_request.trading_pairs and len(filter_request.trading_pairs) == 1
                        else None
                    ),
                    status=filter_request.status,
                    start_time=filter_request.start_time,
                    end_time=filter_request.end_time,
                    limit=filter_request.limit * 2,  # Get more for filtering
                    offset=0,
                )
                # Add cursor-friendly identifier to each order
                for order in orders:
                    order["_cursor_id"] = f"{order.get('timestamp', 0)}:{order.get('client_order_id', '')}"
                all_orders.extend(orders)
            except Exception as e:
                # Log error but continue with other accounts
                import logging

                logger.warning(f"Failed to get orders for {account_name}: {e}")

        # Apply filters for multiple values
        if filter_request.connector_names and len(filter_request.connector_names) > 1:
            all_orders = [order for order in all_orders if order.get("connector_name") in filter_request.connector_names]
        if filter_request.trading_pairs and len(filter_request.trading_pairs) > 1:
            all_orders = [order for order in all_orders if order.get("trading_pair") in filter_request.trading_pairs]

        # Sort by timestamp (most recent first) and then by cursor_id for consistency
        all_orders.sort(key=lambda x: (x.get("timestamp", 0), x.get("_cursor_id", "")), reverse=True)

        # Apply cursor-based pagination
        start_index = 0
        if filter_request.cursor:
            # Find the order after the cursor
            for i, order in enumerate(all_orders):
                if order.get("_cursor_id") == filter_request.cursor:
                    start_index = i + 1
                    break

        # Get page of results
        end_index = start_index + filter_request.limit
        page_orders = all_orders[start_index:end_index]

        # Determine next cursor and has_more
        has_more = end_index < len(all_orders)
        next_cursor = page_orders[-1].get("_cursor_id") if page_orders and has_more else None

        # Clean up cursor_id from response data
        for order in page_orders:
            order.pop("_cursor_id", None)

        return PaginatedResponse(
            data=page_orders,
            pagination={
                "limit": filter_request.limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "total_count": len(all_orders),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching orders: {str(e)}")


# Trade History
@router.post("/trades", response_model=PaginatedResponse)
async def get_trades(filter_request: TradeFilterRequest, accounts_service: AccountsService = Depends(get_accounts_service)):
    """
    Get trade history across all or filtered accounts with complex filtering.

    Args:
        filter_request: JSON payload with filtering criteria

    Returns:
        Paginated response with trade data and pagination metadata
    """
    try:
        all_trades = []

        # Determine which accounts to query
        if filter_request.account_names:
            accounts_to_check = filter_request.account_names
        else:
            # Get all accounts
            all_connectors = accounts_service.connector_manager.get_all_connectors()
            accounts_to_check = list(all_connectors.keys())

        # Collect trades from all specified accounts
        for account_name in accounts_to_check:
            try:
                trades = await accounts_service.get_trades(
                    account_name=account_name,
                    connector_name=(
                        filter_request.connector_names[0]
                        if filter_request.connector_names and len(filter_request.connector_names) == 1
                        else None
                    ),
                    trading_pair=(
                        filter_request.trading_pairs[0]
                        if filter_request.trading_pairs and len(filter_request.trading_pairs) == 1
                        else None
                    ),
                    trade_type=(
                        filter_request.trade_types[0]
                        if filter_request.trade_types and len(filter_request.trade_types) == 1
                        else None
                    ),
                    start_time=filter_request.start_time,
                    end_time=filter_request.end_time,
                    limit=filter_request.limit * 2,  # Get more for filtering
                    offset=0,
                )
                # Add cursor-friendly identifier to each trade
                for trade in trades:
                    trade["_cursor_id"] = f"{trade.get('timestamp', 0)}:{trade.get('trade_id', '')}"
                all_trades.extend(trades)
            except Exception as e:
                # Log error but continue with other accounts
                import logging

                logger.warning(f"Failed to get trades for {account_name}: {e}")

        # Apply filters for multiple values
        if filter_request.connector_names and len(filter_request.connector_names) > 1:
            all_trades = [trade for trade in all_trades if trade.get("connector_name") in filter_request.connector_names]
        if filter_request.trading_pairs and len(filter_request.trading_pairs) > 1:
            all_trades = [trade for trade in all_trades if trade.get("trading_pair") in filter_request.trading_pairs]
        if filter_request.trade_types and len(filter_request.trade_types) > 1:
            all_trades = [trade for trade in all_trades if trade.get("trade_type") in filter_request.trade_types]

        # Sort by timestamp (most recent first) and then by cursor_id for consistency
        all_trades.sort(key=lambda x: (x.get("timestamp", 0), x.get("_cursor_id", "")), reverse=True)

        # Apply cursor-based pagination
        start_index = 0
        if filter_request.cursor:
            # Find the trade after the cursor
            for i, trade in enumerate(all_trades):
                if trade.get("_cursor_id") == filter_request.cursor:
                    start_index = i + 1
                    break

        # Get page of results
        end_index = start_index + filter_request.limit
        page_trades = all_trades[start_index:end_index]

        # Determine next cursor and has_more
        has_more = end_index < len(all_trades)
        next_cursor = page_trades[-1].get("_cursor_id") if page_trades and has_more else None

        # Clean up cursor_id from response data
        for trade in page_trades:
            trade.pop("_cursor_id", None)

        return PaginatedResponse(
            data=page_trades,
            pagination={
                "limit": filter_request.limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "total_count": len(all_trades),
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching trades: {str(e)}")


@router.post("/{account_name}/{connector_name}/position-mode")
async def set_position_mode(
    account_name: str,
    connector_name: str,
    request: PositionModeRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Set position mode for a perpetual connector.

    Args:
        account_name: Name of the account
        connector_name: Name of the perpetual connector
        position_mode: Position mode to set (HEDGE or ONEWAY)

    Returns:
        Success message with status

    Raises:
        HTTPException: 400 if not a perpetual connector or invalid position mode
    """
    try:
        # Convert string to PositionMode enum
        mode = PositionMode[request.position_mode.upper()]
        result = await accounts_service.set_position_mode(account_name, connector_name, mode)
        return result
    except KeyError:
        raise HTTPException(
            status_code=400, detail=f"Invalid position mode '{request.position_mode}'. Must be 'HEDGE' or 'ONEWAY'"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{account_name}/{connector_name}/position-mode")
async def get_position_mode(
    account_name: str, connector_name: str, accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get current position mode for a perpetual connector.

    Args:
        account_name: Name of the account
        connector_name: Name of the perpetual connector

    Returns:
        Dictionary with current position mode, connector name, and account name

    Raises:
        HTTPException: 400 if not a perpetual connector
    """
    try:
        result = await accounts_service.get_position_mode(account_name, connector_name)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{account_name}/{connector_name}/leverage")
async def set_leverage(
    account_name: str,
    connector_name: str,
    request: LeverageRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Set leverage for a specific trading pair on a perpetual connector.

    Args:
        account_name: Name of the account
        connector_name: Name of the perpetual connector
        request: Leverage request with trading pair and leverage value
        accounts_service: Injected accounts service

    Returns:
        Dictionary with success status and message

    Raises:
        HTTPException: 400 for invalid parameters or non-perpetual connector, 404 for account/connector not found, 500 for execution errors
    """
    try:
        result = await accounts_service.set_leverage(
            account_name=account_name, connector_name=connector_name, trading_pair=request.trading_pair, leverage=request.leverage
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error setting leverage: {str(e)}")


@router.post("/funding-payments", response_model=PaginatedResponse)
async def get_funding_payments(
    filter_request: FundingPaymentFilterRequest, accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get funding payment history across all or filtered perpetual connectors.

    This endpoint retrieves historical funding payment records including
    funding rates, payment amounts, and position data at time of payment.

    Args:
        filter_request: JSON payload with filtering criteria

    Returns:
        Paginated response with funding payment data and pagination metadata

    Raises:
        HTTPException: 500 if there's an error fetching funding payments
    """
    try:
        all_funding_payments = []
        all_connectors = accounts_service.connector_manager.get_all_connectors()

        # Filter accounts
        accounts_to_check = filter_request.account_names if filter_request.account_names else list(all_connectors.keys())

        for account_name in accounts_to_check:
            if account_name in all_connectors:
                # Filter connectors
                connectors_to_check = (
                    filter_request.connector_names
                    if filter_request.connector_names
                    else list(all_connectors[account_name].keys())
                )

                for connector_name in connectors_to_check:
                    # Only fetch funding payments from perpetual connectors
                    if connector_name in all_connectors[account_name] and "_perpetual" in connector_name:
                        try:
                            payments = await accounts_service.get_funding_payments(
                                account_name=account_name,
                                connector_name=connector_name,
                                trading_pair=filter_request.trading_pair,
                                limit=filter_request.limit * 2,  # Get more for pagination
                            )
                            # Add cursor-friendly identifier to each payment
                            for payment in payments:
                                payment["_cursor_id"] = (
                                    f"{account_name}:{connector_name}:{payment.get('timestamp', '')}:{payment.get('trading_pair', '')}"
                                )
                            all_funding_payments.extend(payments)
                        except Exception as e:
                            # Log error but continue with other connectors
                            import logging

                            logger.warning(f"Failed to get funding payments for {account_name}/{connector_name}: {e}")

        # Sort by timestamp (most recent first) and then by cursor_id for consistency
        all_funding_payments.sort(key=lambda x: (x.get("timestamp", ""), x.get("_cursor_id", "")), reverse=True)

        # Apply cursor-based pagination
        start_index = 0
        if filter_request.cursor:
            # Find the payment after the cursor
            for i, payment in enumerate(all_funding_payments):
                if payment.get("_cursor_id") == filter_request.cursor:
                    start_index = i + 1
                    break

        # Get page of results
        end_index = start_index + filter_request.limit
        page_payments = all_funding_payments[start_index:end_index]

        # Determine next cursor and has_more
        has_more = end_index < len(all_funding_payments)
        next_cursor = page_payments[-1].get("_cursor_id") if page_payments and has_more else None

        # Clean up cursor_id from response data
        for payment in page_payments:
            payment.pop("_cursor_id", None)

        return PaginatedResponse(
            data=page_payments,
            pagination={
                "limit": filter_request.limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
                "total_count": len(all_funding_payments),
            },
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching funding payments: {str(e)}")


def _standardize_in_flight_order_response(order, account_name: str, connector_name: str) -> dict:
    """
    Convert a Hummingbot InFlightOrder to standardized format matching the orders search response.

    Args:
        order: Hummingbot InFlightOrder instance
        account_name: Name of the account
        connector_name: Name of the connector

    Returns:
        Dictionary with standardized order format
    """
    # Map OrderState to status strings
    from hummingbot.core.data_type.in_flight_order import OrderState

    status_mapping = {
        OrderState.PENDING_CREATE: "SUBMITTED",
        OrderState.OPEN: "OPEN",
        OrderState.PENDING_CANCEL: "PENDING_CANCEL",  # Cancellation in progress
        OrderState.CANCELED: "CANCELLED",
        OrderState.PARTIALLY_FILLED: "PARTIALLY_FILLED",
        OrderState.FILLED: "FILLED",
        OrderState.FAILED: "FAILED",
        OrderState.PENDING_APPROVAL: "SUBMITTED",
        OrderState.APPROVED: "SUBMITTED",
        OrderState.CREATED: "SUBMITTED",
        OrderState.COMPLETED: "FILLED",
    }

    # Get status string
    status = status_mapping.get(order.current_state, "SUBMITTED")

    # Convert timestamps to ISO format
    from datetime import datetime, timezone

    created_at = datetime.fromtimestamp(order.creation_timestamp, tz=timezone.utc).isoformat()
    updated_at = datetime.fromtimestamp(
        getattr(order, "last_update_timestamp", order.creation_timestamp), tz=timezone.utc
    ).isoformat()

    return {
        "order_id": order.client_order_id,
        "account_name": account_name,
        "connector_name": connector_name,
        "trading_pair": order.trading_pair,
        "trade_type": order.trade_type.name,
        "order_type": order.order_type.name,
        "amount": float(order.amount) if order.amount and not math.isnan(float(order.amount)) else 0,
        "price": float(order.price) if order.price and not math.isnan(float(order.price)) else None,
        "status": status,
        "filled_amount": float(getattr(order, "executed_amount_base", 0) or 0) if not math.isnan(float(getattr(order, "executed_amount_base", 0) or 0)) else 0,
        "average_fill_price": float(getattr(order, "last_executed_price", 0)) if getattr(order, "last_executed_price", None) and not math.isnan(float(getattr(order, "last_executed_price", 0))) else None,
        "fee_paid": float(getattr(order, "cumulative_fee_paid_quote", 0)) if getattr(order, "cumulative_fee_paid_quote", None) and not math.isnan(float(getattr(order, "cumulative_fee_paid_quote", 0))) else None,
        "fee_currency": None,  # InFlightOrder doesn't store fee currency directly
        "created_at": created_at,
        "updated_at": updated_at,
        "exchange_order_id": order.exchange_order_id,
        "error_message": None,  # InFlightOrder doesn't store error messages
    }
