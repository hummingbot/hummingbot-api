"""
Gateway Transaction Poller

This service polls blockchain transactions to confirm Gateway swap and CLMM operations.
Unlike CEX connectors that emit events, DEX transactions require active polling until confirmation.

Additionally polls CLMM position state to keep database in sync with on-chain state.
"""
import asyncio
import logging
from typing import Optional, Dict, List
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import AsyncDatabaseManager
from database.repositories import GatewaySwapRepository, GatewayCLMMRepository
from database.models import GatewayCLMMEvent, GatewayCLMMPosition
from services.gateway_client import GatewayClient

logger = logging.getLogger(__name__)


class GatewayTransactionPoller:
    """
    Polls Gateway for transaction status updates and position state.

    - Transaction polling: Confirms pending swap/CLMM transactions
    - Position polling: Updates CLMM position state (in_range, liquidity, fees)

    Unlike CEX connectors that emit events when orders fill, DEX transactions
    need to be polled until they are confirmed on-chain or fail.
    """

    def __init__(
        self,
        db_manager: AsyncDatabaseManager,
        gateway_client: GatewayClient,
        poll_interval: int = 10,  # Poll every 10 seconds for transactions
        position_poll_interval: int = 300,  # Poll every 5 minutes for positions
        max_retry_age: int = 3600  # Stop retrying after 1 hour
    ):
        self.db_manager = db_manager
        self.gateway_client = gateway_client
        self.poll_interval = poll_interval
        self.position_poll_interval = position_poll_interval
        self.max_retry_age = max_retry_age
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None
        self._position_poll_task: Optional[asyncio.Task] = None
        self._last_position_poll: Optional[datetime] = None

    async def start(self):
        """Start the polling service."""
        if self._running:
            logger.warning("GatewayTransactionPoller already running")
            return

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        self._position_poll_task = asyncio.create_task(self._position_poll_loop())
        logger.info(f"GatewayTransactionPoller started (tx_poll={self.poll_interval}s, pos_poll={self.position_poll_interval}s)")

    async def stop(self):
        """Stop the polling service."""
        if not self._running:
            return

        self._running = False

        # Cancel transaction polling task
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        # Cancel position polling task
        if self._position_poll_task:
            self._position_poll_task.cancel()
            try:
                await self._position_poll_task
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
                    age = (datetime.now(timezone.utc) - swap.timestamp).total_seconds()
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
                    age = (datetime.now(timezone.utc) - event.timestamp).total_seconds()
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
            # Get the position by ID from the event's position_id foreign key
            result = await clmm_repo.session.execute(
                select(GatewayCLMMPosition).where(GatewayCLMMPosition.id == event.position_id)
            )
            position = result.scalar_one_or_none()

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
            # Get position by ID using the existing clmm_repo session
            result = await clmm_repo.session.execute(
                select(GatewayCLMMPosition).where(GatewayCLMMPosition.id == event.position_id)
            )
            position = result.scalar_one_or_none()

            if not position:
                logger.error(f"Position not found for event {event.id}")
                return

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

            # Reconstruct network_id from chain and network
            network_id = f"{chain}-{network}"

            # Poll transaction status from Gateway
            result = await self.gateway_client.poll_transaction(
                network_id=network_id,
                tx_hash=tx_hash
            )

            # Check if we got a valid response
            if result is None or not isinstance(result, dict):
                logger.warning(f"Invalid response from Gateway for transaction {tx_hash} on {network_id}: {result}")
                return None

            logger.debug(f"Polled transaction {tx_hash} on {network_id}: txStatus={result.get('txStatus')}")

            # Parse the response with defensive checks
            tx_status = result.get("txStatus")
            tx_data = result.get("txData") or {}
            meta = tx_data.get("meta") if isinstance(tx_data, dict) else {}
            error = meta.get("err") if isinstance(meta, dict) else None

            # Determine gas token based on chain
            gas_token = {
                "solana": "SOL",
                "ethereum": "ETH",
                "arbitrum": "ETH",
                "optimism": "ETH",
                "polygon": "MATIC",
                "avalanche": "AVAX"
            }.get(chain, "UNKNOWN")

            # Transaction is confirmed if txStatus == 1 and no error
            if tx_status == 1 and error is None:
                return {
                    "status": "CONFIRMED",
                    "gas_fee": result.get("fee", 0),
                    "gas_token": gas_token,
                    "error_message": None
                }

            # Transaction failed if there's an error
            if error is not None:
                error_msg = str(error) if error else "Transaction failed on-chain"
                return {
                    "status": "FAILED",
                    "gas_fee": result.get("fee", 0),
                    "gas_token": gas_token,
                    "error_message": error_msg
                }

            # Transaction still pending (txStatus == 0 or not finalized)
            return None

        except Exception as e:
            logger.error(f"Error checking transaction status for {tx_hash}: {e}")
            return None

    async def poll_transaction_once(self, tx_hash: str, network_id: str, wallet_address: Optional[str] = None) -> Optional[Dict]:
        """
        Poll a specific transaction once (useful for immediate status checks).

        Args:
            tx_hash: Transaction hash
            network_id: Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta')
            wallet_address: Optional wallet address for verification

        Returns:
            Transaction status dict or None if pending
        """
        parts = network_id.split('-', 1)
        if len(parts) != 2:
            logger.error(f"Invalid network format: {network_id}")
            return None

        chain, network = parts
        return await self._check_transaction_status(chain, network, tx_hash)

    # ============================================
    # Position State Polling & Discovery
    # ============================================

    # Supported CLMM connectors and their default networks
    SUPPORTED_CLMM_CONFIGS = [
        {"connector": "meteora", "chain": "solana", "network": "mainnet-beta"},
        # Add more connectors as they become supported:
        # {"connector": "raydium", "chain": "solana", "network": "mainnet-beta"},
        # {"connector": "uniswap", "chain": "ethereum", "network": "mainnet"},
    ]

    async def _position_poll_loop(self):
        """Position state polling loop (runs less frequently)."""
        while self._running:
            try:
                # Check if it's time to poll positions
                now = datetime.now(timezone.utc)
                if self._last_position_poll is None or \
                   (now - self._last_position_poll).total_seconds() >= self.position_poll_interval:
                    await self._poll_and_discover_positions()
                    self._last_position_poll = now

                # Sleep for a short time to avoid busy waiting
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in position poll loop: {e}", exc_info=True)
                await asyncio.sleep(10)

    async def _poll_and_discover_positions(self):
        """
        Main position polling method that:
        1. Discovers new positions from Gateway (created via UI or other means)
        2. Updates all open positions with latest state
        """
        try:
            # Check if Gateway is available
            if not await self.gateway_client.ping():
                logger.debug("Gateway not available, skipping position polling")
                return

            # Step 1: Discover new positions from Gateway
            discovered_count = await self._discover_positions_from_gateway()
            if discovered_count > 0:
                logger.info(f"Discovered {discovered_count} new positions from Gateway")

            # Step 2: Update all open positions
            await self._update_all_open_positions()

        except Exception as e:
            logger.error(f"Error in position poll and discovery: {e}", exc_info=True)

    async def _discover_positions_from_gateway(self) -> int:
        """
        Discover positions from Gateway that aren't tracked in the database,
        and reopen positions that were incorrectly marked as closed.

        This allows tracking positions created directly via UI or other means,
        not just those created through the API.

        Also corrects data inconsistencies where a position was marked CLOSED
        in the database but is still OPEN on-chain (e.g., due to a failed close
        transaction).

        Returns:
            Number of newly discovered + reopened positions
        """
        discovered_count = 0
        reopened_count = 0

        try:
            # Get all wallet addresses for supported chains
            wallet_addresses_by_chain = await self.gateway_client.get_all_wallet_addresses()
            if not wallet_addresses_by_chain:
                logger.debug("No wallets configured in Gateway, skipping position discovery")
                return 0

            # Get existing position addresses from database (for quick existence check)
            async with self.db_manager.get_session_context() as session:
                clmm_repo = GatewayCLMMRepository(session)
                # Get OPEN positions (to skip - already tracked correctly)
                open_positions = await clmm_repo.get_position_addresses_set(status="OPEN")
                # Get CLOSED positions (to potentially reopen if still on-chain)
                closed_positions = await clmm_repo.get_position_addresses_set(status="CLOSED")

            # Poll each supported connector/chain/wallet combination
            for config in self.SUPPORTED_CLMM_CONFIGS:
                connector = config["connector"]
                chain = config["chain"]
                network = config["network"]

                # Get wallet addresses for this chain
                wallet_addresses = wallet_addresses_by_chain.get(chain, [])
                if not wallet_addresses:
                    continue

                for wallet_address in wallet_addresses:
                    try:
                        # Fetch ALL positions for this wallet (no pool filter)
                        chain_network = f"{chain}-{network}"
                        gateway_positions = await self.gateway_client.clmm_positions_owned(
                            connector=connector,
                            chain_network=chain_network,
                            wallet_address=wallet_address,
                            pool_address=None  # Get all positions across all pools
                        )

                        if not gateway_positions or not isinstance(gateway_positions, list):
                            continue

                        # Process each position
                        for pos_data in gateway_positions:
                            position_address = pos_data.get("address")
                            if not position_address:
                                continue

                            # Skip if already tracked as OPEN
                            if position_address in open_positions:
                                continue

                            # Check if position was incorrectly marked as CLOSED
                            if position_address in closed_positions:
                                # Position exists on-chain but is CLOSED in DB → reopen it
                                async with self.db_manager.get_session_context() as session:
                                    clmm_repo = GatewayCLMMRepository(session)
                                    reopened = await clmm_repo.reopen_position(position_address)
                                    if reopened:
                                        reopened_count += 1
                                        # Move from closed to open set for this run
                                        closed_positions.discard(position_address)
                                        open_positions.add(position_address)
                                        logger.warning(f"Reopened position {position_address} - "
                                                      f"was CLOSED in DB but still exists on-chain")
                                continue

                            # Create new position in database
                            new_position = await self._create_discovered_position(
                                pos_data=pos_data,
                                connector=connector,
                                chain=chain,
                                network=network,
                                wallet_address=wallet_address
                            )

                            if new_position:
                                discovered_count += 1
                                open_positions.add(position_address)
                                logger.info(f"Discovered new position: {position_address} "
                                           f"(pool: {pos_data.get('poolAddress', 'unknown')[:16]}...)")

                    except Exception as e:
                        logger.warning(f"Error discovering positions for {connector}/{chain}/{wallet_address}: {e}")
                        continue

        except Exception as e:
            logger.error(f"Error in position discovery: {e}", exc_info=True)

        if reopened_count > 0:
            logger.info(f"Position discovery complete: {discovered_count} new, {reopened_count} reopened")

        return discovered_count + reopened_count

    async def _create_discovered_position(
        self,
        pos_data: Dict,
        connector: str,
        chain: str,
        network: str,
        wallet_address: str
    ) -> Optional[GatewayCLMMPosition]:
        """
        Create a database record for a discovered position.

        These positions were created externally (e.g., via UI) and are being
        discovered by the poller.
        """
        try:
            position_address = pos_data.get("address")
            pool_address = pos_data.get("poolAddress", "")

            # Extract token addresses
            base_token_address = pos_data.get("baseTokenAddress", "")
            quote_token_address = pos_data.get("quoteTokenAddress", "")

            # Use full addresses as tokens (consistent with API-created positions)
            base_token = base_token_address if base_token_address else "UNKNOWN"
            quote_token = quote_token_address if quote_token_address else "UNKNOWN"
            trading_pair = f"{base_token}-{quote_token}"

            # Extract price data
            current_price = float(pos_data.get("price", 0))
            lower_price = float(pos_data.get("lowerPrice", 0))
            upper_price = float(pos_data.get("upperPrice", 0))

            # Extract liquidity amounts
            base_token_amount = float(pos_data.get("baseTokenAmount", 0))
            quote_token_amount = float(pos_data.get("quoteTokenAmount", 0))

            # Extract fee data
            base_fee_pending = float(pos_data.get("baseFeeAmount", 0))
            quote_fee_pending = float(pos_data.get("quoteFeeAmount", 0))

            # Extract bin IDs (for Meteora)
            lower_bin_id = pos_data.get("lowerBinId")
            upper_bin_id = pos_data.get("upperBinId")

            # Calculate in_range status
            in_range = "UNKNOWN"
            if current_price > 0 and lower_price > 0 and upper_price > 0:
                if lower_price <= current_price <= upper_price:
                    in_range = "IN_RANGE"
                else:
                    in_range = "OUT_OF_RANGE"

            # Calculate percentage: (upper_price - lower_price) / lower_price
            percentage = None
            if lower_price > 0:
                percentage = (upper_price - lower_price) / lower_price

            # Network in unified format
            network_id = f"{chain}-{network}"

            # Create position in database
            async with self.db_manager.get_session_context() as session:
                clmm_repo = GatewayCLMMRepository(session)

                position_data = {
                    "position_address": position_address,
                    "pool_address": pool_address,
                    "network": network_id,
                    "connector": connector,
                    "wallet_address": wallet_address,
                    "trading_pair": trading_pair,
                    "base_token": base_token,
                    "quote_token": quote_token,
                    "status": "OPEN",
                    "lower_price": lower_price,
                    "upper_price": upper_price,
                    "lower_bin_id": lower_bin_id,
                    "upper_bin_id": upper_bin_id,
                    "entry_price": current_price,  # Best available estimate
                    "current_price": current_price,
                    "percentage": percentage,
                    # For discovered positions, we don't know initial amounts
                    # Use current amounts as initial (best estimate)
                    "initial_base_token_amount": base_token_amount,
                    "initial_quote_token_amount": quote_token_amount,
                    "base_token_amount": base_token_amount,
                    "quote_token_amount": quote_token_amount,
                    "in_range": in_range,
                    "base_fee_pending": base_fee_pending,
                    "quote_fee_pending": quote_fee_pending,
                    "base_fee_collected": 0,
                    "quote_fee_collected": 0,
                }

                position = await clmm_repo.create_position(position_data)

                # Create a DISCOVERED event to mark this position was auto-discovered
                event_data = {
                    "position_id": position.id,
                    "transaction_hash": f"discovered_{position_address[:16]}",  # Synthetic tx hash
                    "event_type": "DISCOVERED",
                    "base_token_amount": base_token_amount,
                    "quote_token_amount": quote_token_amount,
                    "status": "CONFIRMED"  # No actual transaction to confirm
                }
                await clmm_repo.create_event(event_data)

                return position

        except Exception as e:
            logger.error(f"Error creating discovered position {pos_data.get('address')}: {e}", exc_info=True)
            return None

    async def _update_all_open_positions(self):
        """Update state for all open positions from Gateway."""
        try:
            async with self.db_manager.get_session_context() as session:
                clmm_repo = GatewayCLMMRepository(session)

                # Get all open positions
                open_positions = await clmm_repo.get_open_positions()
                if not open_positions:
                    logger.debug("No open CLMM positions to update")
                    return

                logger.info(f"Updating {len(open_positions)} open CLMM positions")

                # Update each position within the same session
                for position in open_positions:
                    try:
                        await self._refresh_position_state(position, clmm_repo)
                    except Exception as e:
                        logger.warning(f"Failed to update position {position.position_address}: {e}")
                        continue

        except Exception as e:
            logger.error(f"Error updating open positions: {e}", exc_info=True)

    # Legacy method name for backwards compatibility
    async def _poll_open_positions(self):
        """Poll all open CLMM positions and update their state. (Legacy wrapper)"""
        await self._poll_and_discover_positions()

    async def _refresh_position_state(self, position: GatewayCLMMPosition, clmm_repo: GatewayCLMMRepository):
        """
        Refresh a single position's state from Gateway.

        Updates:
        - in_range status
        - liquidity amounts
        - pending fees
        - position status (if closed externally)
        """
        try:
            # Validate position has required fields
            if not position.position_address:
                logger.error(f"Position ID {position.id} has no position_address, skipping refresh")
                return
            if not position.wallet_address:
                logger.error(f"Position {position.position_address} has no wallet_address, skipping refresh")
                return
            if not position.connector:
                logger.error(f"Position {position.position_address} has no connector, skipping refresh")
                return
            if not position.network:
                logger.error(f"Position {position.position_address} has no network, skipping refresh")
                return

            # Get individual position info from Gateway (includes pending fees)
            try:
                result = await self.gateway_client.clmm_position_info(
                    connector=position.connector,
                    chain_network=position.network,  # position.network is already in 'chain-network' format
                    position_address=position.position_address
                )

                # Check for Gateway errors
                if result is None:
                    logger.debug(f"Gateway connection error for position {position.position_address}, skipping update")
                    return

                if not isinstance(result, dict):
                    logger.warning(f"Unexpected response type for position {position.position_address}: {type(result)}")
                    return

                # Check if Gateway returned an error response
                if "error" in result:
                    status_code = result.get("status")

                    # Gateway returns 500 instead of 404 when position doesn't exist (closed)
                    # Treat any error (404 or 500) on position-info as "position closed"
                    if status_code in (404, 500):
                        logger.info(f"Position {position.position_address} not found on Gateway (status: {status_code}), marking as CLOSED")
                        await clmm_repo.close_position(position.position_address)
                        return
                    # Other errors → skip update, don't close
                    logger.debug(f"Gateway error for position {position.position_address}: {result.get('error')} (status: {status_code})")
                    return

                # Validate response has required fields
                if "address" not in result:
                    logger.warning(f"Invalid response for position {position.position_address}, missing 'address' field")
                    return

            except Exception as e:
                logger.warning(f"Error fetching position {position.position_address} from Gateway: {e}")
                return

            # Extract current state
            current_price = Decimal(str(result.get("price", 0)))
            lower_price = Decimal(str(result.get("lowerPrice", 0))) if result.get("lowerPrice") else Decimal("0")
            upper_price = Decimal(str(result.get("upperPrice", 0))) if result.get("upperPrice") else Decimal("0")

            # Calculate in_range status
            in_range = "UNKNOWN"
            if current_price > 0 and lower_price > 0 and upper_price > 0:
                if lower_price <= current_price <= upper_price:
                    in_range = "IN_RANGE"
                else:
                    in_range = "OUT_OF_RANGE"

            # Extract token amounts - validate they exist in response
            base_amount_raw = result.get("baseTokenAmount")
            quote_amount_raw = result.get("quoteTokenAmount")

            # If amounts are missing or None, skip update (don't assume zero)
            if base_amount_raw is None or quote_amount_raw is None:
                logger.warning(f"Position {position.position_address} missing token amounts in response, skipping update")
                return

            base_token_amount = Decimal(str(base_amount_raw))
            quote_token_amount = Decimal(str(quote_amount_raw))

            # If Gateway confirms zero liquidity, position was closed externally
            if base_token_amount == 0 and quote_token_amount == 0:
                logger.info(f"Position {position.position_address} has zero liquidity, marking as CLOSED")
                await clmm_repo.close_position(position.position_address)
                return

            # Update liquidity amounts, in_range status, and current price
            await clmm_repo.update_position_liquidity(
                position_address=position.position_address,
                base_token_amount=base_token_amount,
                quote_token_amount=quote_token_amount,
                in_range=in_range,
                current_price=current_price
            )

            # Update pending fees (always update to keep in sync with on-chain state)
            base_fee_pending = Decimal(str(result.get("baseFeeAmount", 0)))
            quote_fee_pending = Decimal(str(result.get("quoteFeeAmount", 0)))

            await clmm_repo.update_position_fees(
                position_address=position.position_address,
                base_fee_pending=base_fee_pending,
                quote_fee_pending=quote_fee_pending
            )

            logger.debug(f"Refreshed position {position.position_address}: price={current_price}, in_range={in_range}, "
                        f"base={base_token_amount}, quote={quote_token_amount}, "
                        f"base_fee={base_fee_pending}, quote_fee={quote_fee_pending}")

        except Exception as e:
            logger.error(f"Error refreshing position state {position.position_address}: {e}", exc_info=True)
