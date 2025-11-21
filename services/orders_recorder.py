import asyncio
import logging
import math
import time

from typing import Any, Optional, Union
from datetime import datetime
from decimal import Decimal, InvalidOperation

from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import (
    TradeType,
    BuyOrderCreatedEvent,
    SellOrderCreatedEvent,
    OrderFilledEvent,
    MarketEvent
)
from hummingbot.connector.connector_base import ConnectorBase
from database import AsyncDatabaseManager, OrderRepository, TradeRepository

# Initialize logger
logger = logging.getLogger(__name__)


class OrdersRecorder:
    """
    Custom orders recorder that mimics Hummingbot's MarketsRecorder functionality
    but uses our AsyncDatabaseManager for storage.
    """
    
    def __init__(self, db_manager: AsyncDatabaseManager, account_name: str, connector_name: str):
        self.db_manager = db_manager
        self.account_name = account_name
        self.connector_name = connector_name
        self._connector: Optional[ConnectorBase] = None
        
        # Create event forwarders similar to MarketsRecorder
        self._create_order_forwarder = SourceInfoEventForwarder(self._did_create_order)
        self._fill_order_forwarder = SourceInfoEventForwarder(self._did_fill_order)
        self._cancel_order_forwarder = SourceInfoEventForwarder(self._did_cancel_order)
        self._fail_order_forwarder = SourceInfoEventForwarder(self._did_fail_order)
        self._complete_order_forwarder = SourceInfoEventForwarder(self._did_complete_order)
        
        # Event pairs mapping events to forwarders
        self._event_pairs = [
            (MarketEvent.BuyOrderCreated, self._create_order_forwarder),
            (MarketEvent.SellOrderCreated, self._create_order_forwarder),
            (MarketEvent.OrderFilled, self._fill_order_forwarder),
            (MarketEvent.OrderCancelled, self._cancel_order_forwarder),
            (MarketEvent.OrderFailure, self._fail_order_forwarder),
            (MarketEvent.BuyOrderCompleted, self._complete_order_forwarder),
            (MarketEvent.SellOrderCompleted, self._complete_order_forwarder),
        ]
        
    def start(self, connector: ConnectorBase):
        """Start recording orders for the given connector"""
        self._connector = connector
        
        # Subscribe to order events using the same pattern as MarketsRecorder
        for event, forwarder in self._event_pairs:
            connector.add_listener(event, forwarder)
            logger.info(f"OrdersRecorder: Added listener for {event} with forwarder {forwarder}")
            
            # Debug: Check if listeners were actually added
            if hasattr(connector, '_event_listeners'):
                listeners = connector._event_listeners.get(event, [])
                logger.info(f"OrdersRecorder: Event {event} now has {len(listeners)} listeners")
                for i, listener in enumerate(listeners):
                    logger.info(f"OrdersRecorder: Listener {i}: {listener}")
        
        logger.info(f"OrdersRecorder started for {self.account_name}/{self.connector_name} with {len(self._event_pairs)} event listeners")
        
        # Debug: Print connector info
        logger.info(f"OrdersRecorder: Connector type: {type(connector)}")
        logger.info(f"OrdersRecorder: Connector name: {getattr(connector, 'name', 'unknown')}")
        logger.info(f"OrdersRecorder: Connector ready: {getattr(connector, 'ready', 'unknown')}")
        
        # Test if forwarders are callable
        for event, forwarder in self._event_pairs:
            if callable(forwarder):
                logger.info(f"OrdersRecorder: Forwarder for {event} is callable")
            else:
                logger.error(f"OrdersRecorder: Forwarder for {event} is NOT callable: {type(forwarder)}")
    
    async def stop(self):
        """Stop recording orders"""
        if self._connector:
            # Remove all event listeners
            for event, forwarder in self._event_pairs:
                self._connector.remove_listener(event, forwarder)
            
        logger.info(f"OrdersRecorder stopped for {self.account_name}/{self.connector_name}")
    
    def _extract_error_message(self, event) -> str:
        """Extract error message from various possible event attributes."""
        # Try different possible attribute names for error messages
        for attr_name in ['error_message', 'message', 'reason', 'failure_reason', 'error']:
            if hasattr(event, attr_name):
                error_value = getattr(event, attr_name)
                if error_value:
                    return str(error_value)
        
        # If no error message found, create a descriptive one
        return f"Order failed: {event.__class__.__name__}"
    
    def _did_create_order(self, event_tag: int, market: ConnectorBase, event: Union[BuyOrderCreatedEvent, SellOrderCreatedEvent]):
        """Handle order creation events - called by SourceInfoEventForwarder"""
        logger.info(f"OrdersRecorder: _did_create_order called for order {getattr(event, 'order_id', 'unknown')}")
        try:
            # Determine trade type from event
            trade_type = TradeType.BUY if isinstance(event, BuyOrderCreatedEvent) else TradeType.SELL
            logger.info(f"OrdersRecorder: Creating task to handle order created - {trade_type} order")
            asyncio.create_task(self._handle_order_created(event, trade_type))
        except Exception as e:
            logger.error(f"Error in _did_create_order: {e}")
    
    def _did_fill_order(self, event_tag: int, market: ConnectorBase, event: OrderFilledEvent):
        """Handle order fill events - called by SourceInfoEventForwarder"""
        try:
            asyncio.create_task(self._handle_order_filled(event))
        except Exception as e:
            logger.error(f"Error in _did_fill_order: {e}")
    
    def _did_cancel_order(self, event_tag: int, market: ConnectorBase, event: Any):
        """Handle order cancel events - called by SourceInfoEventForwarder"""
        try:
            asyncio.create_task(self._handle_order_cancelled(event))
        except Exception as e:
            logger.error(f"Error in _did_cancel_order: {e}")
    
    def _did_fail_order(self, event_tag: int, market: ConnectorBase, event: Any):
        """Handle order failure events - called by SourceInfoEventForwarder"""
        try:
            asyncio.create_task(self._handle_order_failed(event))
        except Exception as e:
            logger.error(f"Error in _did_fail_order: {e}")
    
    def _did_complete_order(self, event_tag: int, market: ConnectorBase, event: Any):
        """Handle order completion events - called by SourceInfoEventForwarder"""
        try:
            asyncio.create_task(self._handle_order_completed(event))
        except Exception as e:
            logger.error(f"Error in _did_complete_order: {e}")
    
    async def _handle_order_created(self, event: Union[BuyOrderCreatedEvent, SellOrderCreatedEvent], trade_type: TradeType):
        """Handle order creation events"""
        logger.info(f"OrdersRecorder: _handle_order_created started for order {event.order_id}")
        try:
            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)
                
                # Check if order already exists first
                existing_order = await order_repo.get_order_by_client_id(event.order_id)
                if existing_order:
                    logger.info(f"OrdersRecorder: Order {event.order_id} already exists with status {existing_order.status}")
                    
                    # Update exchange_order_id if we have it now and it was missing
                    exchange_order_id = getattr(event, 'exchange_order_id', None)
                    if exchange_order_id and not existing_order.exchange_order_id:
                        existing_order.exchange_order_id = exchange_order_id
                        logger.info(f"OrdersRecorder: Updated exchange_order_id to {exchange_order_id} for order {event.order_id}")
                    
                    # Update status if it's still in PENDING_CREATE or similar early state
                    if existing_order.status in ["PENDING_CREATE", "PENDING", "SUBMITTED"]:
                        existing_order.status = "OPEN"
                        logger.info(f"OrdersRecorder: Updated status to OPEN for order {event.order_id}")
                    
                    await session.flush()
                    return
                
                order_data = {
                    "client_order_id": event.order_id,
                    "account_name": self.account_name,
                    "connector_name": self.connector_name,
                    "trading_pair": event.trading_pair,
                    "trade_type": trade_type.name,
                    "order_type": event.type.name if hasattr(event, 'type') else 'UNKNOWN',
                    "amount": float(event.amount),
                    "price": float(event.price) if event.price else None,
                    "status": "OPEN",
                    "exchange_order_id": getattr(event, 'exchange_order_id', None)
                }
                await order_repo.create_order(order_data)
                
            logger.info(f"OrdersRecorder: Successfully recorded order created: {event.order_id}")
        except Exception as e:
            logger.error(f"OrdersRecorder: Error recording order created: {e}")
    
    async def _handle_order_filled(self, event: OrderFilledEvent):
        """Handle order fill events"""
        try:
            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)
                trade_repo = TradeRepository(session)

                # Calculate fees
                trade_fee_paid = 0
                trade_fee_currency = None
                
                if event.trade_fee:
                    try:
                        base_asset, quote_asset = event.trading_pair.split("-")
                        fee_in_quote = event.trade_fee.fee_amount_in_token(
                            trading_pair=event.trading_pair,
                            price=event.price,
                            order_amount=event.amount,
                            token=quote_asset,
                            exchange=self._connector,
                        )
                        trade_fee_paid = float(fee_in_quote)
                        trade_fee_currency = quote_asset
                    except Exception as e:
                        logger.error(f"Error calculating trade fee: {e}")
                        trade_fee_paid = 0
                        trade_fee_currency = None
                
                # Update order with fill information (handle potential NaN values like Hummingbot does)
                try:
                    filled_amount = Decimal(str(event.amount))
                    average_fill_price = Decimal(str(event.price))
                    fee_paid_decimal = Decimal(str(trade_fee_paid)) if trade_fee_paid else None
                    
                    order = await order_repo.update_order_fill(
                        client_order_id=event.order_id,
                        filled_amount=filled_amount,
                        average_fill_price=average_fill_price,
                        fee_paid=fee_paid_decimal,
                        fee_currency=trade_fee_currency
                    )
                except (ValueError, InvalidOperation) as e:
                    logger.error(f"Error processing order fill for {event.order_id}: {e}, skipping update")
                    return
                
                # Create trade record using validated values
                if order:
                    try:
                        # Validate all values before creating trade record
                        validated_timestamp = event.timestamp if event.timestamp and not math.isnan(event.timestamp) else time.time()
                        validated_fee = trade_fee_paid if trade_fee_paid and not math.isnan(trade_fee_paid) else 0
                        
                        trade_data = {
                            "order_id": order.id,
                            "trade_id": f"{event.order_id}_{validated_timestamp}",
                            "timestamp": datetime.fromtimestamp(validated_timestamp),
                            "trading_pair": event.trading_pair,
                            "trade_type": event.trade_type.name,
                            "amount": float(filled_amount),  # Use validated amount
                            "price": float(average_fill_price),  # Use validated price
                            "fee_paid": validated_fee,
                            "fee_currency": trade_fee_currency
                        }
                        await trade_repo.create_trade(trade_data)
                    except (ValueError, TypeError) as e:
                        logger.error(f"Error creating trade record for {event.order_id}: {e}")
                        logger.error(f"Trade data that failed: timestamp={event.timestamp}, amount={event.amount}, price={event.price}, fee={trade_fee_paid}")
                
            logger.debug(f"Recorded order fill: {event.order_id} - {event.amount} @ {event.price}")
        except Exception as e:
            logger.error(f"Error recording order fill: {e}")
    
    async def _handle_order_cancelled(self, event: Any):
        """Handle order cancellation events"""
        try:
            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)
                await order_repo.update_order_status(
                    client_order_id=event.order_id,
                    status="CANCELLED"
                )
                    
            logger.debug(f"Recorded order cancelled: {event.order_id}")
        except Exception as e:
            logger.error(f"Error recording order cancellation: {e}")
    
    def _get_order_details_from_connector(self, order_id: str) -> Optional[dict]:
        """Try to get order details from connector's tracked orders"""
        try:
            if self._connector and hasattr(self._connector, 'in_flight_orders'):
                in_flight_order = self._connector.in_flight_orders.get(order_id)
                if in_flight_order:
                    return {
                        "trading_pair": in_flight_order.trading_pair,
                        "trade_type": in_flight_order.trade_type.name,
                        "order_type": in_flight_order.order_type.name,
                        "amount": float(in_flight_order.amount),
                        "price": float(in_flight_order.price) if in_flight_order.price else None
                    }
        except Exception as e:
            logger.error(f"Error getting order details from connector: {e}")
        return None
    
    async def _handle_order_failed(self, event: Any):
        """Handle order failure events"""
        try:
            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)
                
                # Check if order exists, if not try to get details from connector's tracked orders
                existing_order = await order_repo.get_order_by_client_id(event.order_id)
                if existing_order:
                    # Extract error message from various possible attributes
                    error_msg = self._extract_error_message(event)
                    
                    # Update existing order with failure status and error message
                    await order_repo.update_order_status(
                        client_order_id=event.order_id,
                        status="FAILED",
                        error_message=error_msg
                    )
                    logger.info(f"Updated existing order {event.order_id} to FAILED status")
                else:
                    # Try to get order details from connector's tracked orders
                    order_details = self._get_order_details_from_connector(event.order_id)
                    if order_details:
                        logger.info(f"Retrieved order details from connector for {event.order_id}: {order_details}")
                    
                    # Create order record as FAILED with available details
                    if order_details:
                        order_data = {
                            "client_order_id": event.order_id,
                            "account_name": self.account_name,
                            "connector_name": self.connector_name,
                            "trading_pair": order_details["trading_pair"],
                            "trade_type": order_details["trade_type"],
                            "order_type": order_details["order_type"],
                            "amount": order_details["amount"],
                            "price": order_details["price"],
                            "status": "FAILED",
                            "error_message": self._extract_error_message(event)
                        }
                    else:
                        # Fallback with minimal details
                        order_data = {
                            "client_order_id": event.order_id,
                            "account_name": self.account_name,
                            "connector_name": self.connector_name,
                            "trading_pair": "UNKNOWN",
                            "trade_type": "UNKNOWN", 
                            "order_type": "UNKNOWN",
                            "amount": 0.0,
                            "price": None,
                            "status": "FAILED",
                            "error_message": self._extract_error_message(event)
                        }
                    
                    try:
                        await order_repo.create_order(order_data)
                        logger.info(f"Created failed order record for {event.order_id}")
                    except Exception as create_error:
                        # If creation fails due to duplicate key, try to update existing order
                        if "duplicate key" in str(create_error).lower() or "unique constraint" in str(create_error).lower():
                            logger.info(f"Order {event.order_id} already exists, updating status to FAILED")
                            await order_repo.update_order_status(
                                client_order_id=event.order_id,
                                status="FAILED",
                                error_message=self._extract_error_message(event)
                            )
                        else:
                            raise create_error
                    
        except Exception as e:
            logger.error(f"Error recording order failure: {e}")
    
    async def _handle_order_completed(self, event: Any):
        """Handle order completion events"""
        try:
            async with self.db_manager.get_session_context() as session:
                order_repo = OrderRepository(session)
                order = await order_repo.get_order_by_client_id(event.order_id)
                if order:
                    order.status = "FILLED"
                    order.exchange_order_id = getattr(event, 'exchange_order_id', None)
                    
            logger.debug(f"Recorded order completed: {event.order_id}")
        except Exception as e:
            logger.error(f"Error recording order completion: {e}")