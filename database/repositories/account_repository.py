from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from database import AccountState, TokenState


class AccountRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _token_state_to_dict(token_state: TokenState) -> Dict:
        """Serialize a TokenState into the standard token info dict with float casts."""
        return {
            "token": token_state.token,
            "units": float(token_state.units),
            "price": float(token_state.price),
            "value": float(token_state.value),
            "available_units": float(token_state.available_units)
        }

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

        Note: this method does NOT commit; it only flushes to obtain the AccountState id.
        The caller's session context owns the transaction and commits once
        (e.g. get_session_context commits on successful exit), so a snapshot spanning
        multiple accounts/connectors persists atomically in a single transaction.
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
                
            token_info = [self._token_state_to_dict(token_state) for token_state in account_state.token_states]

            accounts_state[account_state.account_name][account_state.connector_name] = token_info
        
        return accounts_state

    async def get_account_state_history(self,
                                      limit: Optional[int] = None,
                                      account_name: Optional[str] = None,
                                      account_names: Optional[List[str]] = None,
                                      connector_name: Optional[str] = None,
                                      cursor: Optional[str] = None,
                                      start_time: Optional[datetime] = None,
                                      end_time: Optional[datetime] = None,
                                      interval: str = "5m") -> Tuple[List[Dict], Optional[str], bool]:
        """
        Get historical account states with cursor-based pagination and interval sampling.

        Args:
            limit: Maximum number of records to return
            account_name: Filter by a single account name
            account_names: Filter by multiple account names (IN filter)
            connector_name: Filter by connector name
            cursor: Cursor for pagination
            start_time: Start time filter
            end_time: End time filter
            interval: Sampling interval (5m, 15m, 30m, 1h, 4h, 12h, 1d)

        Returns:
            Tuple of (data, next_cursor, has_more)
        """
        interval_minutes = self._interval_to_minutes(interval)

        # Minute bucket expression: a single logical snapshot fans out into one row per
        # (account_name, connector_name) but all share the same minute. Paginate by these
        # distinct minute buckets so the limit/cursor are independent of the account/connector
        # fan-out (a row-based limit would collapse N*M rows into far fewer buckets than `limit`).
        minute_bucket = func.date_trunc("minute", AccountState.timestamp)

        def _apply_filters(stmt):
            if account_name:
                stmt = stmt.filter(AccountState.account_name == account_name)
            if account_names:
                stmt = stmt.filter(AccountState.account_name.in_(account_names))
            if connector_name:
                stmt = stmt.filter(AccountState.connector_name == connector_name)
            if start_time:
                stmt = stmt.filter(AccountState.timestamp >= start_time)
            if end_time:
                stmt = stmt.filter(AccountState.timestamp <= end_time)
            # Handle cursor-based pagination: the cursor is a minute-bucket timestamp, so
            # everything strictly before it excludes all already-returned buckets.
            if cursor:
                try:
                    cursor_time = datetime.fromisoformat(cursor.replace('Z', '+00:00'))
                    stmt = stmt.filter(AccountState.timestamp < cursor_time)
                except (ValueError, TypeError):
                    # Invalid cursor, ignore it
                    pass
            return stmt

        # Step 1: select the distinct minute buckets that match the filters, most recent first.
        # For intervals > 5m we widen the window so sampling still has enough buckets to pick from.
        sampling_multiplier = max(1, interval_minutes // 5)  # How many 5m intervals per sample
        fetch_limit = (limit * sampling_multiplier + 1) if limit else (100 * sampling_multiplier + 1)
        timestamps_query = (
            select(minute_bucket.label("minute"))
            .distinct()
            .order_by(desc(minute_bucket))
            .limit(fetch_limit)
        )
        timestamps_query = _apply_filters(timestamps_query)
        timestamps_result = await self.session.execute(timestamps_query)
        selected_minutes = [row.minute for row in timestamps_result.all()]

        # Step 2: fetch the AccountState (+token) rows only for the selected minute buckets.
        if selected_minutes:
            query = (
                select(AccountState)
                .options(joinedload(AccountState.token_states))
                .filter(minute_bucket.in_(selected_minutes))
                .order_by(desc(AccountState.timestamp))
            )
            query = _apply_filters(query)
            result = await self.session.execute(query)
            account_states = result.unique().scalars().all()
        else:
            account_states = []

        # Format response - Group by minute to aggregate account/connector states
        minute_groups = {}
        for account_state in account_states:
            token_info = [self._token_state_to_dict(token_state) for token_state in account_state.token_states]

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

        # Already ordered most-recent-first: Step 2 fetched rows ordered by descending
        # timestamp and minute truncation is monotonic, so dict insertion order is descending.
        history = list(minute_groups.values())

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
            token_info = [self._token_state_to_dict(token_state) for token_state in account_state.token_states]
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
        
        token_info = [self._token_state_to_dict(token_state) for token_state in account_state.token_states]

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