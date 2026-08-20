"""Persistence for AMM liquidity writes and Meteora DAMM v2 positions."""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GatewayAMMEvent, GatewayAMMPosition

logger = logging.getLogger(__name__)

# Connectors whose AMM positions are NFTs with their own address. Everything else is
# fungible LP: no position identity, so events only.
NFT_POSITION_CONNECTORS = {"meteora"}


def has_nft_positions(connector: str) -> bool:
    """Whether this AMM connector's positions are individually addressable."""
    return connector.split("/")[0].lower() in NFT_POSITION_CONNECTORS


class GatewayAMMRepository:
    """Read/write access to the AMM event log and DAMM v2 position rows."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------------------------- Positions ----------------------------

    async def get_position_by_address(self, position_address: str) -> Optional[GatewayAMMPosition]:
        result = await self.session.execute(
            select(GatewayAMMPosition).where(GatewayAMMPosition.position_address == position_address)
        )
        return result.scalar_one_or_none()

    async def create_position(self, position_data: Dict) -> GatewayAMMPosition:
        position = GatewayAMMPosition(**position_data)
        self.session.add(position)
        await self.session.flush()
        return position

    async def add_to_position_amounts(
        self,
        position_address: str,
        base_delta: Decimal,
        quote_delta: Decimal,
        entry_price: Optional[Decimal] = None,
    ) -> Optional[GatewayAMMPosition]:
        """Book added liquidity: raise the PnL baseline and the held amounts together.

        Mirrors the CLMM repository, for the same reason — raising one without the other
        makes the row report a gain or loss of exactly the amount deposited. Given a
        price, entry becomes the base-weighted average so capital added later is valued
        at the price it actually entered at.
        """
        position = await self.get_position_by_address(position_address)
        if position:
            old_base = float(position.initial_base_token_amount or 0)
            new_base = old_base + float(base_delta)

            if entry_price is not None and float(base_delta) > 0 and new_base > 0:
                old_entry = float(position.entry_price) if position.entry_price is not None else None
                position.entry_price = (
                    (old_entry * old_base + float(entry_price) * float(base_delta)) / new_base
                    if old_entry is not None else float(entry_price)
                )

            position.initial_base_token_amount = new_base
            position.initial_quote_token_amount = float(position.initial_quote_token_amount or 0) + float(quote_delta)
            position.base_token_amount = float(position.base_token_amount or 0) + float(base_delta)
            position.quote_token_amount = float(position.quote_token_amount or 0) + float(quote_delta)
            await self.session.flush()
        return position

    async def subtract_from_position_amounts(
        self,
        position_address: str,
        base_delta: Decimal,
        quote_delta: Decimal,
    ) -> Optional[GatewayAMMPosition]:
        """Book removed liquidity: lower the baseline and the held amounts together.

        entry_price is untouched — a pro-rata removal changes how much remains, not the
        average price it was entered at. Floors at 0 so a remove larger than the recorded
        holding cannot drive amounts negative.
        """
        position = await self.get_position_by_address(position_address)
        if position:
            position.initial_base_token_amount = max(
                0.0, float(position.initial_base_token_amount or 0) - float(base_delta))
            position.initial_quote_token_amount = max(
                0.0, float(position.initial_quote_token_amount or 0) - float(quote_delta))
            position.base_token_amount = max(0.0, float(position.base_token_amount or 0) - float(base_delta))
            position.quote_token_amount = max(0.0, float(position.quote_token_amount or 0) - float(quote_delta))
            await self.session.flush()
        return position

    async def close_position(
        self,
        position_address: str,
        position_rent_refunded: Optional[Decimal] = None
    ) -> Optional[GatewayAMMPosition]:
        """Mark a position closed, recording the rent the chain gave back.

        A DAMM v2 position closes when its liquidity is fully removed: Gateway closes the
        position account in the same transaction, which is what returns its rent. The
        refund needs recording separately because the rent was never liquidity —
        subtracting the removed amounts does not account for it.
        """
        position = await self.get_position_by_address(position_address)
        if position:
            position.status = "CLOSED"
            position.closed_at = datetime.now(timezone.utc)
            if position_rent_refunded is not None:
                position.position_rent_refunded = position_rent_refunded
            await self.session.flush()
        return position

    async def search_positions(
        self,
        connector: Optional[str] = None,
        network: Optional[str] = None,
        wallet_address: Optional[str] = None,
        pool_address: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[GatewayAMMPosition]:
        query = select(GatewayAMMPosition)
        for column, value in (
            (GatewayAMMPosition.connector, connector),
            (GatewayAMMPosition.network, network),
            (GatewayAMMPosition.wallet_address, wallet_address),
            (GatewayAMMPosition.pool_address, pool_address),
            (GatewayAMMPosition.status, status),
        ):
            if value is not None:
                query = query.where(column == value)
        query = query.order_by(desc(GatewayAMMPosition.created_at)).limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    def position_to_dict(position: GatewayAMMPosition) -> Dict:
        def num(value):
            return float(value) if value is not None else None

        return {
            "position_address": position.position_address,
            "pool_address": position.pool_address,
            "connector": position.connector,
            "network": position.network,
            "wallet_address": position.wallet_address,
            "trading_pair": position.trading_pair,
            "base_token": position.base_token,
            "quote_token": position.quote_token,
            "status": position.status,
            "created_at": position.created_at.isoformat() if position.created_at else None,
            "closed_at": position.closed_at.isoformat() if position.closed_at else None,
            "initial_base_token_amount": num(position.initial_base_token_amount),
            "initial_quote_token_amount": num(position.initial_quote_token_amount),
            "base_token_amount": num(position.base_token_amount),
            "quote_token_amount": num(position.quote_token_amount),
            "lp_token_amount": num(position.lp_token_amount),
            # Rent locked at open and what came back at close, kept apart so the two can
            # be compared: a refund short of what was locked means an account was left
            # behind. Neither is liquidity, so neither is in the amounts above.
            "position_rent": num(position.position_rent),
            "position_rent_refunded": num(position.position_rent_refunded),
            "entry_price": num(position.entry_price),
            "current_price": num(position.current_price),
        }

    # ---------------------------- Events ----------------------------

    async def create_event(self, event_data: Dict) -> GatewayAMMEvent:
        """Record one AMM write."""
        event = GatewayAMMEvent(**event_data)
        self.session.add(event)
        await self.session.flush()
        return event

    async def search_events(
        self,
        connector: Optional[str] = None,
        network: Optional[str] = None,
        wallet_address: Optional[str] = None,
        pool_address: Optional[str] = None,
        event_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[GatewayAMMEvent]:
        """Most recent events first, narrowed by whichever filters are given."""
        query = select(GatewayAMMEvent)
        for column, value in (
            (GatewayAMMEvent.connector, connector),
            (GatewayAMMEvent.network, network),
            (GatewayAMMEvent.wallet_address, wallet_address),
            (GatewayAMMEvent.pool_address, pool_address),
            (GatewayAMMEvent.event_type, event_type),
            (GatewayAMMEvent.status, status),
        ):
            if value is not None:
                query = query.where(column == value)

        query = query.order_by(desc(GatewayAMMEvent.timestamp)).limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    def event_to_dict(event: GatewayAMMEvent) -> Dict:
        return {
            "transaction_hash": event.transaction_hash,
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            "connector": event.connector,
            "network": event.network,
            "wallet_address": event.wallet_address,
            "pool_address": event.pool_address,
            "position_address": event.position_address,
            "event_type": event.event_type,
            "base_token_amount": (float(event.base_token_amount)
                                  if event.base_token_amount is not None else None),
            "quote_token_amount": (float(event.quote_token_amount)
                                   if event.quote_token_amount is not None else None),
            "price": float(event.price) if event.price is not None else None,
            "gas_fee": float(event.gas_fee) if event.gas_fee is not None else None,
            "gas_token": event.gas_token,
            "status": event.status,
            "error_message": event.error_message,
        }
