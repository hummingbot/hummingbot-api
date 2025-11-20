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

    async def get_spread_averages(
        self,
        pairs: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        cutoff_timestamp: Optional[int] = None
    ) -> List[Dict]:
        """
        Get average spread statistics grouped by trading pair and connector.
        
        Args:
            pairs: Optional list of trading pairs to filter
            connectors: Optional list of connectors to filter
            cutoff_timestamp: Minimum timestamp (in milliseconds) to include
            
        Returns:
            List of aggregated spread statistics
        """
        # Build query with aggregation
        query = select(
            SpreadSample.pair,
            SpreadSample.connector,
            func.count(SpreadSample.id).label('sample_count'),
            func.avg(SpreadSample.spread).label('avg_spread'),
            func.avg(SpreadSample.bid).label('avg_bid'),
            func.avg(SpreadSample.ask).label('avg_ask'),
            func.avg(SpreadSample.mid).label('avg_mid'),
            func.min(SpreadSample.spread).label('min_spread'),
            func.max(SpreadSample.spread).label('max_spread'),
            func.min(SpreadSample.timestamp).label('first_timestamp'),
            func.max(SpreadSample.timestamp).label('last_timestamp')
        ).where(
            SpreadSample.spread.isnot(None)  # Only samples with spread data
        )
        
        # Apply timestamp filter
        if cutoff_timestamp:
            query = query.where(SpreadSample.timestamp >= cutoff_timestamp)
        
        # Apply pair filter
        if pairs:
            query = query.where(SpreadSample.pair.in_(pairs))
        
        # Apply connector filter
        if connectors:
            query = query.where(SpreadSample.connector.in_(connectors))
        
        # Group by pair and connector
        query = query.group_by(SpreadSample.pair, SpreadSample.connector)
        
        # Order by average spread descending
        query = query.order_by(desc('avg_spread'))
        
        # Execute query
        result = await self.session.execute(query)
        rows = result.all()
        
        # Convert to list of dictionaries
        spread_data = []
        for row in rows:
            spread_data.append({
                "pair": row.pair,
                "connector": row.connector or "unknown",
                "avg_spread": Decimal(f"{row.avg_spread:.6f}") if row.avg_spread else Decimal("0.0"),
                "sample_count": row.sample_count,
                "avg_bid": Decimal(f"{row.avg_bid:.6f}") if row.avg_bid else None,
                "avg_ask": Decimal(f"{row.avg_ask:.6f}") if row.avg_ask else None,
                "avg_mid": Decimal(f"{row.avg_mid:.6f}") if row.avg_mid else None,
                "min_spread": Decimal(f"{row.min_spread:.6f}") if row.min_spread else None,
                "max_spread": Decimal(f"{row.max_spread:.6f}") if row.max_spread else None,
                "first_timestamp": row.first_timestamp,
                "last_timestamp": row.last_timestamp
            })
        
        return spread_data

    async def get_spread_samples(
        self,
        pair: Optional[str] = None,
        connector: Optional[str] = None,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        limit: int = 100,
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
        query = query.limit(limit).offset(offset)
        
        # Execute query
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_spread_count(
        self,
        pair: Optional[str] = None,
        connector: Optional[str] = None,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None
    ) -> int:
        """
        Get total count of spread samples matching filters.
        
        Args:
            pair: Optional trading pair filter
            connector: Optional connector filter
            start_timestamp: Optional start time filter (milliseconds)
            end_timestamp: Optional end time filter (milliseconds)
            
        Returns:
            Total count of matching records
        """
        query = select(func.count(SpreadSample.id))
        
        # Apply filters
        if pair:
            query = query.where(SpreadSample.pair == pair)
        
        if connector:
            query = query.where(SpreadSample.connector == connector)
        
        if start_timestamp:
            query = query.where(SpreadSample.timestamp >= start_timestamp)
        
        if end_timestamp:
            query = query.where(SpreadSample.timestamp <= end_timestamp)
        
        # Execute query
        result = await self.session.execute(query)
        return result.scalar()

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
