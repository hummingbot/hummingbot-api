import asyncio
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
from hummingbot.core.event.events import MarketEvent, FundingPaymentCompletedEvent

from database import AsyncDatabaseManager, FundingRepository


class FundingRecorder:
    """
    Records funding payment events and associates them with position data.
    Follows the same pattern as OrdersRecorder for consistency.
    """

    def __init__(self, db_manager: AsyncDatabaseManager, account_name: str, connector_name: str):
        self.db_manager = db_manager
        self.account_name = account_name
        self.connector_name = connector_name
        self._connector: Optional[ConnectorBase] = None
        self.logger = logging.getLogger(__name__)
        
        # Create event forwarder for funding payments
        self._funding_payment_forwarder = SourceInfoEventForwarder(self._did_funding_payment)
        
        # Event pairs mapping events to forwarders
        self._event_pairs = [
            (MarketEvent.FundingPaymentCompleted, self._funding_payment_forwarder),
        ]
    
    def start(self, connector: ConnectorBase):
        """Start recording funding payments for the given connector"""
        # Idempotency guard: prevent double-registration of listeners
        if self._connector is not None:
            self.logger.warning(f"FundingRecorder already started for {self.account_name}/{self.connector_name}, ignoring duplicate start")
            return

        self._connector = connector

        # Subscribe to funding payment events
        for event, forwarder in self._event_pairs:
            connector.add_listener(event, forwarder)
            
        self.logger.info(f"FundingRecorder started for {self.account_name}/{self.connector_name}")
    
    async def stop(self):
        """Stop recording funding payments"""
        if self._connector:
            for event, forwarder in self._event_pairs:
                self._connector.remove_listener(event, forwarder)
            self.logger.info(f"FundingRecorder stopped for {self.account_name}/{self.connector_name}")
    
    def _did_funding_payment(self, event_tag: int, market: ConnectorBase, event: FundingPaymentCompletedEvent):
        """Handle funding payment events - called by SourceInfoEventForwarder"""
        try:
            asyncio.create_task(self._handle_funding_payment(event))
        except Exception as e:
            self.logger.error(f"Error in _did_funding_payment: {e}")
    
    async def _handle_funding_payment(self, event: FundingPaymentCompletedEvent):
        """Handle funding payment events"""
        # Get current position data if available
        position_data = None
        if self._connector and hasattr(self._connector, 'account_positions'):
            try:
                positions = self._connector.account_positions
                if positions:
                    for position in positions.values():
                        if position.trading_pair == event.trading_pair:
                            position_data = {
                                "size": float(position.amount),
                                "side": position.position_side.name if hasattr(position.position_side, 'name') else str(position.position_side),
                            }
                            break
            except Exception as e:
                self.logger.warning(f"Could not get position data for funding payment: {e}")
        
        # Record the funding payment
        await self.record_funding_payment(event, self.account_name, self.connector_name, position_data)

    async def record_funding_payment(self, event: FundingPaymentCompletedEvent, 
                                   account_name: str, connector_name: str, 
                                   position_data: Optional[Dict] = None):
        """
        Record a funding payment event with optional position association.
        
        Args:
            event: FundingPaymentCompletedEvent from Hummingbot
            account_name: Account name
            connector_name: Connector name
            position_data: Optional position data at time of payment
        """
        try:
            # Validate and convert funding data
            funding_rate = Decimal(str(event.funding_rate))
            funding_payment = Decimal(str(event.amount))
            
            # Create funding payment record
            funding_data = {
                "funding_payment_id": f"{connector_name}_{event.trading_pair}_{event.timestamp.timestamp()}",
                "timestamp": event.timestamp,
                "account_name": account_name,
                "connector_name": connector_name,
                "trading_pair": event.trading_pair,
                "funding_rate": float(funding_rate),
                "funding_payment": float(funding_payment),
                "fee_currency": getattr(event, 'fee_currency', 'USDT'),  # Default to USDT if not provided
                "exchange_funding_id": getattr(event, 'exchange_funding_id', None),
            }
            
            # Add position data if provided
            if position_data:
                funding_data.update({
                    "position_size": float(position_data.get("size", 0)),
                    "position_side": position_data.get("side"),
                })
            
            # Save to database
            async with self.db_manager.get_session() as session:
                funding_repo = FundingRepository(session)
                
                # Check if funding payment already exists
                if await funding_repo.funding_payment_exists(funding_data["funding_payment_id"]):
                    self.logger.info(f"Funding payment {funding_data['funding_payment_id']} already exists, skipping")
                    return
                
                funding_payment = await funding_repo.create_funding_payment(funding_data)
                await session.commit()
                
                self.logger.info(
                    f"Recorded funding payment for {account_name}/{connector_name}: "
                    f"{event.trading_pair} - Rate: {funding_rate}, Payment: {funding_payment} "
                    f"{funding_data['fee_currency']}"
                )
                
                return funding_payment
                
        except (ValueError, InvalidOperation) as e:
            self.logger.error(f"Error processing funding payment for {event.trading_pair}: {e}, skipping update")
            return
        except Exception as e:
            self.logger.error(f"Unexpected error recording funding payment: {e}")
            return