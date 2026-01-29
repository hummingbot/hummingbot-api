from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import OrderbookSnapshot


class OrderBookRepository:
    """Repository for order book snapshot data access."""
    
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_spread_samples(
        self,
        pair: Optional[str] = None,
        connector: Optional[str] = None,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[OrderbookSnapshot]:
        """
        Get raw spread samples with filtering and pagination.
        
        Args:
            pair: Optional trading pair filter
            connector: Optional connector filter
            start_timestamp: Optional start time filter (milliseconds)
            end_timestamp: Optional end time filter (milliseconds)
            limit: Maximum number of records to return
            offset: Pagination offset
            
        Returns:
            List of OrderbookSnapshot objects
        """
        query = select(OrderbookSnapshot)
        
        # Apply filters
        if pair:
            query = query.where(OrderbookSnapshot.trading_pair == pair)
        
        if connector:
            query = query.where(OrderbookSnapshot.exchange == connector)
        
        if start_timestamp:
            query = query.where(OrderbookSnapshot.timestamp >= start_timestamp)
        
        if end_timestamp:
            query = query.where(OrderbookSnapshot.timestamp <= end_timestamp)
        
        # Order by timestamp descending (most recent first)
        query = query.order_by(desc(OrderbookSnapshot.timestamp))
        
        # Apply pagination
        if limit is not None:
            query = query.limit(limit).offset(offset)
        
        # Execute query
        result = await self.session.execute(query)
        return result.scalars().all()

    def to_dict(self, sample: OrderbookSnapshot) -> Dict:
        """
        Convert OrderbookSnapshot model to dictionary format.
        
        Args:
            sample: OrderbookSnapshot object
            
        Returns:
            Dictionary representation
        """
        return {
            "id": sample.id,
            "pair": sample.trading_pair,
            "connector": sample.exchange,
            "timestamp": sample.timestamp,
            "bid": float(sample.best_bid) if sample.best_bid else None,
            "ask": float(sample.best_ask) if sample.best_ask else None,
            "mid": float(sample.mid_price) if sample.mid_price else None,
            "spread": float(sample.spread) if sample.spread else None
        }
