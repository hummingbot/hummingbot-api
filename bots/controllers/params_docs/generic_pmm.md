# PMM (Pure Market Making) Controller Documentation

## General Description

The PMM (Pure Market Making) controller implements a sophisticated market making strategy that continuously places buy and sell limit orders around the current market price to profit from the bid-ask spread. This controller uses dynamic position management with configurable inventory targets and risk controls to maintain balanced exposure while capturing spread profits.

**Core Strategy Mechanics**: The controller maintains multiple order levels on both sides of the order book, adjusting order sizes based on current inventory levels through a skew mechanism. When inventory deviates from the target position, the controller automatically adjusts order sizes to encourage rebalancing - increasing buy orders when below target and sell orders when above target.

**Optimal Market Conditions**:
- Stable, range-bound markets with consistent volatility
- High trading volume with good liquidity
- Markets with natural mean reversion tendencies
- Periods of low directional momentum

**Challenging Conditions**:
- Strong trending markets (risk of adverse selection)
- Extreme volatility spikes or flash crashes
- Low liquidity environments with wide spreads
- Markets experiencing structural breaks or regime changes

**Risk Profile**: Medium to high risk depending on configuration. Primary risks include inventory risk from accumulating positions during trends, adverse selection from informed traders, and execution risk from rapid price movements.

## Parameters

### `connector_name`
- **Type**: `str`
- **Default**: `"binance"`
- **Description**: The exchange connector to use for trading

#### Value Impact Analysis:
- Different exchanges have varying fee structures, liquidity profiles, and API latencies
- Perpetual connectors (e.g., `binance_perpetual`) enable leverage trading
- Spot connectors (e.g., `binance`) are for unleveraged trading

### `trading_pair`
- **Type**: `str`  
- **Default**: `"BTC-FDUSD"`
- **Description**: The trading pair to make markets on

#### Value Impact Analysis:
- **FDUSD pairs on Binance** (BTC-FDUSD, ETH-FDUSD): **RECOMMENDED** - Zero maker/taker fees, ideal for market making
- Major pairs (BTC-USDT, ETH-USDT) typically have tighter spreads and higher competition but incur trading fees
- Altcoin pairs may offer wider spreads but higher volatility risk
- Stablecoin pairs (USDC-USDT) have minimal directional risk but tiny spreads
- **Fee Consideration**: On Binance, FDUSD pairs have 0% fees, making them significantly more profitable for high-frequency market making

### `portfolio_allocation`
- **Type**: `Decimal`
- **Default**: `0.05` (5%)
- **Range**: `[0.01, 1.0]`
- **Description**: Maximum percentage of total capital to allocate around mid-price

#### Value Impact Analysis:
- **Low Values** (`0.01-0.05`): Conservative exposure, suitable for testing or volatile markets
- **Medium Values** (`0.05-0.20`): Standard allocation for balanced risk/reward
- **High Values** (`0.20-1.0`): Aggressive allocation, higher profit potential but increased risk
- **Edge Cases**: Values above 0.5 may lead to insufficient reserves for rebalancing

#### Interaction Effects:
- Combines with `total_amount_quote` to determine actual order sizes
- Affects how quickly the bot can adjust to inventory imbalances

### `target_base_pct`
- **Type**: `Decimal`
- **Default**: `0.2` (20%)
- **Range**: `[0.0, 1.0]`
- **Description**: Target inventory level as percentage of total allocation

#### Value Impact Analysis:
- **Low Values** (`0.0-0.2`): Quote-heavy strategy, profits from upward price moves
- **Medium Values** (`0.3-0.7`): Balanced inventory, neutral market exposure
- **High Values** (`0.8-1.0`): Base-heavy strategy, profits from downward moves
- **Typical**: 0.5 for market-neutral approach

#### Interaction Effects:
- Works with `min_base_pct` and `max_base_pct` to define rebalancing boundaries
- Influences skew calculations for order sizing

### `min_base_pct` / `max_base_pct`
- **Type**: `Decimal`
- **Default**: `0.1` / `0.4`
- **Range**: `[0.0, 1.0]`
- **Description**: Inventory boundaries that trigger rebalancing behavior

#### Value Impact Analysis:
- **Tight Range** (`0.4-0.6`): Aggressive rebalancing, more frequent position adjustments
- **Medium Range** (`0.3-0.7`): Balanced approach, moderate rebalancing
- **Wide Range** (`0.1-0.9`): Tolerates large inventory swings, less rebalancing
- **Edge Cases**: Range too tight may cause excessive trading; too wide increases directional risk

#### Interaction Effects:
- When inventory hits boundaries, controller only places orders on one side
- Affects profitability in trending vs ranging markets

