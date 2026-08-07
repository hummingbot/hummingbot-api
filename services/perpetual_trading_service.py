import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import HTTPException
from hummingbot.core.data_type.common import PositionMode

# Create module-specific logger
logger = logging.getLogger(__name__)


class PerpetualTradingService:
    """
    Perpetual-specific trading operations: leverage, position mode and position queries.
    Connector instances are resolved through an injected provider so this service stays
    decoupled from account/credential management.
    """

    def __init__(self, connector_provider: Callable[[str, str], Awaitable[Any]]):
        """
        Initialize the PerpetualTradingService.

        Args:
            connector_provider: Async callable (account_name, connector_name) -> connector instance.
                                Expected to raise HTTPException if the account or connector is not found.
        """
        self._connector_provider = connector_provider

    async def _get_perpetual_connector(self, account_name: str, connector_name: str):
        """
        Get a perpetual connector instance with validation.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)

        Returns:
            Perpetual connector instance

        Raises:
            HTTPException: If connector is not perpetual or not found
        """
        if "_perpetual" not in connector_name:
            raise HTTPException(status_code=400, detail=f"Connector '{connector_name}' is not a perpetual connector")
        return await self._connector_provider(account_name, connector_name)

    async def set_leverage(self, account_name: str, connector_name: str,
                           trading_pair: str, leverage: int) -> Dict[str, str]:
        """
        Set leverage for a specific trading pair on a perpetual connector.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)
            trading_pair: Trading pair to set leverage for
            leverage: Leverage value (typically 1-125)

        Returns:
            Dictionary with success status and message

        Raises:
            HTTPException: If account/connector not found, not perpetual, or operation fails
        """
        connector = await self._get_perpetual_connector(account_name, connector_name)

        if not hasattr(connector, '_execute_set_leverage'):
            raise HTTPException(status_code=400, detail=f"Connector '{connector_name}' does not support leverage setting")

        # Set-leverage endpoints can be pair-scoped (e.g. bybit's
        # v5/position/set-leverage-{PAIR}); register the pair so the throttler
        # learns its rate limit before the request — see issue #207.
        from services.unified_connector_service import UnifiedConnectorService
        await UnifiedConnectorService.sync_pair_derived_state(connector, trading_pair, refresh_rules=False)

        try:
            await connector._execute_set_leverage(trading_pair, leverage)
            message = f"Leverage for {trading_pair} set to {leverage} on {connector_name}"
            logger.info(f"Set leverage for {trading_pair} to {leverage} on {connector_name} (Account: {account_name})")
            return {"status": "success", "message": message}

        except Exception as e:
            logger.error(f"Failed to set leverage for {trading_pair} to {leverage}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to set leverage: {str(e)}")

    async def set_position_mode(self, account_name: str, connector_name: str,
                                position_mode: PositionMode,
                                trading_pair: Optional[str] = None) -> Dict[str, str]:
        """
        Set position mode for a perpetual connector.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)
            position_mode: PositionMode.HEDGE or PositionMode.ONEWAY
            trading_pair: Pair to register before switching. Position-mode
                implementations apply the switch through the connector's trading
                pairs, so at least one registered pair is required.

        Returns:
            Dictionary with success status and message

        Raises:
            HTTPException: If account/connector not found, not perpetual, or operation fails
        """
        connector = await self._get_perpetual_connector(account_name, connector_name)

        # Register the provided pair FIRST. Position-mode implementations apply
        # the switch through the connector's trading pairs (with an empty list the
        # base implementation warns and returns; bybit/bitget overrides vacuously
        # "succeed" without any exchange call), and supported_position_modes() on
        # e.g. bybit depends on the registered pair list — validating against it
        # before registration would vacuously pass modes the actual pair cannot
        # support. Unknown pairs are rejected with 400 and never registered.
        if trading_pair:
            from services.unified_connector_service import UnifiedConnectorService
            try:
                await UnifiedConnectorService.sync_pair_derived_state(
                    connector, trading_pair, refresh_rules=False)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
        if not getattr(connector, "trading_pairs", None):
            raise HTTPException(
                status_code=400,
                detail=f"No trading pairs registered on {connector_name}; pass trading_pair "
                       f"so the position mode switch can be applied on the exchange"
            )

        # Validate AFTER registration, against the real pair set
        supported_modes = connector.supported_position_modes()
        if position_mode not in supported_modes:
            supported_values = [mode.value for mode in supported_modes]
            raise HTTPException(
                status_code=400,
                detail=f"Position mode '{position_mode.value}' not supported. Supported modes: {supported_values}"
            )

        try:
            # Await the actual exchange call. connector.set_position_mode() is
            # fire-and-forget (it spawns _execute_set_position_mode in the
            # background and returns immediately), which would report success
            # before the exchange ever responds — and cannot report a rejection
            # (e.g. Binance -4068 with open positions). _execute_set_position_mode
            # updates the local trait only on confirmed success, so the local mode
            # is the truth test.
            execute = getattr(connector, "_execute_set_position_mode", None)
            if execute is not None:
                await execute(position_mode)
            else:
                result = connector.set_position_mode(position_mode)
                if asyncio.iscoroutine(result):
                    await result

            if getattr(connector, "position_mode", position_mode) != position_mode:
                raise HTTPException(
                    status_code=502,
                    detail=f"Exchange did not accept position mode {position_mode.value} on "
                           f"{connector_name} — check for open positions/orders and connector logs"
                )

            message = f"Position mode set to {position_mode.value} on {connector_name}"
            logger.info(f"Set position mode to {position_mode.value} on {connector_name} (Account: {account_name})")
            return {"status": "success", "message": message}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to set position mode to {position_mode.value}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to set position mode: {str(e)}")

    async def get_position_mode(self, account_name: str, connector_name: str) -> Dict[str, str]:
        """
        Get current position mode for a perpetual connector.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)

        Returns:
            Dictionary with current position mode

        Raises:
            HTTPException: If account/connector not found, not perpetual, or operation fails
        """
        connector = await self._get_perpetual_connector(account_name, connector_name)

        if not hasattr(connector, 'position_mode'):
            raise HTTPException(status_code=400, detail=f"Connector '{connector_name}' does not support position mode")

        try:
            current_mode = connector.position_mode
            return {
                "position_mode": current_mode.value if current_mode else "UNKNOWN",
                "connector": connector_name,
                "account": account_name
            }

        except Exception as e:
            logger.error(f"Failed to get position mode: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get position mode: {str(e)}")

    async def get_account_positions(self, account_name: str, connector_name: str) -> List[Dict]:
        """
        Get current positions for a specific perpetual connector.

        Args:
            account_name: Name of the account
            connector_name: Name of the connector (must be perpetual)

        Returns:
            List of position dictionaries

        Raises:
            HTTPException: If account/connector not found or not perpetual
        """
        connector = await self._get_perpetual_connector(account_name, connector_name)

        if not hasattr(connector, 'account_positions'):
            raise HTTPException(status_code=400, detail=f"Connector '{connector_name}' does not support position tracking")

        try:
            # Force position update to ensure current market prices are used
            await connector._update_positions()

            positions = []
            raw_positions = connector.account_positions

            for trading_pair, position_info in raw_positions.items():
                # Convert position data to dict format
                position_dict = {
                    "account_name": account_name,
                    "connector_name": connector_name,
                    "trading_pair": position_info.trading_pair,
                    "side": position_info.position_side.name if hasattr(position_info, 'position_side') else "UNKNOWN",
                    "amount": float(position_info.amount) if hasattr(position_info, 'amount') else 0.0,
                    "entry_price": float(position_info.entry_price) if hasattr(position_info, 'entry_price') else None,
                    "unrealized_pnl": float(position_info.unrealized_pnl) if hasattr(position_info, 'unrealized_pnl') else None,
                    "leverage": float(position_info.leverage) if hasattr(position_info, 'leverage') else None,
                }

                # Only include positions with non-zero amounts
                if position_dict["amount"] != 0:
                    positions.append(position_dict)

            return positions

        except Exception as e:
            logger.error(f"Failed to get positions for {connector_name}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get positions: {str(e)}")
