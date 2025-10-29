from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GatewayCLMMPosition, GatewayCLMMEvent


class GatewayCLMMRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ============================================
    # Position Management
    # ============================================

    async def create_position(self, position_data: Dict) -> GatewayCLMMPosition:
        """Create a new CLMM position record."""
        position = GatewayCLMMPosition(**position_data)
        self.session.add(position)
        await self.session.flush()
        return position

    async def get_position_by_address(self, position_address: str) -> Optional[GatewayCLMMPosition]:
        """Get a position by its address."""
        result = await self.session.execute(
            select(GatewayCLMMPosition).where(GatewayCLMMPosition.position_address == position_address)
        )
        return result.scalar_one_or_none()

    async def update_position_liquidity(
        self,
        position_address: str,
        base_token_amount: Decimal,
        quote_token_amount: Decimal,
        in_range: Optional[str] = None
    ) -> Optional[GatewayCLMMPosition]:
        """Update position liquidity amounts."""
        result = await self.session.execute(
            select(GatewayCLMMPosition).where(GatewayCLMMPosition.position_address == position_address)
        )
        position = result.scalar_one_or_none()
        if position:
            position.base_token_amount = float(base_token_amount)
            position.quote_token_amount = float(quote_token_amount)
            if in_range is not None:
                position.in_range = in_range
            await self.session.flush()
        return position

    async def update_position_fees(
        self,
        position_address: str,
        base_fee_pending: Optional[Decimal] = None,
        quote_fee_pending: Optional[Decimal] = None,
        base_fee_collected: Optional[Decimal] = None,
        quote_fee_collected: Optional[Decimal] = None
    ) -> Optional[GatewayCLMMPosition]:
        """Update position fee amounts."""
        result = await self.session.execute(
            select(GatewayCLMMPosition).where(GatewayCLMMPosition.position_address == position_address)
        )
        position = result.scalar_one_or_none()
        if position:
            if base_fee_pending is not None:
                position.base_fee_pending = float(base_fee_pending)
            if quote_fee_pending is not None:
                position.quote_fee_pending = float(quote_fee_pending)
            if base_fee_collected is not None:
                position.base_fee_collected = float(base_fee_collected)
            if quote_fee_collected is not None:
                position.quote_fee_collected = float(quote_fee_collected)
            await self.session.flush()
        return position

    async def close_position(self, position_address: str) -> Optional[GatewayCLMMPosition]:
        """Mark position as closed."""
        result = await self.session.execute(
            select(GatewayCLMMPosition).where(GatewayCLMMPosition.position_address == position_address)
        )
        position = result.scalar_one_or_none()
        if position:
            position.status = "CLOSED"
            position.closed_at = datetime.utcnow()
            await self.session.flush()
        return position

    async def get_positions(
        self,
        network: Optional[str] = None,
        connector: Optional[str] = None,
        wallet_address: Optional[str] = None,
        trading_pair: Optional[str] = None,
        status: Optional[str] = None,
        position_addresses: Optional[List[str]] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[GatewayCLMMPosition]:
        """Get positions with filtering and pagination."""
        query = select(GatewayCLMMPosition)

        # Apply filters
        if network:
            query = query.where(GatewayCLMMPosition.network == network)
        if connector:
            query = query.where(GatewayCLMMPosition.connector == connector)
        if wallet_address:
            query = query.where(GatewayCLMMPosition.wallet_address == wallet_address)
        if trading_pair:
            query = query.where(GatewayCLMMPosition.trading_pair == trading_pair)
        if status:
            query = query.where(GatewayCLMMPosition.status == status)
        if position_addresses:
            query = query.where(GatewayCLMMPosition.position_address.in_(position_addresses))

        # Apply ordering and pagination
        query = query.order_by(GatewayCLMMPosition.created_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_open_positions(
        self,
        network: Optional[str] = None,
        wallet_address: Optional[str] = None
    ) -> List[GatewayCLMMPosition]:
        """Get all open positions."""
        return await self.get_positions(
            network=network,
            wallet_address=wallet_address,
            status="OPEN",
            limit=1000
        )

    # ============================================
    # Event Management
    # ============================================

    async def create_event(self, event_data: Dict) -> GatewayCLMMEvent:
        """Create a new CLMM event record."""
        event = GatewayCLMMEvent(**event_data)
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_event_by_tx_hash(
        self,
        transaction_hash: str,
        event_type: Optional[str] = None
    ) -> Optional[GatewayCLMMEvent]:
        """Get an event by transaction hash."""
        query = select(GatewayCLMMEvent).where(GatewayCLMMEvent.transaction_hash == transaction_hash)
        if event_type:
            query = query.where(GatewayCLMMEvent.event_type == event_type)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def update_event_status(
        self,
        transaction_hash: str,
        status: str,
        error_message: Optional[str] = None,
        gas_fee: Optional[Decimal] = None,
        gas_token: Optional[str] = None
    ) -> Optional[GatewayCLMMEvent]:
        """Update event status after transaction confirmation."""
        result = await self.session.execute(
            select(GatewayCLMMEvent).where(GatewayCLMMEvent.transaction_hash == transaction_hash)
        )
        event = result.scalar_one_or_none()
        if event:
            event.status = status
            if error_message:
                event.error_message = error_message
            if gas_fee is not None:
                event.gas_fee = float(gas_fee)
            if gas_token:
                event.gas_token = gas_token
            await self.session.flush()
        return event

    async def get_position_events(
        self,
        position_address: str,
        event_type: Optional[str] = None,
        limit: int = 100
    ) -> List[GatewayCLMMEvent]:
        """Get all events for a position."""
        # First get the position
        position = await self.get_position_by_address(position_address)
        if not position:
            return []

        # Then get its events
        query = select(GatewayCLMMEvent).where(GatewayCLMMEvent.position_id == position.id)

        if event_type:
            query = query.where(GatewayCLMMEvent.event_type == event_type)

        query = query.order_by(GatewayCLMMEvent.timestamp.desc()).limit(limit)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_pending_events(self, limit: int = 100) -> List[GatewayCLMMEvent]:
        """Get events that are still pending confirmation."""
        query = select(GatewayCLMMEvent).where(
            GatewayCLMMEvent.status == "SUBMITTED"
        ).order_by(GatewayCLMMEvent.timestamp.desc()).limit(limit)

        result = await self.session.execute(query)
        return result.scalars().all()

    # ============================================
    # Utilities
    # ============================================

    def position_to_dict(self, position: GatewayCLMMPosition) -> Dict:
        """Convert GatewayCLMMPosition model to dictionary format with PnL calculation."""
        # Calculate PnL if initial amounts are available
        pnl_summary = None
        if position.initial_base_token_amount is not None and position.initial_quote_token_amount is not None:
            # Current total value = current liquidity + fees collected
            current_base_total = float(position.base_token_amount) + float(position.base_fee_collected) + float(position.base_fee_pending)
            current_quote_total = float(position.quote_token_amount) + float(position.quote_fee_collected) + float(position.quote_fee_pending)

            # PnL = current - initial
            base_pnl = current_base_total - float(position.initial_base_token_amount)
            quote_pnl = current_quote_total - float(position.initial_quote_token_amount)

            pnl_summary = {
                "initial_base": float(position.initial_base_token_amount),
                "initial_quote": float(position.initial_quote_token_amount),
                "current_base_total": current_base_total,
                "current_quote_total": current_quote_total,
                "base_pnl": base_pnl,
                "quote_pnl": quote_pnl
            }

        return {
            "position_address": position.position_address,
            "pool_address": position.pool_address,
            "network": position.network,
            "connector": position.connector,
            "wallet_address": position.wallet_address,
            "trading_pair": position.trading_pair,
            "base_token": position.base_token,
            "quote_token": position.quote_token,
            "created_at": position.created_at.isoformat(),
            "closed_at": position.closed_at.isoformat() if position.closed_at else None,
            "status": position.status,
            "lower_price": float(position.lower_price),
            "upper_price": float(position.upper_price),
            "lower_bin_id": position.lower_bin_id,
            "upper_bin_id": position.upper_bin_id,
            "percentage": float(position.percentage) if position.percentage is not None else None,
            "initial_base_token_amount": float(position.initial_base_token_amount) if position.initial_base_token_amount is not None else None,
            "initial_quote_token_amount": float(position.initial_quote_token_amount) if position.initial_quote_token_amount is not None else None,
            "position_rent": float(position.position_rent) if position.position_rent is not None else None,
            "base_token_amount": float(position.base_token_amount),
            "quote_token_amount": float(position.quote_token_amount),
            "in_range": position.in_range,
            "base_fee_collected": float(position.base_fee_collected),
            "quote_fee_collected": float(position.quote_fee_collected),
            "base_fee_pending": float(position.base_fee_pending),
            "quote_fee_pending": float(position.quote_fee_pending),
            "pnl_summary": pnl_summary,
            "last_updated": position.last_updated.isoformat(),
        }

    def event_to_dict(self, event: GatewayCLMMEvent) -> Dict:
        """Convert GatewayCLMMEvent model to dictionary format."""
        return {
            "transaction_hash": event.transaction_hash,
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type,
            "base_token_amount": float(event.base_token_amount) if event.base_token_amount else None,
            "quote_token_amount": float(event.quote_token_amount) if event.quote_token_amount else None,
            "base_fee_collected": float(event.base_fee_collected) if event.base_fee_collected else None,
            "quote_fee_collected": float(event.quote_fee_collected) if event.quote_fee_collected else None,
            "gas_fee": float(event.gas_fee) if event.gas_fee else None,
            "gas_token": event.gas_token,
            "status": event.status,
            "error_message": event.error_message,
        }
