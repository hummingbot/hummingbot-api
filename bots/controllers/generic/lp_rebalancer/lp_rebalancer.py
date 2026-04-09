import logging
from decimal import Decimal
from typing import List, Optional

from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.gateway_utils import parse_provider
from hummingbot.strategy_v2.executors.lp_executor.data_types import LPExecutorConfig, LPExecutorStates
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo
from pydantic import Field, field_validator, model_validator


class LPRebalancerConfig(ControllerConfigBase):
    """
    Configuration for LP Rebalancer Controller.

    Uses total_amount_quote and side for position sizing.
    Implements KEEP vs REBALANCE logic based on price limits.

    Provider Architecture:
    - connector_name: The network identifier (e.g., "solana-mainnet-beta")
    - lp_provider: LP provider in format "dex/trading_type" (e.g., "meteora/clmm")
    - autoswap: Uses OrderExecutor with network's configured swapProvider
    """
    controller_type: str = "generic"
    controller_name: str = "lp_rebalancer"
    candles_config: List[CandlesConfig] = []

    # Network connector - e.g., "solana-mainnet-beta"
    connector_name: str = "solana-mainnet-beta"

    # LP provider (required) - format: "dex/trading_type"
    # Examples: "meteora/clmm", "orca/clmm", "raydium/clmm"
    lp_provider: str = "orca/clmm"

    # Pool configuration (required)
    trading_pair: str
    pool_address: str

    # Position parameters
    total_amount_quote: Decimal = Field(default=Decimal("50"), json_schema_extra={"is_updatable": True})
    side: TradeType = Field(default=TradeType.BUY, json_schema_extra={"is_updatable": True})  # BUY, SELL, or RANGE
    position_width_pct: Decimal = Field(default=Decimal("0.5"), json_schema_extra={"is_updatable": True})
    position_offset_pct: Decimal = Field(
        default=Decimal("0.01"),
        json_schema_extra={"is_updatable": True},
        description="Offset from current price. Positive = out-of-range (single-sided). "
                    "Negative = in-range (needs both tokens, autoswap will convert |offset|%)"
    )

    # Rebalancing
    rebalance_seconds: int = Field(default=60, json_schema_extra={"is_updatable": True})
    rebalance_threshold_pct: Decimal = Field(
        default=Decimal("0.1"),
        json_schema_extra={"is_updatable": True},
        description="Price must be this % out of range before rebalance timer starts (e.g., 0.1 = 0.1%, 2 = 2%)"
    )

    # Price limits - overlapping grids for sell and buy ranges
    # Sell range: [sell_price_min, sell_price_max]
    # Buy range: [buy_price_min, buy_price_max]
    sell_price_max: Optional[Decimal] = Field(default=None, json_schema_extra={"is_updatable": True})
    sell_price_min: Optional[Decimal] = Field(default=None, json_schema_extra={"is_updatable": True})
    buy_price_max: Optional[Decimal] = Field(default=None, json_schema_extra={"is_updatable": True})
    buy_price_min: Optional[Decimal] = Field(default=None, json_schema_extra={"is_updatable": True})

    # Connector-specific params (optional)
    strategy_type: Optional[int] = Field(default=None, json_schema_extra={"is_updatable": True})

    # Auto-swap feature: swap tokens if balance insufficient for position
    autoswap: bool = Field(
        default=False,
        json_schema_extra={"is_updatable": True},
        description="Automatically swap tokens if balance is insufficient for position."
    )
    swap_buffer_pct: Decimal = Field(
        default=Decimal("0.01"),
        json_schema_extra={"is_updatable": True},
        description="Extra % to swap beyond deficit to account for slippage (e.g., 0.01 = 0.01%)"
    )

    @field_validator("sell_price_min", "sell_price_max", "buy_price_min", "buy_price_max", mode="before")
    @classmethod
    def validate_price_limits(cls, v):
        """Allow null/None values for price limits."""
        if v is None:
            return None
        return Decimal(str(v))

    @field_validator("side", mode="before")
    @classmethod
    def validate_side(cls, v):
        """Validate and convert side to TradeType enum."""
        if isinstance(v, TradeType):
            return v
        if isinstance(v, str):
            v = v.upper()
            if v in ("BUY", "1"):
                return TradeType.BUY
            elif v in ("SELL", "2"):
                return TradeType.SELL
            elif v in ("RANGE", "3"):
                return TradeType.RANGE
            raise ValueError(f"Invalid side '{v}'. Must be BUY, SELL, or RANGE")
        if isinstance(v, int):
            if v == 1:
                return TradeType.BUY
            elif v == 2:
                return TradeType.SELL
            elif v == 3:
                return TradeType.RANGE
            raise ValueError(f"Invalid side {v}. Must be 1 (BUY), 2 (SELL), or 3 (RANGE)")
        raise ValueError(f"Invalid side type {type(v)}. Must be TradeType, str, or int")

    @model_validator(mode="after")
    def validate_price_limit_ranges(self):
        """Validate that price limit ranges are valid."""
        if self.buy_price_max is not None and self.buy_price_min is not None:
            if self.buy_price_max < self.buy_price_min:
                raise ValueError("buy_price_max must be >= buy_price_min")
        if self.sell_price_max is not None and self.sell_price_min is not None:
            if self.sell_price_max < self.sell_price_min:
                raise ValueError("sell_price_max must be >= sell_price_min")
        # For negative offset (in-range), offset magnitude must not exceed width
        if self.position_offset_pct < 0:
            if abs(self.position_offset_pct) > self.position_width_pct:
                raise ValueError(
                    f"For in-range positions, |position_offset_pct| ({abs(self.position_offset_pct)}) "
                    f"must not exceed position_width_pct ({self.position_width_pct})"
                )
        return self

    def update_markets(self, markets: MarketDict) -> MarketDict:
        """Register the LP connector with trading pair"""
        markets = markets.add_or_update(self.connector_name, self.trading_pair)
        return markets


