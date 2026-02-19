"""
Repository for executor database operations.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ExecutorOrder, ExecutorRecord


class ExecutorRepository:
    """Repository for ExecutorRecord and ExecutorOrder database operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ========================================
    # ExecutorRecord Operations
    # ========================================

    async def create_executor(
        self,
        executor_id: str,
        executor_type: str,
        account_name: str,
        connector_name: str,
        trading_pair: str,
        config: Optional[str] = None,
        status: str = "RUNNING"
    ) -> ExecutorRecord:
        """Create a new executor record."""
        executor = ExecutorRecord(
            executor_id=executor_id,
            executor_type=executor_type,
            account_name=account_name,
            connector_name=connector_name,
            trading_pair=trading_pair,
            config=config,
            status=status
        )

        self.session.add(executor)
        await self.session.flush()
        await self.session.refresh(executor)
        return executor

    async def update_executor(
        self,
        executor_id: str,
        status: Optional[str] = None,
        close_type: Optional[str] = None,
        net_pnl_quote: Optional[Decimal] = None,
        net_pnl_pct: Optional[Decimal] = None,
        cum_fees_quote: Optional[Decimal] = None,
        filled_amount_quote: Optional[Decimal] = None,
        final_state: Optional[str] = None
    ) -> Optional[ExecutorRecord]:
        """Update an executor record."""
        stmt = select(ExecutorRecord).where(ExecutorRecord.executor_id == executor_id)
        result = await self.session.execute(stmt)
        executor = result.scalar_one_or_none()

        if executor:
            if status is not None:
                executor.status = status
            if close_type is not None:
                executor.close_type = close_type
                executor.closed_at = datetime.now(timezone.utc)
            if net_pnl_quote is not None:
                executor.net_pnl_quote = net_pnl_quote
            if net_pnl_pct is not None:
                executor.net_pnl_pct = net_pnl_pct
            if cum_fees_quote is not None:
                executor.cum_fees_quote = cum_fees_quote
            if filled_amount_quote is not None:
                executor.filled_amount_quote = filled_amount_quote
            if final_state is not None:
                executor.final_state = final_state

            await self.session.flush()
            await self.session.refresh(executor)

        return executor

    async def get_executor_by_id(self, executor_id: str) -> Optional[ExecutorRecord]:
        """Get an executor by ID."""
        stmt = select(ExecutorRecord).where(ExecutorRecord.executor_id == executor_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_executors(
        self,
        account_name: Optional[str] = None,
        connector_name: Optional[str] = None,
        trading_pair: Optional[str] = None,
        executor_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[ExecutorRecord]:
        """Get executors with optional filters."""
        stmt = select(ExecutorRecord)

        conditions = []
        if account_name:
            conditions.append(ExecutorRecord.account_name == account_name)
        if connector_name:
            conditions.append(ExecutorRecord.connector_name == connector_name)
        if trading_pair:
            conditions.append(ExecutorRecord.trading_pair == trading_pair)
        if executor_type:
            conditions.append(ExecutorRecord.executor_type == executor_type)
        if status:
            conditions.append(ExecutorRecord.status == status)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = stmt.order_by(desc(ExecutorRecord.created_at)).limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_executors(
        self,
        account_name: Optional[str] = None,
        connector_name: Optional[str] = None
    ) -> List[ExecutorRecord]:
        """Get all active (running) executors."""
        stmt = select(ExecutorRecord).where(ExecutorRecord.status == "RUNNING")

        if account_name:
            stmt = stmt.where(ExecutorRecord.account_name == account_name)
        if connector_name:
            stmt = stmt.where(ExecutorRecord.connector_name == connector_name)

        stmt = stmt.order_by(desc(ExecutorRecord.created_at))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_position_hold_executors(
        self,
        account_name: Optional[str] = None,
        connector_name: Optional[str] = None,
        trading_pair: Optional[str] = None
    ) -> List[ExecutorRecord]:
        """Get executors that closed with POSITION_HOLD (keep_position=True)."""
        stmt = select(ExecutorRecord).where(ExecutorRecord.close_type == "POSITION_HOLD")

        conditions = []
        if account_name:
            conditions.append(ExecutorRecord.account_name == account_name)
        if connector_name:
            conditions.append(ExecutorRecord.connector_name == connector_name)
        if trading_pair:
            conditions.append(ExecutorRecord.trading_pair == trading_pair)

        if conditions:
            stmt = stmt.where(and_(*conditions))

        stmt = stmt.order_by(desc(ExecutorRecord.created_at))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_executor_stats(self) -> Dict[str, Any]:
        """Get statistics about executors."""
        # Total executors
        total_stmt = select(func.count(ExecutorRecord.id))
        total_result = await self.session.execute(total_stmt)
        total_executors = total_result.scalar() or 0

        # Active executors
        active_stmt = select(func.count(ExecutorRecord.id)).where(
            ExecutorRecord.status == "RUNNING"
        )
        active_result = await self.session.execute(active_stmt)
        active_executors = active_result.scalar() or 0

        # Total PnL
        pnl_stmt = select(func.sum(ExecutorRecord.net_pnl_quote))
        pnl_result = await self.session.execute(pnl_stmt)
        total_pnl = pnl_result.scalar() or Decimal("0")

        # Total volume
        volume_stmt = select(func.sum(ExecutorRecord.filled_amount_quote))
        volume_result = await self.session.execute(volume_stmt)
        total_volume = volume_result.scalar() or Decimal("0")

        # Executors by type
        type_stmt = select(
            ExecutorRecord.executor_type,
            func.count(ExecutorRecord.id).label('count')
        ).group_by(ExecutorRecord.executor_type)
        type_result = await self.session.execute(type_stmt)
        type_counts = {row.executor_type: row.count for row in type_result}

        # Executors by status
        status_stmt = select(
            ExecutorRecord.status,
            func.count(ExecutorRecord.id).label('count')
        ).group_by(ExecutorRecord.status)
        status_result = await self.session.execute(status_stmt)
        status_counts = {row.status: row.count for row in status_result}

        # Executors by connector
        connector_stmt = select(
            ExecutorRecord.connector_name,
            func.count(ExecutorRecord.id).label('count')
        ).group_by(ExecutorRecord.connector_name)
        connector_result = await self.session.execute(connector_stmt)
        connector_counts = {row.connector_name: row.count for row in connector_result}

        return {
            "total_executors": total_executors,
            "active_executors": active_executors,
            "total_pnl_quote": float(total_pnl),
            "total_volume_quote": float(total_volume),
            "type_counts": type_counts,
            "status_counts": status_counts,
            "connector_counts": connector_counts
        }

    # ========================================
    # ExecutorOrder Operations
    # ========================================

    async def create_executor_order(
        self,
        executor_id: str,
        client_order_id: str,
        order_type: str,
        trade_type: str,
        amount: Decimal,
        price: Optional[Decimal] = None,
        exchange_order_id: Optional[str] = None,
        status: str = "SUBMITTED"
    ) -> ExecutorOrder:
        """Create a new executor order record."""
        order = ExecutorOrder(
            executor_id=executor_id,
            client_order_id=client_order_id,
            order_type=order_type,
            trade_type=trade_type,
            amount=amount,
            price=price,
            exchange_order_id=exchange_order_id,
            status=status
        )

        self.session.add(order)
        await self.session.flush()
        await self.session.refresh(order)
        return order

    async def update_executor_order(
        self,
        client_order_id: str,
        status: Optional[str] = None,
        filled_amount: Optional[Decimal] = None,
        average_fill_price: Optional[Decimal] = None,
        exchange_order_id: Optional[str] = None
    ) -> Optional[ExecutorOrder]:
        """Update an executor order record."""
        stmt = select(ExecutorOrder).where(ExecutorOrder.client_order_id == client_order_id)
        result = await self.session.execute(stmt)
        order = result.scalar_one_or_none()

        if order:
            if status is not None:
                order.status = status
            if filled_amount is not None:
                order.filled_amount = filled_amount
            if average_fill_price is not None:
                order.average_fill_price = average_fill_price
            if exchange_order_id is not None:
                order.exchange_order_id = exchange_order_id

            await self.session.flush()
            await self.session.refresh(order)

        return order

    async def get_executor_orders(
        self,
        executor_id: str,
        status: Optional[str] = None
    ) -> List[ExecutorOrder]:
        """Get orders for an executor."""
        stmt = select(ExecutorOrder).where(ExecutorOrder.executor_id == executor_id)

        if status:
            stmt = stmt.where(ExecutorOrder.status == status)

        stmt = stmt.order_by(desc(ExecutorOrder.created_at))

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[ExecutorOrder]:
        """Get an order by client order ID."""
        stmt = select(ExecutorOrder).where(ExecutorOrder.client_order_id == client_order_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def cleanup_orphaned_executors(
        self,
        active_executor_ids: List[str],
        close_type: str = "SYSTEM_CLEANUP"
    ) -> int:
        """
        Clean up orphaned executors - those marked as RUNNING but not in active memory.
        
        Args:
            active_executor_ids: List of executor IDs currently active in memory
            close_type: Close type to set for cleaned up executors
            
        Returns:
            Number of executors cleaned up
        """
        from sqlalchemy import update
        
        # Find executors that are RUNNING but not in the active list
        conditions = [ExecutorRecord.status == "RUNNING"]
        
        if active_executor_ids:
            conditions.append(~ExecutorRecord.executor_id.in_(active_executor_ids))
        
        # First, get the count of orphaned executors for logging
        count_stmt = select(func.count(ExecutorRecord.id)).where(and_(*conditions))
        count_result = await self.session.execute(count_stmt)
        orphaned_count = count_result.scalar() or 0
        
        if orphaned_count > 0:
            # Update orphaned executors to TERMINATED status
            update_stmt = (
                update(ExecutorRecord)
                .where(and_(*conditions))
                .values(
                    status="TERMINATED",
                    close_type=close_type,
                    closed_at=datetime.now(timezone.utc)
                )
            )
            
            await self.session.execute(update_stmt)
            await self.session.flush()
        
        return orphaned_count
