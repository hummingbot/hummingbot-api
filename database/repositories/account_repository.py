from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
import base64
import json

from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database import AccountState, TokenState


class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _interval_to_minutes(interval: str) -> int:
        """Convert interval string to minutes."""
        interval_map = {
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "4h": 240,
            "12h": 720,
            "1d": 1440
        }
        return interval_map.get(interval, 5)  # Default to 5 minutes

    @staticmethod
    def _sample_history_by_interval(history: List[Dict], interval_minutes: int) -> List[Dict]:
        """
        Sample historical data points based on the specified interval.

        Args:
            history: List of historical data points sorted by timestamp (descending)
            interval_minutes: Sampling interval in minutes

        Returns:
            Sampled list of data points
        """
        if not history or interval_minutes <= 5:
            return history  # Return all data for 5m or less

        sampled = []
        last_sampled_time = None

        for item in history:
            item_time = datetime.fromisoformat(item["timestamp"].replace('Z', '+00:00'))

            if last_sampled_time is None:
                # Always include the first (most recent) data point
                sampled.append(item)
                last_sampled_time = item_time
            else:
                # Check if enough time has passed since last sampled point
                time_diff = (last_sampled_time - item_time).total_seconds() / 60
                if time_diff >= interval_minutes:
                    sampled.append(item)
                    last_sampled_time = item_time

        return sampled

    async def save_account_state(self, account_name: str, connector_name: str, tokens_info: List[Dict], 
                                snapshot_timestamp: Optional[datetime] = None) -> AccountState:
        """
        Save account state with token information to the database.
        If snapshot_timestamp is provided, use it instead of server default.
        """
        account_state_data = {
            "account_name": account_name,
            "connector_name": connector_name
        }
        
        # If a specific timestamp is provided, use it instead of server default
        if snapshot_timestamp:
            account_state_data["timestamp"] = snapshot_timestamp
            
        account_state = AccountState(**account_state_data)
        
        self.session.add(account_state)
        await self.session.flush()  # Get the ID
        
        for token_info in tokens_info:
            token_state = TokenState(
                account_state_id=account_state.id,
                token=token_info["token"],
                units=Decimal(str(token_info["units"])),
                price=Decimal(str(token_info["price"])),
                value=Decimal(str(token_info["value"])),
                available_units=Decimal(str(token_info["available_units"]))
            )
            self.session.add(token_state)
        
        await self.session.commit()
        return account_state

    async def get_latest_account_states(self) -> Dict[str, Dict[str, List[Dict]]]:
        """
        Get the latest account states for all accounts and connectors.
        """
        # Get the latest timestamp for each account-connector combination
        subquery = (
            select(
                AccountState.account_name,
                AccountState.connector_name,
                func.max(AccountState.timestamp).label("max_timestamp")
            )
            .group_by(AccountState.account_name, AccountState.connector_name)
            .subquery()
        )
        
        # Get the full records for the latest timestamps
        query = (
            select(AccountState)
            .options(joinedload(AccountState.token_states))
            .join(
                subquery,
                (AccountState.account_name == subquery.c.account_name) &
                (AccountState.connector_name == subquery.c.connector_name) &
                (AccountState.timestamp == subquery.c.max_timestamp)
            )
        )
        
        result = await self.session.execute(query)
        account_states = result.unique().scalars().all()
        
        # Convert to the expected format
        accounts_state = {}
        for account_state in account_states:
            if account_state.account_name not in accounts_state:
                accounts_state[account_state.account_name] = {}
                
            token_info = []
            for token_state in account_state.token_states:
                token_info.append({
                    "token": token_state.token,
                    "units": float(token_state.units),
                    "price": float(token_state.price),
                    "value": float(token_state.value),
                    "available_units": float(token_state.available_units)
                })
            
            accounts_state[account_state.account_name][account_state.connector_name] = token_info
        
        return accounts_state

    async def get_account_state_history(self,
                                      limit: Optional[int] = None,
                                      account_name: Optional[str] = None,
                                      connector_name: Optional[str] = None,
                                      cursor: Optional[str] = None,
                                      start_time: Optional[datetime] = None,
                                      end_time: Optional[datetime] = None,
                                      interval: str = "5m") -> Tuple[List[Dict], Optional[str], bool]:
        """
        Get historical account states with cursor-based pagination and interval sampling.

        Args:
            limit: Maximum number of records to return
            account_name: Filter by account name
            connector_name: Filter by connector name
            cursor: Cursor for pagination
            start_time: Start time filter
            end_time: End time filter
            interval: Sampling interval (5m, 15m, 30m, 1h, 4h, 12h, 1d)

        Returns:
            Tuple of (data, next_cursor, has_more)
        """
        interval_minutes = self._interval_to_minutes(interval)
        query = (
            select(AccountState)
            .options(joinedload(AccountState.token_states))
            .order_by(desc(AccountState.timestamp))
        )
        
        # Apply filters
        if account_name:
            query = query.filter(AccountState.account_name == account_name)
        if connector_name:
            query = query.filter(AccountState.connector_name == connector_name)
        if start_time:
            query = query.filter(AccountState.timestamp >= start_time)
        if end_time:
            query = query.filter(AccountState.timestamp <= end_time)
            
        # Handle cursor-based pagination
        if cursor:
            try:
                cursor_time = datetime.fromisoformat(cursor.replace('Z', '+00:00'))
                query = query.filter(AccountState.timestamp < cursor_time)
            except (ValueError, TypeError):
                # Invalid cursor, ignore it
                pass

        # Fetch more records than requested to ensure we have enough after sampling
        # For intervals > 5m, we need to fetch more data to get enough sampled points
        sampling_multiplier = max(1, interval_minutes // 5)  # How many 5m intervals per sample
        fetch_limit = (limit * sampling_multiplier + 1) if limit else (100 * sampling_multiplier + 1)
        query = query.limit(fetch_limit)
            
        result = await self.session.execute(query)
        account_states = result.unique().scalars().all()

        # Format response - Group by minute to aggregate account/connector states
        minute_groups = {}
        for account_state in account_states:
            token_info = []
            for token_state in account_state.token_states:
                token_info.append({
                    "token": token_state.token,
                    "units": float(token_state.units),
                    "price": float(token_state.price),
                    "value": float(token_state.value),
                    "available_units": float(token_state.available_units)
                })

            # Round timestamp to the nearest minute for grouping
            minute_timestamp = account_state.timestamp.replace(second=0, microsecond=0)
            minute_key = minute_timestamp.isoformat()

            # Initialize minute group if it doesn't exist
            if minute_key not in minute_groups:
                minute_groups[minute_key] = {
                    "timestamp": minute_key,
                    "state": {}
                }

            # Add account/connector to the minute group
            if account_state.account_name not in minute_groups[minute_key]["state"]:
                minute_groups[minute_key]["state"][account_state.account_name] = {}

            minute_groups[minute_key]["state"][account_state.account_name][account_state.connector_name] = token_info

        # Convert to list and maintain chronological order (most recent first)
        history = list(minute_groups.values())
        history.sort(key=lambda x: x["timestamp"], reverse=True)

        # Apply interval sampling
        sampled_history = self._sample_history_by_interval(history, interval_minutes)

        # Apply limit and check if there are more records after sampling
        has_more = len(sampled_history) > limit if limit else False
        if has_more:
            sampled_history = sampled_history[:limit]

        # Generate next cursor from the last sampled item
        next_cursor = None
        if has_more and sampled_history:
            next_cursor = sampled_history[-1]["timestamp"]

        return sampled_history, next_cursor, has_more
    
    async def get_account_current_state(self, account_name: str) -> Dict[str, List[Dict]]:
        """
        Get the current state for a specific account.
        """
        subquery = (
            select(
                AccountState.connector_name,
                func.max(AccountState.timestamp).label("max_timestamp")
            )
            .filter(AccountState.account_name == account_name)
            .group_by(AccountState.connector_name)
            .subquery()
        )
        
        query = (
            select(AccountState)
            .options(joinedload(AccountState.token_states))
            .join(
                subquery,
                (AccountState.connector_name == subquery.c.connector_name) &
                (AccountState.timestamp == subquery.c.max_timestamp)
            )
            .filter(AccountState.account_name == account_name)
        )
        
        result = await self.session.execute(query)
        account_states = result.unique().scalars().all()
        
        state = {}
        for account_state in account_states:
            token_info = []
            for token_state in account_state.token_states:
                token_info.append({
                    "token": token_state.token,
                    "units": float(token_state.units),
                    "price": float(token_state.price),
                    "value": float(token_state.value),
                    "available_units": float(token_state.available_units)
                })
            state[account_state.connector_name] = token_info
        
        return state
    
    async def get_connector_current_state(self, account_name: str, connector_name: str) -> List[Dict]:
        """
        Get the current state for a specific connector.
        """
        query = (
            select(AccountState)
            .options(joinedload(AccountState.token_states))
            .filter(
                AccountState.account_name == account_name,
                AccountState.connector_name == connector_name
            )
            .order_by(desc(AccountState.timestamp))
            .limit(1)
        )
        
        result = await self.session.execute(query)
        account_state = result.unique().scalar_one_or_none()
        
        if not account_state:
            return []
        
        token_info = []
        for token_state in account_state.token_states:
            token_info.append({
                "token": token_state.token,
                "units": float(token_state.units),
                "price": float(token_state.price),
                "value": float(token_state.value),
                "available_units": float(token_state.available_units)
            })
        
        return token_info
    
    async def get_all_unique_tokens(self) -> List[str]:
        """
        Get all unique tokens across all accounts and connectors.
        """
        query = (
            select(TokenState.token)
            .distinct()
            .order_by(TokenState.token)
        )
        
        result = await self.session.execute(query)
        tokens = result.scalars().all()
        
        return list(tokens)
    
    async def get_token_current_state(self, token: str) -> List[Dict]:
        """
        Get current state of a specific token across all accounts.
        """
        # Get latest timestamps for each account-connector combination
        subquery = (
            select(
                AccountState.id,
                AccountState.account_name,
                AccountState.connector_name,
                func.max(AccountState.timestamp).label("max_timestamp")
            )
            .group_by(AccountState.account_name, AccountState.connector_name, AccountState.id)
            .subquery()
        )
        
        query = (
            select(TokenState, AccountState.account_name, AccountState.connector_name)
            .join(AccountState)
            .join(
                subquery,
                (AccountState.id == subquery.c.id) &
                (AccountState.timestamp == subquery.c.max_timestamp)
            )
            .filter(TokenState.token == token)
        )
        
        result = await self.session.execute(query)
        token_states = result.all()
        
        states = []
        for token_state, account_name, connector_name in token_states:
            states.append({
                "account_name": account_name,
                "connector_name": connector_name,
                "units": float(token_state.units),
                "price": float(token_state.price),
                "value": float(token_state.value),
                "available_units": float(token_state.available_units)
            })
        
        return states
    
    async def get_portfolio_value(self, account_name: Optional[str] = None) -> Dict:
        """
        Get total portfolio value, optionally filtered by account.
        """
        # Get latest timestamps
        subquery = (
            select(
                AccountState.account_name,
                AccountState.connector_name,
                func.max(AccountState.timestamp).label("max_timestamp")
            )
            .group_by(AccountState.account_name, AccountState.connector_name)
        )
        
        if account_name:
            subquery = subquery.filter(AccountState.account_name == account_name)
        
        subquery = subquery.subquery()
        
        # Get token values
        query = (
            select(
                AccountState.account_name,
                func.sum(TokenState.value).label("total_value")
            )
            .join(TokenState)
            .join(
                subquery,
                (AccountState.account_name == subquery.c.account_name) &
                (AccountState.connector_name == subquery.c.connector_name) &
                (AccountState.timestamp == subquery.c.max_timestamp)
            )
            .group_by(AccountState.account_name)
        )
        
        result = await self.session.execute(query)
        values = result.all()
        
        portfolio = {
            "accounts": {},
            "total_value": 0
        }
        
        for account, value in values:
            portfolio["accounts"][account] = float(value or 0)
            portfolio["total_value"] += float(value or 0)
        
        return portfolio