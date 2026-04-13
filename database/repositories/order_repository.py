from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Order


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_order(self, order_data: Dict) -> Order:
        """Create a new order record."""
        order = Order(**order_data)
        self.session.add(order)
        await self.session.flush()  # Get the ID
        return order

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[Order]:
        """Get an order by its client order ID."""
        result = await self.session.execute(
            select(Order).where(Order.client_order_id == client_order_id)
        )
        return result.scalar_one_or_none()

    async def update_order_status(self, client_order_id: str, status: str, 
                                error_message: Optional[str] = None) -> Optional[Order]:
        """Update order status and optional error message."""
        result = await self.session.execute(
            select(Order).where(Order.client_order_id == client_order_id)
        )
        order = result.scalar_one_or_none()
        if order:
            order.status = status
            if error_message:
                order.error_message = error_message
            await self.session.flush()
        return order

    async def update_order_fill(self, client_order_id: str, filled_amount: Decimal,
                              average_fill_price: Decimal, fee_paid: Decimal = None,
                              fee_currency: str = None, exchange_order_id: str = None) -> Optional[Order]:
        """Update order with fill information."""
        result = await self.session.execute(
            select(Order).where(Order.client_order_id == client_order_id)
        )
        order = result.scalar_one_or_none()
        if order:
            # Add to existing filled amount instead of replacing
            previous_filled = Decimal(str(order.filled_amount or 0))
            order.filled_amount = float(previous_filled + filled_amount)
            
            # Update average price (simplified - use latest fill price)
            order.average_fill_price = float(average_fill_price)
            
            # Add to existing fees
            if fee_paid is not None:
                previous_fee = Decimal(str(order.fee_paid or 0))
                order.fee_paid = float(previous_fee + fee_paid)
            if fee_currency:
                order.fee_currency = fee_currency
            if exchange_order_id:
                order.exchange_order_id = exchange_order_id
            
            # Update status based on total filled amount
            total_filled = Decimal(str(order.filled_amount))
            if total_filled >= Decimal(str(order.amount)):
                order.status = "FILLED"
            elif total_filled > 0:
                order.status = "PARTIALLY_FILLED"
            
            await self.session.flush()
        return order

    async def get_orders(self, account_name: Optional[str] = None, 
                        connector_name: Optional[str] = None,
                        trading_pair: Optional[str] = None, 
                        status: Optional[str] = None,
                        start_time: Optional[int] = None, 
                        end_time: Optional[int] = None,
                        limit: int = 100, offset: int = 0) -> List[Order]:
        """Get orders with filtering and pagination."""
        query = select(Order)
        
        # Apply filters
        if account_name:
            query = query.where(Order.account_name == account_name)
        if connector_name:
            query = query.where(Order.connector_name == connector_name)
        if trading_pair:
            query = query.where(Order.trading_pair == trading_pair)
        if status:
            query = query.where(Order.status == status)
        if start_time:
            start_dt = datetime.fromtimestamp(start_time / 1000)
            query = query.where(Order.created_at >= start_dt)
        if end_time:
            end_dt = datetime.fromtimestamp(end_time / 1000)
            query = query.where(Order.created_at <= end_dt)
        
        # Apply ordering and pagination
        query = query.order_by(Order.created_at.desc())
        query = query.limit(limit).offset(offset)
        
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_active_orders(self, account_name: Optional[str] = None,
                              connector_name: Optional[str] = None,
                              trading_pair: Optional[str] = None) -> List[Order]:
        """Get active orders (SUBMITTED, OPEN, PARTIALLY_FILLED, PENDING_CANCEL)."""
        query = select(Order).where(
            Order.status.in_(["SUBMITTED", "OPEN", "PARTIALLY_FILLED", "PENDING_CANCEL"])
        )
        
        # Apply filters
        if account_name:
            query = query.where(Order.account_name == account_name)
        if connector_name:
            query = query.where(Order.connector_name == connector_name)
        if trading_pair:
            query = query.where(Order.trading_pair == trading_pair)
        
        query = query.order_by(Order.created_at.desc()).limit(1000)
        
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_orders_summary(self, account_name: Optional[str] = None,
                               start_time: Optional[int] = None,
                               end_time: Optional[int] = None) -> Dict:
        """Get order summary statistics."""
        orders = await self.get_orders(
            account_name=account_name,
            start_time=start_time,
            end_time=end_time,
            limit=10000  # Get all for summary
        )
        
        total_orders = len(orders)
        filled_orders = sum(1 for o in orders if o.status == "FILLED")
        cancelled_orders = sum(1 for o in orders if o.status == "CANCELLED")
        failed_orders = sum(1 for o in orders if o.status == "FAILED")
        active_orders = sum(1 for o in orders if o.status in ["SUBMITTED", "OPEN", "PARTIALLY_FILLED"])
        
        return {
            "total_orders": total_orders,
            "filled_orders": filled_orders,
            "cancelled_orders": cancelled_orders,
            "failed_orders": failed_orders,
            "active_orders": active_orders,
            "fill_rate": filled_orders / total_orders if total_orders > 0 else 0,
        }

    def to_dict(self, order: Order) -> Dict:
        """Convert Order model to dictionary format."""
        return {
            "order_id": order.client_order_id,
            "account_name": order.account_name,
            "connector_name": order.connector_name,
            "trading_pair": order.trading_pair,
            "trade_type": order.trade_type,
            "order_type": order.order_type,
            "amount": float(order.amount),
            "price": float(order.price) if order.price else None,
            "status": order.status,
            "filled_amount": float(order.filled_amount),
            "average_fill_price": float(order.average_fill_price) if order.average_fill_price else None,
            "fee_paid": float(order.fee_paid) if order.fee_paid else None,
            "fee_currency": order.fee_currency,
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
            "exchange_order_id": order.exchange_order_id,
            "error_message": order.error_message,
        }