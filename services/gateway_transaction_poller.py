"""
Gateway Transaction Poller

This service polls blockchain transactions to confirm Gateway swap and CLMM operations.
Unlike CEX connectors that emit events, DEX transactions require active polling until confirmation.
"""
import asyncio
import logging
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from decimal import Decimal

from database import AsyncDatabaseManager
from database.repositories import GatewaySwapRepository, GatewayCLMMRepository
from services.gateway_client import GatewayClient

logger = logging.getLogger(__name__)


class GatewayTransactionPoller:
    """
    Polls Gateway for transaction status updates and updates database records.

    Unlike CEX connectors that emit events when orders fill, DEX transactions
    need to be polled until they are confirmed on-chain or fail.
    """

    def __init__(
        self,
        db_manager: AsyncDatabaseManager,
        gateway_client: GatewayClient,
        poll_interval: int = 10,  # Poll every 10 seconds
        max_retry_age: int = 3600  # Stop retrying after 1 hour
    ):
        self.db_manager = db_manager
        self.gateway_client = gateway_client
        self.poll_interval = poll_interval
        self.max_retry_age = max_retry_age
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the polling service."""
        if self._running:
            logger.warning("GatewayTransactionPoller already running")
            return

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(f"GatewayTransactionPoller started (poll_interval={self.poll_interval}s)")

    async def stop(self):
        """Stop the polling service."""
        if not self._running:
            return

        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        logger.info("GatewayTransactionPoller stopped")

    async def _poll_loop(self):
        """Main polling loop."""
        while self._running:
            try:
                await self._poll_pending_transactions()
            except Exception as e:
                logger.error(f"Error in poll loop: {e}", exc_info=True)

            # Wait before next poll
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    async def _poll_pending_transactions(self):
        """Poll all pending transactions and update their status."""
        try:
            async with self.db_manager.get_session_context() as session:
                swap_repo = GatewaySwapRepository(session)
                clmm_repo = GatewayCLMMRepository(session)

                # Get pending swaps
                pending_swaps = await swap_repo.get_pending_swaps(limit=100)
                logger.debug(f"Found {len(pending_swaps)} pending swaps")

                for swap in pending_swaps:
                    # Skip if too old (likely failed without proper error)
                    age = (datetime.utcnow() - swap.timestamp).total_seconds()
                    if age > self.max_retry_age:
                        logger.warning(f"Swap {swap.transaction_hash} exceeded max retry age, marking as FAILED")
                        await swap_repo.update_swap_status(
                            transaction_hash=swap.transaction_hash,
                            status="FAILED",
                            error_message="Transaction confirmation timeout"
                        )
                        continue

                    # Poll transaction status
                    await self._poll_swap_transaction(swap, swap_repo)

                # Get pending CLMM events
                pending_events = await clmm_repo.get_pending_events(limit=100)
                logger.debug(f"Found {len(pending_events)} pending CLMM events")

                for event in pending_events:
                    # Skip if too old
                    age = (datetime.utcnow() - event.timestamp).total_seconds()
                    if age > self.max_retry_age:
                        logger.warning(f"CLMM event {event.transaction_hash} exceeded max retry age, marking as FAILED")
                        await clmm_repo.update_event_status(
                            transaction_hash=event.transaction_hash,
                            status="FAILED",
                            error_message="Transaction confirmation timeout"
                        )
                        continue

                    # Poll transaction status
                    await self._poll_clmm_event_transaction(event, clmm_repo)

        except Exception as e:
            logger.error(f"Error polling pending transactions: {e}", exc_info=True)

    async def _poll_swap_transaction(self, swap, swap_repo: GatewaySwapRepository):
        """Poll a specific swap transaction status."""
        try:
            # Parse network into chain and network
            parts = swap.network.split('-', 1)
            if len(parts) != 2:
                logger.error(f"Invalid network format for swap {swap.transaction_hash}: {swap.network}")
                return

            chain, network = parts

            # Check transaction status on Gateway/blockchain
            # Note: This is a placeholder - actual implementation depends on Gateway API
            status_result = await self._check_transaction_status(
                chain=chain,
                network=network,
                tx_hash=swap.transaction_hash
            )

            if status_result:
                if status_result["status"] == "CONFIRMED":
                    logger.info(f"Swap transaction confirmed: {swap.transaction_hash}")
                    await swap_repo.update_swap_status(
                        transaction_hash=swap.transaction_hash,
                        status="CONFIRMED",
                        gas_fee=Decimal(str(status_result.get("gas_fee", 0))) if status_result.get("gas_fee") else None,
                        gas_token=status_result.get("gas_token")
                    )
                elif status_result["status"] == "FAILED":
                    logger.warning(f"Swap transaction failed: {swap.transaction_hash}")
                    await swap_repo.update_swap_status(
                        transaction_hash=swap.transaction_hash,
                        status="FAILED",
                        error_message=status_result.get("error_message", "Transaction failed on-chain")
                    )
                # If status is still pending, do nothing and retry later

        except Exception as e:
            logger.error(f"Error polling swap transaction {swap.transaction_hash}: {e}")

    async def _poll_clmm_event_transaction(self, event, clmm_repo: GatewayCLMMRepository):
        """Poll a specific CLMM event transaction status."""
        try:
            # Get the position to access network info
            position = await clmm_repo.get_position_by_address(
                position_address=(await self.db_manager.get_session_context().__aenter__())
                .query(GatewayCLMMEvent)
                .filter(GatewayCLMMEvent.id == event.id)
                .first()
                .position.position_address
            )

            if not position:
                logger.error(f"Position not found for CLMM event {event.transaction_hash}")
                return

            # Parse network
            parts = position.network.split('-', 1)
            if len(parts) != 2:
                logger.error(f"Invalid network format for CLMM event {event.transaction_hash}: {position.network}")
                return

            chain, network = parts

            # Check transaction status
            status_result = await self._check_transaction_status(
                chain=chain,
                network=network,
                tx_hash=event.transaction_hash
            )

            if status_result:
                if status_result["status"] == "CONFIRMED":
                    logger.info(f"CLMM event transaction confirmed: {event.transaction_hash}")
                    await clmm_repo.update_event_status(
                        transaction_hash=event.transaction_hash,
                        status="CONFIRMED",
                        gas_fee=Decimal(str(status_result.get("gas_fee", 0))) if status_result.get("gas_fee") else None,
                        gas_token=status_result.get("gas_token")
                    )

                    # Update position state based on event type
                    await self._update_position_from_event(event, clmm_repo)

                elif status_result["status"] == "FAILED":
                    logger.warning(f"CLMM event transaction failed: {event.transaction_hash}")
                    await clmm_repo.update_event_status(
                        transaction_hash=event.transaction_hash,
                        status="FAILED",
                        error_message=status_result.get("error_message", "Transaction failed on-chain")
                    )

        except Exception as e:
            logger.error(f"Error polling CLMM event transaction {event.transaction_hash}: {e}")

    async def _update_position_from_event(self, event, clmm_repo: GatewayCLMMRepository):
        """Update CLMM position state based on confirmed event."""
        try:
            # Get position through session
            async with self.db_manager.get_session_context() as session:
                from database.models import GatewayCLMMEvent
                result = await session.execute(
                    session.query(GatewayCLMMEvent).filter(GatewayCLMMEvent.id == event.id)
                )
                event_with_position = result.scalar_one_or_none()

                if not event_with_position or not event_with_position.position:
                    logger.error(f"Position not found for event {event.id}")
                    return

                position = event_with_position.position

                if event.event_type == "CLOSE":
                    await clmm_repo.close_position(position.position_address)

                elif event.event_type == "COLLECT_FEES":
                    # Add collected fees to cumulative total
                    if event.base_fee_collected or event.quote_fee_collected:
                        new_base_collected = float(position.base_fee_collected or 0) + float(event.base_fee_collected or 0)
                        new_quote_collected = float(position.quote_fee_collected or 0) + float(event.quote_fee_collected or 0)

                        await clmm_repo.update_position_fees(
                            position_address=position.position_address,
                            base_fee_collected=Decimal(str(new_base_collected)),
                            quote_fee_collected=Decimal(str(new_quote_collected)),
                            base_fee_pending=Decimal("0"),
                            quote_fee_pending=Decimal("0")
                        )

        except Exception as e:
            logger.error(f"Error updating position from event: {e}", exc_info=True)

    async def _check_transaction_status(
        self,
        chain: str,
        network: str,
        tx_hash: str
    ) -> Optional[Dict]:
        """
        Check transaction status on blockchain via Gateway.

        Returns:
            Dict with status, gas_fee, gas_token, and error_message if available.
            None if transaction not yet confirmed or pending.
        """
        try:
            # Check if Gateway is available
            if not await self.gateway_client.ping():
                logger.warning("Gateway not available for transaction polling")
                return None

            # Poll transaction status from Gateway
            # This would use a Gateway endpoint like GET /chain/transaction/{txHash}
            # For now, we'll implement a basic structure

            # TODO: Implement actual Gateway transaction status polling
            # result = await self.gateway_client._request(
            #     "GET",
            #     f"chain/transaction/{tx_hash}",
            #     params={"chain": chain, "network": network}
            # )

            # Placeholder return - in production this would parse Gateway response
            logger.debug(f"Checking transaction status: {tx_hash} on {chain}-{network}")

            # Return None for now (transaction still pending)
            # Real implementation would return:
            # {
            #     "status": "CONFIRMED" | "FAILED" | "PENDING",
            #     "gas_fee": 0.001,
            #     "gas_token": "SOL",
            #     "error_message": "..." if failed
            # }
            return None

        except Exception as e:
            logger.error(f"Error checking transaction status for {tx_hash}: {e}")
            return None

    async def poll_transaction_once(self, tx_hash: str, network: str) -> Optional[Dict]:
        """
        Poll a specific transaction once (useful for immediate status checks).

        Args:
            tx_hash: Transaction hash
            network: Network in format 'chain-network'

        Returns:
            Transaction status dict or None if pending
        """
        parts = network.split('-', 1)
        if len(parts) != 2:
            logger.error(f"Invalid network format: {network}")
            return None

        chain, network_name = parts
        return await self._check_transaction_status(chain, network_name, tx_hash)