### `buy_spreads` / `sell_spreads`
- **Type**: `List[float]`
- **Default**: `[0.01, 0.02]`
- **Range**: `[0.0001, 0.10]` per spread
- **Description**: Distance from mid-price for each order level (as decimal percentage)

#### Value Impact Analysis:
- **Tight Spreads** (`0.0001-0.001`): 
  - More fills but smaller profit per trade
  - Higher risk of adverse selection
  - Suitable for liquid markets with low volatility
- **Medium Spreads** (`0.001-0.01`):
  - Balanced fill rate and profitability
  - Standard for most market conditions
- **Wide Spreads** (`0.01-0.10`):
  - Fewer fills but larger profit per trade
  - Better protection against adverse moves
  - Suitable for volatile or illiquid markets

#### Example Configurations:
```yaml
# Liquid market (BTC-FDUSD on Binance - zero fees)
buy_spreads: [0.0001, 0.0002, 0.0005, 0.0007]
sell_spreads: [0.0002, 0.0004, 0.0006, 0.0008]

# Volatile altcoin
buy_spreads: [0.005, 0.01, 0.015, 0.02]
sell_spreads: [0.005, 0.01, 0.015, 0.02]
```

### `buy_amounts_pct` / `sell_amounts_pct`
- **Type**: `List[Decimal]` or `None`
- **Default**: `None` (distributes equally)
- **Description**: Percentage allocation for each order level

#### Value Impact Analysis:
- **Equal Distribution** (`[1, 1, 1, 1]`): Same size for all levels
- **Front-Weighted** (`[2, 1.5, 1, 0.5]`): Larger orders near mid-price
- **Back-Weighted** (`[0.5, 1, 1.5, 2]`): Larger orders further from mid-price
- **Custom Patterns**: Design based on market microstructure

#### Example Configurations:
```yaml
# Aggressive near touch
buy_amounts_pct: [3, 2, 1, 1]

# Defensive depth building  
buy_amounts_pct: [1, 1, 2, 3]
```

### `executor_refresh_time`
- **Type**: `int` (seconds)
- **Default**: `300` (5 minutes)
- **Range**: `[10, 3600]`
- **Description**: Time before refreshing unfilled orders

#### Value Impact Analysis:
- **Fast Refresh** (`10-60s`): 
  - Rapid adjustment to price changes
  - Higher fees from cancellations
  - Better for volatile markets
- **Medium Refresh** (`60-300s`):
  - Balanced between responsiveness and fees
  - Standard for most conditions
- **Slow Refresh** (`300-3600s`):
  - Patient order placement
  - Lower fees
  - Risk of stale orders in fast markets

### `cooldown_time`
- **Type**: `int` (seconds)
- **Default**: `15`
- **Range**: `[0, 300]`
- **Description**: Wait time after a fill before replacing the order

#### Value Impact Analysis:
- **No Cooldown** (`0`): Immediate replacement, aggressive market making
- **Short Cooldown** (`5-15s`): Quick recovery, standard operation
- **Long Cooldown** (`30-300s`): Cautious approach, allows market to settle
- **Use Case**: Increase during news events or high volatility

### `leverage`
- **Type**: `int`
- **Default**: `20`
- **Range**: `[1, 125]`
- **Description**: Leverage multiplier for perpetual contracts (1 for spot)

#### Value Impact Analysis:
- **No Leverage** (`1`): Spot trading only, no liquidation risk
- **Low Leverage** (`2-5x`): Moderate capital efficiency
- **Medium Leverage** (`10-20x`): Standard for experienced traders
- **High Leverage** (`50-125x`): Extreme risk, small moves can liquidate
- **Risk Warning**: Higher leverage amplifies both profits and losses

### `position_mode`
- **Type**: `PositionMode`
- **Default**: `"HEDGE"`
- **Options**: `["HEDGE", "ONEWAY"]`
- **Description**: Position mode for perpetual contracts

#### Value Impact Analysis:
- **HEDGE Mode**: Can hold both long and short positions simultaneously
- **ONEWAY Mode**: Single direction position only
- **Use Case**: HEDGE mode useful for complex strategies; ONEWAY for simplicity

### `take_profit`
- **Type**: `Decimal` or `None`
- **Default**: `0.02` (2%)
- **Range**: `[0.001, 0.10]`
- **Description**: Take profit target for individual positions

#### Value Impact Analysis:
- **Tight TP** (`0.001-0.01`): Quick profits, high turnover
- **Medium TP** (`0.01-0.03`): Balanced approach
- **Wide TP** (`0.03-0.10`): Patient strategy, larger moves
- **None**: No position-level take profit

### `take_profit_order_type`
- **Type**: `OrderType`
- **Default**: `LIMIT_MAKER`
- **Options**: `[MARKET, LIMIT, LIMIT_MAKER]`
- **Description**: Order type for take profit execution

