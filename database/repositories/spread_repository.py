from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import SpreadSample


class SpreadRepository:
    """Repository for spread sample data access."""
    
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
    ) -> List[SpreadSample]:
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
            List of SpreadSample objects
        """
        query = select(SpreadSample)
        
        # Apply filters
        if pair:
            query = query.where(SpreadSample.pair == pair)
        
        if connector:
            query = query.where(SpreadSample.connector == connector)
        
        if start_timestamp:
            query = query.where(SpreadSample.timestamp >= start_timestamp)
        
        if end_timestamp:
            query = query.where(SpreadSample.timestamp <= end_timestamp)
        
        # Order by timestamp descending (most recent first)
        query = query.order_by(desc(SpreadSample.timestamp))
        
        # Apply pagination
        if limit is not None:
            query = query.limit(limit).offset(offset)
        
        # Execute query
        result = await self.session.execute(query)
        return result.scalars().all()

    def to_dict(self, sample: SpreadSample) -> Dict:
        """
        Convert SpreadSample model to dictionary format.
        
        Args:
            sample: SpreadSample object
            
        Returns:
            Dictionary representation
        """
        return {
            "id": sample.id,
            "pair": sample.pair,
            "connector": sample.connector,
            "timestamp": sample.timestamp,
            "bid": float(sample.bid) if sample.bid else None,
            "ask": float(sample.ask) if sample.ask else None,
            "mid": float(sample.mid) if sample.mid else None,
            "spread": float(sample.spread) if sample.spread else None,
            "source": sample.source
        }