class LPRebalancer(ControllerBase):
    """
    Controller for LP position management with smart rebalancing.

    Key features:
    - Uses total_amount_quote for all positions (initial and rebalance)
    - Derives rebalance side from price vs last executor's range
    - KEEP position when already at limit, REBALANCE when not
    - Validates bounds before creating positions
    """

    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, config: LPRebalancerConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config: LPRebalancerConfig = config

        # Parse lp_provider into dex_name and trading_type for gateway calls
        self.lp_dex_name, self.lp_trading_type = parse_provider(
            config.lp_provider, default_trading_type="clmm"
        )

        # Parse token symbols from trading pair
        parts = config.trading_pair.split("-")
        self._base_token: str = parts[0] if len(parts) >= 2 else ""
        self._quote_token: str = parts[1] if len(parts) >= 2 else ""

        # Rebalance tracking
        self._pending_rebalance: bool = False
        self._pending_rebalance_side: Optional[int] = None  # Side for pending rebalance

        # Track the executor we created
        self._current_executor_id: Optional[str] = None

        # Track amounts from last closed position (for rebalance sizing)
        self._last_closed_base_amount: Optional[Decimal] = None
        self._last_closed_quote_amount: Optional[Decimal] = None
        self._last_closed_base_fee: Optional[Decimal] = None
        self._last_closed_quote_fee: Optional[Decimal] = None

        # Track initial balances for comparison
        self._initial_base_balance: Optional[Decimal] = None
        self._initial_quote_balance: Optional[Decimal] = None

        # Flag to trigger balance update after position creation
        self._pending_balance_update: bool = False

        # Cached pool price (updated in update_processed_data)
        self._pool_price: Optional[Decimal] = None

        # Swap executor tracking (for autoswap feature)
        self._swap_executor_id: Optional[str] = None
        self._pending_swap_side: Optional[int] = None  # LP side to create after swap completes

        # Track if initial position has been created (after that, always use side 1 or 2)
        self._initial_position_created: bool = False

        # Initialize rate sources
        self.market_data_provider.initialize_rate_sources([
            ConnectorPair(
                connector_name=self.config.connector_name,
                trading_pair=self.config.trading_pair
            )
        ])

    def active_executor(self) -> Optional[ExecutorInfo]:
        """Get current active executor (should be 0 or 1)"""
        active = [e for e in self.executors_info if e.is_active]
        return active[0] if active else None

    def get_tracked_executor(self) -> Optional[ExecutorInfo]:
        """Get the executor we're currently tracking (by ID)"""
        if not self._current_executor_id:
            return None
        for e in self.executors_info:
            if e.id == self._current_executor_id:
                return e
        return None

    def is_tracked_executor_terminated(self) -> bool:
        """Check if the executor we created has terminated"""
        from hummingbot.strategy_v2.models.base import RunnableStatus
        if not self._current_executor_id:
            return True
        executor = self.get_tracked_executor()
        if executor is None:
            return True
        return executor.status == RunnableStatus.TERMINATED

    def get_swap_executor(self) -> Optional[ExecutorInfo]:
        """Get the swap executor we're tracking"""
        if not self._swap_executor_id:
            return None
        for e in self.executors_info:
            if e.id == self._swap_executor_id:
                return e
        return None

    def is_swap_executor_done(self) -> bool:
        """Check if swap executor has completed (success or failure)"""
        if not self._swap_executor_id:
            return True
        swap_executor = self.get_swap_executor()
        if swap_executor is None:
            return True
        return swap_executor.is_done

    def _check_autoswap_needed(self, side: TradeType, current_price: Decimal) -> Optional[OrderExecutorConfig]:
        """
        Check if autoswap is needed and return swap config if so.

        Returns OrderExecutorConfig if swap is needed, None otherwise.

        Simply checks balance vs required amounts and swaps deficit + buffer if insufficient.
        Works for both positive offset (out-of-range) and negative offset (in-range) positions.

        For rebalances, includes tokens from just-closed position in available balance
        since wallet balance may not be updated yet.
        """
        if not self.config.autoswap:
            return None

        # Capture closed position amounts BEFORE creating LP position
        # (they get cleared after position creation in determine_executor_actions)
        closed_base = self._last_closed_base_amount or Decimal("0")
        closed_quote = self._last_closed_quote_amount or Decimal("0")
        closed_base_fee = self._last_closed_base_fee or Decimal("0")
        closed_quote_fee = self._last_closed_quote_fee or Decimal("0")

        # Calculate required amounts (handles negative offset internally)
        base_amt, quote_amt = self._calculate_amounts(side, current_price)

        # Get current wallet balances
        try:
            base_balance = self.market_data_provider.get_balance(
                self.config.connector_name, self._base_token
            )
            quote_balance = self.market_data_provider.get_balance(
                self.config.connector_name, self._quote_token
            )
        except Exception as e:
            self.logger().warning(f"Could not fetch balances for autoswap check: {e}")
            return None

        # For rebalances, add closed position amounts to available balance
        # (wallet balance may not be updated yet after position close)
        if closed_base > 0 or closed_quote > 0:
            base_balance += closed_base + closed_base_fee
            quote_balance += closed_quote + closed_quote_fee
            self.logger().info(
                f"Autoswap: including closed position amounts in balance: "
                f"+{closed_base + closed_base_fee:.6f} {self._base_token}, "
                f"+{closed_quote + closed_quote_fee:.6f} {self._quote_token}"
            )

        # Calculate deficit from raw amounts
        base_deficit = base_amt - base_balance
        quote_deficit = quote_amt - quote_balance

        # Add 0.1 SOL buffer for rent and transaction fees when SOL is involved
        sol_buffer = Decimal("0.1")
        if self._base_token.upper() == "SOL":
            base_deficit += sol_buffer
        if self._quote_token.upper() == "SOL":
            quote_deficit += sol_buffer

        self.logger().info(
            f"Autoswap check: need base={base_amt:.6f}, have={base_balance:.6f}, deficit={base_deficit:.6f} | "
            f"need quote={quote_amt:.6f}, have={quote_balance:.6f}, deficit={quote_deficit:.6f}"
        )

        # Buffer multiplier only applied to swap amount
        buffer_multiplier = Decimal("1") + (self.config.swap_buffer_pct / Decimal("100"))

        # If any deficit, swap
        if base_deficit > 0 and quote_deficit <= 0:
            # Need more base, have enough quote - BUY base with quote
            swap_amount = base_deficit * buffer_multiplier
            # Check if we have enough quote to buy this much base
            required_quote = swap_amount * current_price * Decimal("1.02")  # 2% extra for price movement
            if quote_balance >= required_quote:
                self.logger().info(
                    f"Autoswap: BUY {swap_amount:.6f} {self._base_token} "
                    f"(deficit={base_deficit:.6f} + {self.config.swap_buffer_pct}% buffer, "
                    f"have {quote_balance:.6f} {self._quote_token})"
                )
                return OrderExecutorConfig(
                    timestamp=self.market_data_provider.time(),
                    connector_name=self.config.connector_name,
                    trading_pair=self.config.trading_pair,
                    side=TradeType.BUY,
                    amount=swap_amount,
                    execution_strategy=ExecutionStrategy.MARKET,
                )
            else:
                self.logger().warning(
                    f"Autoswap: insufficient quote ({quote_balance:.6f}) to buy {swap_amount:.6f} base "
                    f"(need ~{required_quote:.6f} {self._quote_token})"
                )
                return None

        elif quote_deficit > 0 and base_deficit <= 0:
            # Need more quote, have enough base - SELL base for quote
            swap_amount = (quote_deficit / current_price) * buffer_multiplier
            # Check if we have enough base to sell
            if base_balance >= swap_amount * Decimal("1.02"):  # 2% extra for price movement
                self.logger().info(
                    f"Autoswap: SELL {swap_amount:.6f} {self._base_token} for ~{quote_deficit:.6f} {self._quote_token} "
                    f"(deficit + {self.config.swap_buffer_pct}% buffer, have {base_balance:.6f} {self._base_token})"
                )
                return OrderExecutorConfig(
                    timestamp=self.market_data_provider.time(),
                    connector_name=self.config.connector_name,
                    trading_pair=self.config.trading_pair,
                    side=TradeType.SELL,
                    amount=swap_amount,
                    execution_strategy=ExecutionStrategy.MARKET,
                )
            else:
                self.logger().warning(
                    f"Autoswap: insufficient base ({base_balance:.6f}) to sell for {quote_deficit:.6f} quote"
                )
                return None

        elif base_deficit > 0 and quote_deficit > 0:
            # Both tokens in deficit - user is underfunded for side=RANGE
            total_deficit_quote = base_deficit * current_price + quote_deficit
            self.logger().warning(
                f"Autoswap: cannot swap - both tokens in deficit (side=RANGE). "
                f"Need {base_deficit:.6f} more {self._base_token} AND {quote_deficit:.6f} more {self._quote_token} "
                f"(total deficit: {total_deficit_quote:.2f} {self._quote_token})"
            )
            return None

        # No swap needed
        return None

    def _trigger_balance_update(self):
        """Trigger a balance update on the connector after position changes."""
        try:
            connector = self.market_data_provider.get_connector(self.config.connector_name)
            if hasattr(connector, 'update_balances'):
                safe_ensure_future(connector.update_balances())
                self.logger().info("Triggered balance update after position creation")
        except Exception as e:
            self.logger().debug(f"Could not trigger balance update: {e}")

    def determine_executor_actions(self) -> List[ExecutorAction]:
        """Decide whether to create/stop executors"""
        # Capture initial balances on first run
        if self._initial_base_balance is None:
            try:
                self._initial_base_balance = self.market_data_provider.get_balance(
                    self.config.connector_name, self._base_token
                )
                self._initial_quote_balance = self.market_data_provider.get_balance(
                    self.config.connector_name, self._quote_token
                )
            except Exception as e:
                self.logger().debug(f"Could not capture initial balances: {e}")

        actions = []

        # Check if swap executor is running (autoswap in progress)
        if self._pending_swap_side is not None:
            # Find and track the swap executor if not already tracked
            if not self._swap_executor_id:
                for e in self.executors_info:
                    if e.config.type == "order_executor" and e.is_active:
                        self._swap_executor_id = e.id
                        self.logger().info(f"Tracking swap executor: {e.id}")
                        break

            # If swap is pending but executor not found yet, wait for it to appear
            if not self._swap_executor_id:
                self.logger().debug("Waiting for swap executor to appear in executors_info")
                return actions

        if self._swap_executor_id:
            if not self.is_swap_executor_done():
                swap_executor = self.get_swap_executor()
                state = swap_executor.custom_info.get("state") if swap_executor else "unknown"
                self.logger().debug(f"Waiting for swap executor to complete (state: {state})")
                return actions

            # Swap executor completed - check result and proceed
            swap_executor = self.get_swap_executor()
            pending_side = self._pending_swap_side

            # Clear swap tracking
            self._swap_executor_id = None
            self._pending_swap_side = None

            # Check if swap succeeded (not failed)
            swap_succeeded = swap_executor and swap_executor.close_type != CloseType.FAILED
            if swap_succeeded:
                self.logger().info("Autoswap completed successfully, proceeding to LP position")
                # Trigger balance update after successful swap
                self._trigger_balance_update()

                # Create LP position with the side that was pending
                if pending_side is not None:
                    executor_config = self._create_executor_config(pending_side)
                    if executor_config:
                        actions.append(CreateExecutorAction(
                            controller_id=self.config.id,
                            executor_config=executor_config
                        ))
                        self._initial_position_created = True
                        self._pending_balance_update = True
            else:
                # Swap failed - log error and skip LP position creation this cycle
                close_type = swap_executor.close_type if swap_executor else "unknown"
                self.logger().error(
                    f"Autoswap FAILED (close_type: {close_type}). "
                    f"Will retry autoswap check on next cycle for side={pending_side}"
                )
                # Don't create LP position - let the next cycle re-check balances
                # and potentially retry the swap

            return actions

        executor = self.active_executor()

        # Track the active executor's ID if we don't have one yet
        if executor and not self._current_executor_id:
            self._current_executor_id = executor.id
            self.logger().info(f"Tracking executor: {executor.id}")

        # No active executor - check if we should create one
        if executor is None:
            if not self.is_tracked_executor_terminated():
                tracked = self.get_tracked_executor()
                self.logger().debug(
                    f"Waiting for executor {self._current_executor_id} to terminate "
                    f"(status: {tracked.status if tracked else 'not found'})"
                )
                return actions

            # Previous executor terminated - capture final amounts for rebalance sizing
            terminated_executor = self.get_tracked_executor()
            if terminated_executor and self._pending_rebalance:
                self._last_closed_base_amount = Decimal(str(terminated_executor.custom_info.get("base_amount", 0)))
                self._last_closed_quote_amount = Decimal(str(terminated_executor.custom_info.get("quote_amount", 0)))
                self._last_closed_base_fee = Decimal(str(terminated_executor.custom_info.get("base_fee", 0)))
                self._last_closed_quote_fee = Decimal(str(terminated_executor.custom_info.get("quote_fee", 0)))
                self.logger().info(
                    f"Captured closed position amounts: base={self._last_closed_base_amount}, "
                    f"quote={self._last_closed_quote_amount}, base_fee={self._last_closed_base_fee}, "
                    f"quote_fee={self._last_closed_quote_fee}"
                )

            # Clear tracking
            self._current_executor_id = None

            # Determine side for new position
            if self._pending_rebalance and self._pending_rebalance_side is not None:
                # Rebalance: use the side determined by price direction
                side = self._pending_rebalance_side
                self._pending_rebalance = False
                self._pending_rebalance_side = None
            elif not self._initial_position_created:
                # Initial position: use configured side (can be BUY, SELL, or RANGE)
                side = self.config.side
            else:
                # After initial position but no pending rebalance (e.g., position failed/closed)
                # Determine side from current price vs price limits
                if not self._pool_price:
                    self.logger().info("Waiting for pool price to determine side")
                    return actions
                side = self._determine_side_from_price(self._pool_price)

            # Check if autoswap is needed before creating LP position
            if self.config.autoswap:
                if not self._pool_price:
                    self.logger().info("Autoswap: waiting for pool price")
                    return actions
                swap_config = self._check_autoswap_needed(side, self._pool_price)
                if swap_config:
                    # Create swap executor and wait for it to complete
                    self._pending_swap_side = side
                    actions.append(CreateExecutorAction(
                        controller_id=self.config.id,
                        executor_config=swap_config
                    ))
                    # Track the swap executor ID on next tick
                    return actions
                else:
                    self.logger().info("Autoswap: no swap needed, balances sufficient")

            # Create executor config with calculated bounds
            executor_config = self._create_executor_config(side)
            if executor_config is None:
                self.logger().warning("Skipping position creation - invalid bounds")
                return actions

            actions.append(CreateExecutorAction(
                controller_id=self.config.id,
                executor_config=executor_config
            ))
            # Note: _initial_position_created is set below when position is confirmed active
            self._pending_balance_update = True

            # Clear closed position amounts after LP position is created
            self._last_closed_base_amount = None
            self._last_closed_quote_amount = None
            self._last_closed_base_fee = None
            self._last_closed_quote_fee = None

            return actions

        # Mark initial position created and trigger balance update when position is active
        if self._pending_balance_update:
            state = executor.custom_info.get("state")
            if state in ("IN_RANGE", "OUT_OF_RANGE"):
                self._pending_balance_update = False
                self._initial_position_created = True  # Only mark created when actually active
                self._trigger_balance_update()

        # Check executor state
        state = executor.custom_info.get("state")

        # Don't take action while executor is in transition states
        if state in [LPExecutorStates.OPENING.value, LPExecutorStates.CLOSING.value]:
            return actions

        # Check for rebalancing when out of range
        if state == LPExecutorStates.OUT_OF_RANGE.value:
            # Check if price is beyond threshold before considering timer
            if self._is_beyond_rebalance_threshold(executor):
                out_of_range_seconds = executor.custom_info.get("out_of_range_seconds")
                if out_of_range_seconds is not None and out_of_range_seconds >= self.config.rebalance_seconds:
                    rebalance_action = self._handle_rebalance(executor)
                    if rebalance_action:
                        actions.append(rebalance_action)

        return actions

    def _handle_rebalance(self, executor: ExecutorInfo) -> Optional[StopExecutorAction]:
        """
        Handle rebalancing logic.

        Returns StopExecutorAction if rebalance needed, None if KEEP.
        """
        current_price = executor.custom_info.get("current_price")
        lower_price = executor.custom_info.get("lower_price")
        upper_price = executor.custom_info.get("upper_price")

        if current_price is None or lower_price is None or upper_price is None:
            return None

        current_price = Decimal(str(current_price))
        lower_price = Decimal(str(lower_price))
        upper_price = Decimal(str(upper_price))

        # Step 1: Determine side from price direction (using [lower, upper) convention)
        if current_price >= upper_price:
            new_side = TradeType.BUY  # price at or above range
        elif current_price < lower_price:
            new_side = TradeType.SELL  # price below range
        else:
            # Price is in range, shouldn't happen in OUT_OF_RANGE state
            self.logger().warning(f"Price {current_price} appears in range [{lower_price}, {upper_price})")
            return None

        # Step 2: Check if new position would be valid (price within limits)
        if not self._is_price_within_limits(current_price, new_side):
            # Don't log repeatedly - this is checked every tick
            return None

        # Step 3: Initiate rebalance
        self._pending_rebalance = True
        self._pending_rebalance_side = new_side
        self.logger().info(
            f"REBALANCE initiated (side={new_side}, price={current_price}, "
            f"old_bounds=[{lower_price}, {upper_price}])"
        )

        return StopExecutorAction(
            controller_id=self.config.id,
            executor_id=executor.id,
        )

    def _is_beyond_rebalance_threshold(self, executor: ExecutorInfo) -> bool:
        """
        Check if price is beyond the rebalance threshold.

        Price must be this % out of range before rebalance timer is considered.
        """
        current_price = executor.custom_info.get("current_price")
        lower_price = executor.custom_info.get("lower_price")
        upper_price = executor.custom_info.get("upper_price")

        if current_price is None or lower_price is None or upper_price is None:
            return False

        threshold = self.config.rebalance_threshold_pct / Decimal("100")

        # Check if price is beyond threshold above upper or below lower
        if current_price > upper_price:
            deviation_pct = (current_price - upper_price) / upper_price
            return deviation_pct >= threshold
        elif current_price < lower_price:
            deviation_pct = (lower_price - current_price) / lower_price
            return deviation_pct >= threshold

        return False  # Price is in range

    def _create_executor_config(self, side: TradeType) -> Optional[LPExecutorConfig]:
        """
        Create executor config for the given side.

        Returns None if bounds are invalid.
        """
        # Use pool price (fetched in update_processed_data every tick)
        current_price = self._pool_price
        if current_price is None or current_price == 0:
            self.logger().warning("No pool price available - waiting for update_processed_data")
            return None

        # Calculate bounds for requested side
        lower_price, upper_price = self._calculate_price_bounds(side, current_price)

        # Check bounds against price limits - clamp if one exceeds, try opposite if both exceed
        lower_price, upper_price, side = self._validate_and_clamp_bounds(
            lower_price, upper_price, side, current_price
        )
        if lower_price is None:
            return None

        # Validate bounds after clamping
        if lower_price >= upper_price:
            self.logger().warning(f"Invalid bounds [{lower_price}, {upper_price}] - skipping")
            return None

        # Calculate amounts based on final side
        base_amt, quote_amt = self._calculate_amounts(side, current_price)

        # Build extra params (connector-specific)
        extra_params = {}
        if self.config.strategy_type is not None:
            extra_params["strategyType"] = self.config.strategy_type

        self.logger().info(
            f"Creating position: side={side.name}, pool_price={current_price:.2f}, "
            f"bounds=[{lower_price:.4f}, {upper_price:.4f}], "
            f"base={base_amt:.4f}, quote={quote_amt:.4f}"
        )

        return LPExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.connector_name,
            lp_provider=self.config.lp_provider,
            trading_pair=self.config.trading_pair,
            pool_address=self.config.pool_address,
            lower_price=lower_price,
            upper_price=upper_price,
            base_amount=base_amt,
            quote_amount=quote_amt,
            side=side,
            extra_params=extra_params if extra_params else None,
        )

    def _calculate_amounts(self, side: TradeType, current_price: Decimal) -> tuple:
        """
        Calculate base and quote amounts based on side, offset, and total_amount_quote.

        Allocation logic:
        - Side RANGE: split 50/50
        - Side 1/2 with offset >= 0 (out-of-range): 100% single-sided
        - Side 1/2 with offset < 0 (in-range): proportional split based on price position

        For in-range positions, the split is calculated based on where current price
        sits in the range. This mirrors CLMM behavior where both tokens are needed
        when price is within bounds.

        Note: No clamping is done here - autoswap handles any token deficits.
        """
        total = self.config.total_amount_quote
        offset = self.config.position_offset_pct

        if side == TradeType.RANGE:  # RANGE
            quote_amt = total / Decimal("2")
            base_amt = quote_amt / current_price
        elif offset >= 0:
            # Out-of-range: single-sided allocation
            if side == TradeType.BUY:  # BUY - all quote
                base_amt = Decimal("0")
                quote_amt = total
            else:  # SELL - all base
                base_amt = total / current_price
                quote_amt = Decimal("0")
        else:
            # In-range (offset < 0): proportional split based on price position in range
            # Calculate bounds to determine where price sits
            lower_price, upper_price = self._calculate_price_bounds(side, current_price)
            price_range = upper_price - lower_price

            if price_range <= 0 or current_price <= lower_price:
                # At or below lower bound - all quote for BUY, all base for SELL
                if side == TradeType.BUY:
                    base_amt = Decimal("0")
                    quote_amt = total
                else:
                    base_amt = total / current_price
                    quote_amt = Decimal("0")
            elif current_price >= upper_price:
                # At or above upper bound - all base for SELL, all quote for BUY
                if side == TradeType.SELL:
                    base_amt = total / current_price
                    quote_amt = Decimal("0")
                else:
                    base_amt = Decimal("0")
                    quote_amt = total
            else:
                # Price is in range - calculate proportional split
                # price_ratio: 0 at lower_price, 1 at upper_price
                price_ratio = (current_price - lower_price) / price_range
                # As price goes up, more of the position is in quote, less in base
                quote_pct = price_ratio
                base_pct = Decimal("1") - price_ratio

                quote_amt = total * quote_pct
                base_amt = (total * base_pct) / current_price

        return base_amt, quote_amt

    def _calculate_price_bounds(self, side: TradeType, current_price: Decimal) -> tuple:
        """
        Calculate position bounds based on side and price limits.

        Side RANGE: centered on current price, clamped to [buy_min, sell_max]
        Side 1 (BUY): upper = min(current, buy_price_max) * (1 - offset), lower extends width below
        Side 2 (SELL): lower = max(current, sell_price_min) * (1 + offset), upper extends width above

        The offset ensures single-sided positions start out-of-range so they only
        require one token (SOL for SELL, USDC for BUY).
        """
        width = self.config.position_width_pct / Decimal("100")
        offset = self.config.position_offset_pct / Decimal("100")

        if side == TradeType.RANGE:
            # Centered on current price
            half_width = width / Decimal("2")
            lower_price = current_price * (Decimal("1") - half_width)
            upper_price = current_price * (Decimal("1") + half_width)

        elif side == TradeType.BUY:
            # Anchor at buy_price_max if set, otherwise at current price
            if self.config.buy_price_max:
                upper_price = min(current_price, self.config.buy_price_max)
            else:
                upper_price = current_price
            upper_price = upper_price * (Decimal("1") - offset)
            lower_price = upper_price * (Decimal("1") - width)

        else:  # SELL
            # Anchor at sell_price_min if set, otherwise at current price
            if self.config.sell_price_min:
                lower_price = max(current_price, self.config.sell_price_min)
            else:
                lower_price = current_price
            lower_price = lower_price * (Decimal("1") + offset)
            upper_price = lower_price * (Decimal("1") + width)

        return lower_price, upper_price

    def _is_price_within_limits(self, price: Decimal, side: TradeType) -> bool:
        """
        Check if price is within configured limits for the position type.

        Price must be within the range to create a position that's IN_RANGE:
        - BUY: price must be within [buy_price_min, buy_price_max]
        - SELL: price must be within [sell_price_min, sell_price_max]
        - RANGE: price must be within the intersection of both ranges

        If price is outside the range, the position would be immediately OUT_OF_RANGE.
        """
        if side == TradeType.SELL:  # SELL
            if self.config.sell_price_min and price < self.config.sell_price_min:
                return False
            if self.config.sell_price_max and price > self.config.sell_price_max:
                return False
        elif side == TradeType.BUY:  # BUY
            if self.config.buy_price_min and price < self.config.buy_price_min:
                return False
            if self.config.buy_price_max and price > self.config.buy_price_max:
                return False
        else:  # RANGE - must be within intersection of ranges
            # Check buy range
            if self.config.buy_price_min and price < self.config.buy_price_min:
                return False
            if self.config.buy_price_max and price > self.config.buy_price_max:
                return False
            # Check sell range
            if self.config.sell_price_min and price < self.config.sell_price_min:
                return False
            if self.config.sell_price_max and price > self.config.sell_price_max:
                return False
        return True

    def _validate_and_clamp_bounds(
        self, lower_price: Decimal, upper_price: Decimal, side: TradeType, current_price: Decimal
    ) -> tuple:
        """
        Validate bounds against price limits. Clamp if one bound exceeds, try opposite side if both exceed.

        Returns: (lower_price, upper_price, side) or (None, None, None) if no valid position possible.

        Note: RANGE positions skip price limit checks entirely.
        """
        # RANGE positions skip price limit checks
        if side == TradeType.RANGE:
            return lower_price, upper_price, side

        # Get limits for this side
        if side == TradeType.BUY:
            min_limit = self.config.buy_price_min
            max_limit = self.config.buy_price_max
        else:  # SELL
            min_limit = self.config.sell_price_min
            max_limit = self.config.sell_price_max

        # Check how many bounds exceed limits
        lower_exceeds = min_limit and lower_price < min_limit
        upper_exceeds = max_limit and upper_price > max_limit

        if not lower_exceeds and not upper_exceeds:
            # Both bounds within limits
            return lower_price, upper_price, side

        if lower_exceeds and upper_exceeds:
            # Both bounds exceed - try opposite side
            opposite_side = TradeType.SELL if side == TradeType.BUY else TradeType.BUY
            opp_lower, opp_upper = self._calculate_price_bounds(opposite_side, current_price)

            # Check opposite side limits
            if opposite_side == TradeType.BUY:
                opp_min = self.config.buy_price_min
                opp_max = self.config.buy_price_max
            else:
                opp_min = self.config.sell_price_min
                opp_max = self.config.sell_price_max

            opp_lower_exceeds = opp_min and opp_lower < opp_min
            opp_upper_exceeds = opp_max and opp_upper > opp_max

            if not opp_lower_exceeds and not opp_upper_exceeds:
                self.logger().info(f"Side {side.name} out of limits, using {opposite_side.name}")
                return opp_lower, opp_upper, opposite_side
            elif opp_lower_exceeds and not opp_upper_exceeds:
                # Clamp lower on opposite side
                self.logger().info(f"Side {side.name} out of limits, using {opposite_side.name} (clamped lower)")
                return opp_min, opp_upper, opposite_side
            elif not opp_lower_exceeds and opp_upper_exceeds:
                # Clamp upper on opposite side
                self.logger().info(f"Side {side.name} out of limits, using {opposite_side.name} (clamped upper)")
                return opp_lower, opp_max, opposite_side
            else:
                # Both sides completely out of limits
                self.logger().info("Both sides out of price limits - waiting")
                return None, None, None

        # Only one bound exceeds - clamp it
        if lower_exceeds:
            self.logger().debug(f"Clamping lower from {lower_price} to {min_limit}")
            return min_limit, upper_price, side
        else:  # upper_exceeds
            self.logger().debug(f"Clamping upper from {upper_price} to {max_limit}")
            return lower_price, max_limit, side

    def _determine_side_from_price(self, current_price: Decimal) -> TradeType:
        """
        Determine side (BUY or SELL) based on current price vs price limits.

        Used after initial position to ensure we never use RANGE for rebalances.
        - If price is closer to buy range, use BUY
        - If price is closer to sell range, use SELL
        """
        # Get midpoints of buy and sell ranges
        buy_mid = None
        sell_mid = None

        if self.config.buy_price_min and self.config.buy_price_max:
            buy_mid = (self.config.buy_price_min + self.config.buy_price_max) / 2
        if self.config.sell_price_min and self.config.sell_price_max:
            sell_mid = (self.config.sell_price_min + self.config.sell_price_max) / 2

        # If both ranges defined, use the one price is closer to
        if buy_mid and sell_mid:
            if current_price <= buy_mid:
                return TradeType.BUY  # price in lower range
            elif current_price >= sell_mid:
                return TradeType.SELL  # price in upper range
            else:
                # Price between buy_mid and sell_mid - use BUY if closer to buy_mid
                return TradeType.BUY if (current_price - buy_mid) < (sell_mid - current_price) else TradeType.SELL

        # If only one range defined, use that side
        if buy_mid:
            return TradeType.BUY
        if sell_mid:
            return TradeType.SELL

        # No price limits defined - default to BUY
        return TradeType.BUY

    async def update_processed_data(self):
        """Called every tick - fetch pool price."""
        try:
            connector = self.market_data_provider.get_connector(self.config.connector_name)
            if hasattr(connector, 'get_pool_info_by_address'):
                pool_info = await connector.get_pool_info_by_address(
                    self.config.pool_address,
                    dex_name=self.lp_dex_name,
                    trading_type=self.lp_trading_type,
                )
                if pool_info and pool_info.price:
                    self._pool_price = Decimal(str(pool_info.price))
        except Exception as e:
            self.logger().debug(f"Could not fetch pool price: {e}")

    def to_format_status(self) -> List[str]:
        """Format status for display."""
        status = []
        box_width = 100
        price_decimals = 8  # For small-value tokens like memecoins

        # Header
        status.append("+" + "-" * box_width + "+")
        header = f"| LP Rebalancer: {self.config.trading_pair} on {self.config.connector_name}"
        status.append(header + " " * (box_width - len(header) + 1) + "|")
        status.append("+" + "-" * box_width + "+")

        # === CONFIG SECTION ===
        line = f"| Network: {self.config.connector_name} | LP: {self.config.lp_provider}"
        status.append(line + " " * (box_width - len(line) + 1) + "|")

        line = f"| Pool: {self.config.pool_address}"
        status.append(line + " " * (box_width - len(line) + 1) + "|")

        # Config summary
        side_str = self.config.side.name
        amt = self.config.total_amount_quote
        width = self.config.position_width_pct
        offset = self.config.position_offset_pct
        rebal = self.config.rebalance_seconds
        line = f"| Config: side={side_str}, amount={amt} {self._quote_token}, width={width}%, offset={offset}%, rebal={rebal}s"
        status.append(line + " " * (box_width - len(line) + 1) + "|")

        # Spacer before Position section
        status.append("|" + " " * box_width + "|")

        # === POSITION SECTION ===
        executor = self.active_executor() or self.get_tracked_executor()

        # Get position amounts for balance calculations
        pos_base_amount = Decimal("0")
        pos_quote_amount = Decimal("0")

        if executor and not executor.is_done:
            custom = executor.custom_info

            # Position Address
            position_address = custom.get("position_address", "N/A")
            line = f"| Position: {position_address}"
            status.append(line + " " * (box_width - len(line) + 1) + "|")

            # Assets row: base_amount + quote_amount = total value
            pos_base_amount = Decimal(str(custom.get("base_amount", 0)))
            pos_quote_amount = Decimal(str(custom.get("quote_amount", 0)))
            total_value_quote = Decimal(str(custom.get("total_value_quote", 0)))
            line = (
                f"| Assets: {float(pos_base_amount):.6f} {self._base_token} + "
                f"{float(pos_quote_amount):.6f} {self._quote_token} = {float(total_value_quote):.4f} {self._quote_token}"
            )
            status.append(line + " " * (box_width - len(line) + 1) + "|")

            # Fees row: base_fee + quote_fee = total
            base_fee = Decimal(str(custom.get("base_fee", 0)))
            quote_fee = Decimal(str(custom.get("quote_fee", 0)))
            fees_earned_quote = Decimal(str(custom.get("fees_earned_quote", 0)))
            line = (
                f"| Fees: {float(base_fee):.6f} {self._base_token} + "
                f"{float(quote_fee):.6f} {self._quote_token} = {float(fees_earned_quote):.6f} {self._quote_token}"
            )
            status.append(line + " " * (box_width - len(line) + 1) + "|")

            # Price and rebalance thresholds
            lower_price = custom.get("lower_price")
            upper_price = custom.get("upper_price")

            if lower_price is not None and upper_price is not None and self._pool_price:
                threshold = self.config.rebalance_threshold_pct / Decimal("100")
                lower_threshold = Decimal(str(lower_price)) * (Decimal("1") - threshold)
                upper_threshold = Decimal(str(upper_price)) * (Decimal("1") + threshold)

                # Lower threshold triggers SELL - check sell_price_min
                if self.config.sell_price_min and lower_threshold < self.config.sell_price_min:
                    lower_str = "N/A"
                else:
                    lower_str = f"{float(lower_threshold):.{price_decimals}f}"

                # Upper threshold triggers BUY - check buy_price_max
                if self.config.buy_price_max and upper_threshold > self.config.buy_price_max:
                    upper_str = "N/A"
                else:
                    upper_str = f"{float(upper_threshold):.{price_decimals}f}"

                line = f"| Price: {float(self._pool_price):.{price_decimals}f}  |  Rebalance if: <{lower_str} or >{upper_str}"
                status.append(line + " " * (box_width - len(line) + 1) + "|")

                # Status with icon
                state = custom.get("state", "UNKNOWN")
                state_icons = {
                    "IN_RANGE": "●",
                    "OUT_OF_RANGE": "○",
                    "OPENING": "◐",
                    "CLOSING": "◑",
                    "COMPLETE": "◌",
                    "NOT_ACTIVE": "○",
                }
                state_icon = state_icons.get(state, "?")

                status.append("|" + " " * box_width + "|")
                line = f"| Position Status: [{state_icon} {state}]"
                status.append(line + " " * (box_width - len(line) + 1) + "|")

                # Range visualization
                range_viz = self._create_price_range_visualization(
                    Decimal(str(lower_price)),
                    self._pool_price,
                    Decimal(str(upper_price))
                )
                for viz_line in range_viz.split('\n'):
                    line = f"| {viz_line}"
                    status.append(line + " " * (box_width - len(line) + 1) + "|")

                # Rebalance timer if out of range
                out_of_range_seconds = custom.get("out_of_range_seconds")
                if out_of_range_seconds is not None:
                    beyond_threshold = self._is_beyond_rebalance_threshold(executor)
                    if beyond_threshold:
                        line = f"| Rebalance: {out_of_range_seconds}s / {self.config.rebalance_seconds}s"
                    else:
                        line = f"| Rebalance: waiting (below {float(self.config.rebalance_threshold_pct):.2f}% threshold)"
                    status.append(line + " " * (box_width - len(line) + 1) + "|")
        else:
            line = "| Position: None"
            status.append(line + " " * (box_width - len(line) + 1) + "|")

        # === PRICE LIMITS VISUALIZATION ===
        has_limits = any([
            self.config.sell_price_min, self.config.sell_price_max,
            self.config.buy_price_min, self.config.buy_price_max
        ])
        if has_limits and self._pool_price:
            pos_lower = None
            pos_upper = None
            if executor and not executor.is_done:
                pos_lower = executor.custom_info.get("lower_price")
                pos_upper = executor.custom_info.get("upper_price")
                if pos_lower:
                    pos_lower = Decimal(str(pos_lower))
                if pos_upper:
                    pos_upper = Decimal(str(pos_upper))

            status.append("|" + " " * box_width + "|")
            limits_viz = self._create_price_limits_visualization(
                self._pool_price, pos_lower, pos_upper, price_decimals
            )
            if limits_viz:
                for viz_line in limits_viz.split('\n'):
                    line = f"| {viz_line}"
                    status.append(line + " " * (box_width - len(line) + 1) + "|")

        # === BALANCES ===
        status.append("|" + " " * box_width + "|")
        try:
            wallet_base = self.market_data_provider.get_balance(
                self.config.connector_name, self._base_token
            )
            wallet_quote = self.market_data_provider.get_balance(
                self.config.connector_name, self._quote_token
            )

            line = "| Balances:"
            status.append(line + " " * (box_width - len(line) + 1) + "|")

            # Table header: Asset | Initial | Current (wallet) | Position | Change
            header = f"|   {'Asset':<8} {'Initial':>12} {'Current':>12} {'Position':>12} {'Change':>14}"
            status.append(header + " " * (box_width - len(header) + 1) + "|")

            # Base token row
            # Change = (wallet + position) - initial
            if self._initial_base_balance is not None:
                total_base = wallet_base + pos_base_amount
                base_change = total_base - self._initial_base_balance
                init_b = float(self._initial_base_balance)
                wall_b = float(wallet_base)
                pos_b = float(pos_base_amount)
                chg_b = float(base_change)
                line = f"|   {self._base_token:<8} {init_b:>12.6f} {wall_b:>12.6f} {pos_b:>12.6f} {chg_b:>+14.6f}"
            else:
                wall_b = float(wallet_base)
                pos_b = float(pos_base_amount)
                line = f"|   {self._base_token:<8} {'N/A':>12} {wall_b:>12.6f} {pos_b:>12.6f} {'N/A':>14}"
            status.append(line + " " * (box_width - len(line) + 1) + "|")

            # Quote token row
            if self._initial_quote_balance is not None:
                total_quote = wallet_quote + pos_quote_amount
                quote_change = total_quote - self._initial_quote_balance
                init_q = float(self._initial_quote_balance)
                wall_q = float(wallet_quote)
                pos_q = float(pos_quote_amount)
                chg_q = float(quote_change)
                line = f"|   {self._quote_token:<8} {init_q:>12.6f} {wall_q:>12.6f} {pos_q:>12.6f} {chg_q:>+14.6f}"
            else:
                wall_q = float(wallet_quote)
                pos_q = float(pos_quote_amount)
                line = f"|   {self._quote_token:<8} {'N/A':>12} {wall_q:>12.6f} {pos_q:>12.6f} {'N/A':>14}"
            status.append(line + " " * (box_width - len(line) + 1) + "|")
        except Exception as e:
            line = f"| Balances: Error fetching ({e})"
            status.append(line + " " * (box_width - len(line) + 1) + "|")

        # === CLOSED POSITIONS SUMMARY ===
        status.append("|" + " " * box_width + "|")

        closed = [e for e in self.executors_info if e.is_done]

        # Separate LP positions from swaps
        closed_lp = [e for e in closed if getattr(e.config, "type", None) == "lp_executor"]
        closed_swaps = [e for e in closed if getattr(e.config, "type", None) == "order_executor"]

        # Count LP positions by side
        range_count = len([e for e in closed_lp if getattr(e.config, "side", None) == TradeType.RANGE])
        buy_count = len([e for e in closed_lp if getattr(e.config, "side", None) == TradeType.BUY])
        sell_count = len([e for e in closed_lp if getattr(e.config, "side", None) == TradeType.SELL])

        # Calculate fees from closed LP positions
        total_fees_base = Decimal("0")
        total_fees_quote = Decimal("0")

        for e in closed_lp:
            total_fees_base += Decimal(str(e.custom_info.get("base_fee", 0)))
            total_fees_quote += Decimal(str(e.custom_info.get("quote_fee", 0)))

        pool_price = self._pool_price or Decimal("0")
        total_fees_value = total_fees_base * pool_price + total_fees_quote

        line = f"| Closed Positions: {len(closed_lp)} (range:{range_count} buy:{buy_count} sell:{sell_count})"
        status.append(line + " " * (box_width - len(line) + 1) + "|")

        # Show swaps count if any
        if closed_swaps:
            swap_buy = len([e for e in closed_swaps if e.custom_info.get("side") == "BUY"])
            swap_sell = len([e for e in closed_swaps if e.custom_info.get("side") == "SELL"])
            line = f"| Swaps Executed: {len(closed_swaps)} (buy:{swap_buy} sell:{swap_sell})"
            status.append(line + " " * (box_width - len(line) + 1) + "|")

        fb = float(total_fees_base)
        fq = float(total_fees_quote)
        fv = float(total_fees_value)
        line = f"| Fees Collected: {fb:.6f} {self._base_token} + {fq:.6f} {self._quote_token} = {fv:.6f} {self._quote_token}"
        status.append(line + " " * (box_width - len(line) + 1) + "|")

        status.append("+" + "-" * box_width + "+")
        return status

    def _create_price_range_visualization(self, lower_price: Decimal, current_price: Decimal,
                                          upper_price: Decimal) -> str:
        """Create visual representation of price range with current price marker"""
        price_range = upper_price - lower_price
        if price_range == 0:
            return f"[{float(lower_price):.6f}] (zero width)"

        current_position = (current_price - lower_price) / price_range
        bar_width = 50
        current_pos = int(current_position * bar_width)

        range_bar = ['─'] * bar_width
        range_bar[0] = '├'
        range_bar[-1] = '┤'

        if current_pos < 0:
            marker_line = '● ' + ''.join(range_bar)
        elif current_pos >= bar_width:
            marker_line = ''.join(range_bar) + ' ●'
        else:
            range_bar[current_pos] = '●'
            marker_line = ''.join(range_bar)

        viz_lines = []
        viz_lines.append(marker_line)
        lower_str = f'{float(lower_price):.6f}'
        upper_str = f'{float(upper_price):.6f}'
        viz_lines.append(lower_str + ' ' * (bar_width - len(lower_str) - len(upper_str)) + upper_str)

        return '\n'.join(viz_lines)

    def _create_price_limits_visualization(
        self,
        current_price: Decimal,
        pos_lower: Optional[Decimal] = None,
        pos_upper: Optional[Decimal] = None,
        price_decimals: int = 8
    ) -> Optional[str]:
        """Create visualization of sell/buy price limits on unified scale."""
        viz_lines = []

        bar_width = 50

        # Collect all price points to determine unified scale
        prices = [current_price]
        if self.config.sell_price_min:
            prices.append(self.config.sell_price_min)
        if self.config.sell_price_max:
            prices.append(self.config.sell_price_max)
        if self.config.buy_price_min:
            prices.append(self.config.buy_price_min)
        if self.config.buy_price_max:
            prices.append(self.config.buy_price_max)
        if pos_lower:
            prices.append(pos_lower)
        if pos_upper:
            prices.append(pos_upper)

        scale_min = min(prices)
        scale_max = max(prices)
        scale_range = scale_max - scale_min

        if scale_range <= 0:
            return None

        def pos_to_idx(price: Decimal) -> int:
            return int((price - scale_min) / scale_range * (bar_width - 1))

        # Get position marker index
        price_idx = pos_to_idx(current_price)

        # Helper to create a range bar on unified scale with position marker
        def make_range_bar(range_min: Optional[Decimal], range_max: Optional[Decimal],
                           label: str, fill_char: str = '═', show_position: bool = False) -> str:
            if range_min is None or range_max is None:
                return ""

            bar = [' '] * bar_width
            start_idx = max(0, pos_to_idx(range_min))
            end_idx = min(bar_width - 1, pos_to_idx(range_max))

            # Fill the range
            for i in range(start_idx, end_idx + 1):
                bar[i] = fill_char
            # Mark boundaries
            if 0 <= start_idx < bar_width:
                bar[start_idx] = '['
            if 0 <= end_idx < bar_width:
                bar[end_idx] = ']'

            # Add position marker if requested
            if show_position and 0 <= price_idx < bar_width:
                bar[price_idx] = '●'

            return f"  {label}: {''.join(bar)}"

        # Build visualization with aligned bars
        viz_lines.append("Price Limits:")

        # Create labels with price ranges
        if self.config.sell_price_min and self.config.sell_price_max:
            s_min = float(self.config.sell_price_min)
            s_max = float(self.config.sell_price_max)
            sell_label = f"Sell [{s_min:.{price_decimals}f}-{s_max:.{price_decimals}f}]"
        else:
            sell_label = "Sell"
        if self.config.buy_price_min and self.config.buy_price_max:
            b_min = float(self.config.buy_price_min)
            b_max = float(self.config.buy_price_max)
            buy_label = f"Buy  [{b_min:.{price_decimals}f}-{b_max:.{price_decimals}f}]"
        else:
            buy_label = "Buy "

        # Find max label length for alignment
        max_label_len = max(len(sell_label), len(buy_label))

        # Sell range (with position marker)
        if self.config.sell_price_min and self.config.sell_price_max:
            viz_lines.append(make_range_bar(
                self.config.sell_price_min, self.config.sell_price_max,
                sell_label.ljust(max_label_len), '═', show_position=True
            ))
        else:
            viz_lines.append("  Sell: No limits set")

        # Buy range (with position marker)
        if self.config.buy_price_min and self.config.buy_price_max:
            viz_lines.append(make_range_bar(
                self.config.buy_price_min, self.config.buy_price_max,
                buy_label.ljust(max_label_len), '─', show_position=True
            ))
        else:
            viz_lines.append("  Buy : No limits set")

        # Scale line (aligned with bar start)
        min_str = f'{float(scale_min):.{price_decimals}f}'
        max_str = f'{float(scale_max):.{price_decimals}f}'
        label_padding = max_label_len + 4  # "  " prefix + ": " suffix
        viz_lines.append(f"{' ' * label_padding}{min_str}{' ' * (bar_width - len(min_str) - len(max_str))}{max_str}")

        return '\n'.join(viz_lines)