#### Value Impact Analysis:
- **MARKET**: Immediate execution, guarantees fill but may slip
- **LIMIT**: Precise price, may not fill
- **LIMIT_MAKER**: Post-only limit, earns maker fees

### `max_skew`
- **Type**: `Decimal`
- **Default**: `1.0`
- **Range**: `[0.0, 1.0]`
- **Description**: Maximum order size adjustment based on inventory (0=full skew, 1=no skew)

#### Value Impact Analysis:
- **No Skew** (`1.0`): Orders don't adjust with inventory
- **Moderate Skew** (`0.5-0.8`): Gradual size adjustments
- **Full Skew** (`0.0-0.3`): Aggressive inventory management
- **Effect**: Lower values mean stronger rebalancing pressure

### `global_take_profit` / `global_stop_loss`
- **Type**: `Decimal`
- **Default**: `0.02` / `0.05`
- **Range**: `[0.01, 0.20]`
- **Description**: Portfolio-level profit/loss triggers

#### Value Impact Analysis:
- **Tight Stops** (`0.01-0.03`): Quick exit, capital preservation
- **Medium Stops** (`0.03-0.10`): Standard risk management
- **Wide Stops** (`0.10-0.20`): Tolerates larger drawdowns
- **Action**: Triggers market sell of entire position when hit

### `total_amount_quote`
- **Type**: `Decimal`
- **Default**: `2000`
- **Description**: Total quote currency amount for position sizing

#### Value Impact Analysis:
- Determines absolute position sizes when combined with `portfolio_allocation`
- Should be set based on account balance and risk tolerance
- Actual deployed = `total_amount_quote * portfolio_allocation`

## Common Configurations

### Conservative Market Making
**Use Case**: Low risk tolerance, stable markets, learning the strategy

```yaml
controller_name: pmm
controller_type: generic
connector_name: binance
trading_pair: BTC-FDUSD  # Zero fees on Binance
portfolio_allocation: 0.025  # Only 2.5% allocation
total_amount_quote: 1000

# Wide spreads for safety
buy_spreads: [0.002, 0.004, 0.006]
sell_spreads: [0.002, 0.004, 0.006]
buy_amounts_pct: [1, 1, 1]
sell_amounts_pct: [1, 1, 1]

# Conservative inventory management
target_base_pct: 0.5
min_base_pct: 0.3
max_base_pct: 0.7
max_skew: 0.5

# Longer refresh for lower fees
executor_refresh_time: 600
cooldown_time: 30

# Risk controls
leverage: 1  # Spot only
take_profit: 0.01
global_take_profit: 0.02
global_stop_loss: 0.03
```

### Balanced Market Making
**Use Case**: Standard configuration for most market conditions

```yaml
controller_name: pmm
controller_type: generic
connector_name: binance
trading_pair: ETH-FDUSD  # Zero fees on Binance spot
portfolio_allocation: 0.05
total_amount_quote: 5000

# Moderate spreads
buy_spreads: [0.0005, 0.001, 0.002, 0.003]
sell_spreads: [0.0005, 0.001, 0.002, 0.003]
buy_amounts_pct: [1.5, 1.25, 1, 0.75]  # Front-weighted
sell_amounts_pct: [1.5, 1.25, 1, 0.75]

# Balanced inventory
target_base_pct: 0.5
min_base_pct: 0.2
max_base_pct: 0.8
max_skew: 0.7

# Standard timing
executor_refresh_time: 300
cooldown_time: 15

# Moderate leverage
leverage: 10
position_mode: HEDGE
take_profit: 0.02
take_profit_order_type: LIMIT_MAKER
global_take_profit: 0.05
global_stop_loss: 0.08
```

### Aggressive Scalping
**Use Case**: High volume, liquid markets, experienced traders

```yaml
controller_name: pmm
controller_type: generic
connector_name: binance_perpetual
trading_pair: BTC-USDT
portfolio_allocation: 0.1
total_amount_quote: 10000

# Tight spreads for maximum fills
buy_spreads: [0.0001, 0.0002, 0.0003, 0.0005, 0.0008]
sell_spreads: [0.0001, 0.0002, 0.0003, 0.0005, 0.0008]
buy_amounts_pct: [2, 1.5, 1, 0.5, 0.5]  # Heavy near touch
sell_amounts_pct: [2, 1.5, 1, 0.5, 0.5]

# Tight inventory control
target_base_pct: 0.5
min_base_pct: 0.4
max_base_pct: 0.6
max_skew: 0.3  # Strong rebalancing

# Fast refresh for responsiveness
executor_refresh_time: 60
cooldown_time: 5

# Higher leverage for capital efficiency
leverage: 20
position_mode: HEDGE
take_profit: 0.005  # Quick profits
take_profit_order_type: MARKET
global_take_profit: 0.03
global_stop_loss: 0.05
```

