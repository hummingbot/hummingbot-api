from datetime import datetime
from typing import Dict, List, Optional
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GatewaySwap


class GatewaySwapRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_swap(self, swap_data: Dict) -> GatewaySwap:
        """Create a new swap record."""
        swap = GatewaySwap(**swap_data)
        self.session.add(swap)
        await self.session.flush()
        return swap

    async def get_swap_by_tx_hash(self, transaction_hash: str) -> Optional[GatewaySwap]:
        """Get a swap by its transaction hash."""
        result = await self.session.execute(
            select(GatewaySwap).where(GatewaySwap.transaction_hash == transaction_hash)
        )
        return result.scalar_one_or_none()

    async def update_swap_status(
        self,
        transaction_hash: str,
        status: str,
        error_message: Optional[str] = None,
        gas_fee: Optional[Decimal] = None,
        gas_token: Optional[str] = None
    ) -> Optional[GatewaySwap]:
        """Update swap status and optional metadata after transaction confirmation."""
        result = await self.session.execute(
            select(GatewaySwap).where(GatewaySwap.transaction_hash == transaction_hash)
        )
        swap = result.scalar_one_or_none()
        if swap:
            swap.status = status
            if error_message:
                swap.error_message = error_message
            if gas_fee is not None:
                swap.gas_fee = float(gas_fee)
            if gas_token:
                swap.gas_token = gas_token
            await self.session.flush()
        return swap

    async def get_swaps(
        self,
        network: Optional[str] = None,
        connector: Optional[str] = None,
        wallet_address: Optional[str] = None,
        trading_pair: Optional[str] = None,
        status: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[GatewaySwap]:
        """Get swaps with filtering and pagination."""
        query = select(GatewaySwap)

        # Apply filters
        if network:
            query = query.where(GatewaySwap.network == network)
        if connector:
            query = query.where(GatewaySwap.connector == connector)
        if wallet_address:
            query = query.where(GatewaySwap.wallet_address == wallet_address)
        if trading_pair:
            query = query.where(GatewaySwap.trading_pair == trading_pair)
        if status:
            query = query.where(GatewaySwap.status == status)
        if start_time:
            start_dt = datetime.fromtimestamp(start_time)
            query = query.where(GatewaySwap.timestamp >= start_dt)
        if end_time:
            end_dt = datetime.fromtimestamp(end_time)
            query = query.where(GatewaySwap.timestamp <= end_dt)

        # Apply ordering and pagination
        query = query.order_by(GatewaySwap.timestamp.desc())
        query = query.limit(limit).offset(offset)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_pending_swaps(self, limit: int = 100) -> List[GatewaySwap]:
        """Get swaps that are still pending confirmation."""
        query = select(GatewaySwap).where(
            GatewaySwap.status == "SUBMITTED"
        ).order_by(GatewaySwap.timestamp.desc()).limit(limit)

        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_swaps_summary(
        self,
        network: Optional[str] = None,
        wallet_address: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> Dict:
        """Get swap summary statistics."""
        swaps = await self.get_swaps(
            network=network,
            wallet_address=wallet_address,
            start_time=start_time,
            end_time=end_time,
            limit=10000  # Get all for summary
        )

        total_swaps = len(swaps)
        confirmed_swaps = sum(1 for s in swaps if s.status == "CONFIRMED")
        failed_swaps = sum(1 for s in swaps if s.status == "FAILED")
        pending_swaps = sum(1 for s in swaps if s.status == "SUBMITTED")

        # Calculate total volume (in quote token)
        total_volume = sum(
            float(s.output_amount if s.side == "BUY" else s.input_amount)
            for s in swaps if s.status == "CONFIRMED"
        )

        # Calculate total gas fees
        total_gas_fees = sum(
            float(s.gas_fee) for s in swaps
            if s.gas_fee is not None and s.status == "CONFIRMED"
        )

        return {
            "total_swaps": total_swaps,
            "confirmed_swaps": confirmed_swaps,
            "failed_swaps": failed_swaps,
            "pending_swaps": pending_swaps,
            "success_rate": confirmed_swaps / total_swaps if total_swaps > 0 else 0,
            "total_volume": total_volume,
            "total_gas_fees": total_gas_fees,
        }

    def to_dict(self, swap: GatewaySwap) -> Dict:
        """Convert GatewaySwap model to dictionary format."""
        return {
            "transaction_hash": swap.transaction_hash,
            "timestamp": swap.timestamp.isoformat(),
            "network": swap.network,
            "connector": swap.connector,
            "wallet_address": swap.wallet_address,
            "trading_pair": swap.trading_pair,
            "base_token": swap.base_token,
            "quote_token": swap.quote_token,
            "side": swap.side,
            "input_amount": float(swap.input_amount),
            "output_amount": float(swap.output_amount),
            "price": float(swap.price),
            "slippage_pct": float(swap.slippage_pct) if swap.slippage_pct else None,
            "gas_fee": float(swap.gas_fee) if swap.gas_fee else None,
            "gas_token": swap.gas_token,
            "status": swap.status,
            "pool_address": swap.pool_address,
            "quote_id": swap.quote_id,
            "error_message": swap.error_message,
        }
