from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import FundingPayment


class FundingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_funding_payment(self, funding_data: Dict) -> FundingPayment:
        """Create a new funding payment record."""
        funding = FundingPayment(**funding_data)
        self.session.add(funding)
        await self.session.flush()  # Get the ID
        return funding

    async def get_funding_payments(self, account_name: str, connector_name: str = None, 
                                 trading_pair: str = None, limit: int = 100) -> List[FundingPayment]:
        """Get funding payments with optional filters."""
        query = select(FundingPayment).where(FundingPayment.account_name == account_name)
        
        if connector_name:
            query = query.where(FundingPayment.connector_name == connector_name)
        if trading_pair:
            query = query.where(FundingPayment.trading_pair == trading_pair)
            
        query = query.order_by(FundingPayment.timestamp.desc()).limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_total_funding_fees(self, account_name: str, connector_name: str, 
                                   trading_pair: str) -> Dict:
        """Get total funding fees for a specific trading pair."""
        query = select(FundingPayment).where(
            FundingPayment.account_name == account_name,
            FundingPayment.connector_name == connector_name,
            FundingPayment.trading_pair == trading_pair
        )
        
        result = await self.session.execute(query)
        payments = result.scalars().all()
        
        total_funding = Decimal('0')
        payment_count = 0
        
        for payment in payments:
            total_funding += Decimal(str(payment.funding_payment))
            payment_count += 1
            
        return {
            "total_funding_fees": float(total_funding),
            "payment_count": payment_count,
            "fee_currency": payments[0].fee_currency if payments else None
        }

    async def funding_payment_exists(self, funding_payment_id: str) -> bool:
        """Check if a funding payment already exists."""
        result = await self.session.execute(
            select(FundingPayment).where(FundingPayment.funding_payment_id == funding_payment_id)
        )
        return result.scalar_one_or_none() is not None

    def to_dict(self, funding: FundingPayment) -> Dict:
        """Convert FundingPayment model to dictionary format."""
        return {
            "id": funding.id,
            "funding_payment_id": funding.funding_payment_id,
            "timestamp": funding.timestamp.isoformat(),
            "account_name": funding.account_name,
            "connector_name": funding.connector_name,
            "trading_pair": funding.trading_pair,
            "funding_rate": float(funding.funding_rate),
            "funding_payment": float(funding.funding_payment),
            "fee_currency": funding.fee_currency,
            "position_size": float(funding.position_size) if funding.position_size else None,
            "position_side": funding.position_side,
            "exchange_funding_id": funding.exchange_funding_id,
        }