### Volatile Market Configuration
**Use Case**: High volatility periods, news events, low liquidity

```yaml
controller_name: pmm
controller_type: generic
connector_name: binance
trading_pair: DOGE-USDT
portfolio_allocation: 0.03
total_amount_quote: 2000

# Wide spreads for protection
buy_spreads: [0.01, 0.02, 0.03, 0.05]
sell_spreads: [0.01, 0.02, 0.03, 0.05]
buy_amounts_pct: [0.5, 1, 1.5, 2]  # Back-weighted for safety
sell_amounts_pct: [0.5, 1, 1.5, 2]

# Wide inventory tolerance
target_base_pct: 0.5
min_base_pct: 0.1
max_base_pct: 0.9
max_skew: 0.8

# Slower refresh to let market settle
executor_refresh_time: 900
cooldown_time: 60

# Conservative leverage
leverage: 2
take_profit: 0.05
global_take_profit: 0.1
global_stop_loss: 0.15
```

## Performance Tuning Guide

### Key Parameters for Optimization

1. **Spread Parameters** (`buy_spreads`, `sell_spreads`)
   - Primary driver of profitability vs fill rate
   - Adjust based on market volatility and competition
   - Monitor fill rates and adjust if too high/low

2. **Inventory Management** (`target_base_pct`, `min/max_base_pct`, `max_skew`)
   - Controls directional exposure
   - Tighter ranges for ranging markets
   - Wider ranges for trending markets

3. **Timing Parameters** (`executor_refresh_time`, `cooldown_time`)
   - Balance between responsiveness and transaction costs
   - Shorter times for volatile markets
   - Longer times for stable conditions

### Market Condition Adjustments

**High Volatility**:
- Increase spreads by 2-5x
- Reduce portfolio allocation by 50%
- Increase cooldown time to 30-60s
- Tighten global stop loss

**Low Liquidity**:
- Increase spreads to capture wider bid-ask
- Reduce order sizes (lower portfolio allocation)
- Increase executor refresh time
- Use back-weighted amount distributions

**Trending Market**:
- Adjust target_base_pct in trend direction
- Widen min/max boundaries
- Reduce max_skew for stronger rebalancing
- Consider reducing leverage

**Range-Bound Market**:
- Tighten spreads for more fills
- Increase portfolio allocation
- Use balanced or front-weighted distributions
- Standard refresh times

## Troubleshooting

### Orders Not Filling
- **Check minimum order size** for the trading pair
- **Reduce spreads** to get closer to market price
- **Verify** market is active and liquid
- **Increase** portfolio allocation if orders too small

### Excessive Inventory Accumulation
- **Reduce** max_skew to increase rebalancing pressure
- **Tighten** min/max_base_pct range
- **Review** spreads - may be too aggressive on one side
- **Check** for trending market conditions

### High Drawdown
- **Reduce** leverage immediately
- **Tighten** global_stop_loss parameter
- **Decrease** portfolio_allocation
- **Widen** spreads for better entry prices
- **Increase** cooldown_time after losses

### Frequent Order Cancellations
- **Increase** executor_refresh_time
- **Check** for API rate limits
- **Verify** network connection stability
- **Consider** wider spreads

## Best Practices

1. **Start Small**: Begin with 1-2% portfolio allocation and low/no leverage on real funds
2. **Use FDUSD Pairs on Binance**: Take advantage of zero-fee trading for BTC-FDUSD, ETH-FDUSD, and other FDUSD pairs
3. **Monitor Actively**: Watch performance for first 24-48 hours of new config
4. **Gradual Scaling**: Increase allocation/leverage gradually as confidence builds
5. **Risk Limits**: Always set global stop loss and take profit levels
6. **Market Research**: Understand the specific dynamics of your chosen trading pair
7. **Regular Reviews**: Analyze performance weekly and adjust parameters
8. **Diversification**: Consider running multiple instances on different pairs
9. **Fee Optimization**: Use zero-fee pairs (FDUSD on Binance) or exchanges with maker rebates
10. **Backup Plans**: Have exit strategy if market conditions change dramatically

## Additional Notes

- **FDUSD Advantage**: Binance offers 0% maker and taker fees on FDUSD pairs, making them ideal for PMM strategies
- PMM works best in liquid markets with consistent two-way flow
- Avoid during major news events unless specifically configured for volatility
- Consider time-of-day effects (Asian/European/US sessions)
- Some exchanges have special maker fee rebates that improve profitability (but FDUSD pairs are already free on Binance)
- Always ensure sufficient balance for potential position accumulation
- The controller automatically handles position sizing based on available balance
- Monitor the skew visualization in status to understand rebalancing behavior
- Start with real funds but small amounts - real market dynamics differ from simulations