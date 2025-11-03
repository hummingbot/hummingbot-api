# Controller Configuration Documentation Template

## Overview

2-3 sentence description covering: strategy type, core logic, optimal market conditions, and main risks.

## Parameters

Each parameter should be documented with the following structure:

### `parameter_name`: **Type** = `type` | **Default** = `value` | **Range** = `[min, max]` | Brief description

#### Impact:
- **Low** (`range`): behavior and implications
- **High** (`range`): behavior and implications

#### Interactions:
Related params and how they affect behavior together (optional, only if significant interactions exist)

## Common Configurations

### Conservative
Low risk, suitable for beginners or stable markets
```yaml
controller_name: example_name
connector_name: EXCHANGE  # e.g., binance
trading_pair: PAIR  # e.g., BTC-USDT
param1: value  # inline comment explaining why
param2: value
```

### Balanced
Standard setup for most conditions
```yaml
controller_name: example_name
connector_name: EXCHANGE
trading_pair: PAIR
param1: value
param2: value
```

### Aggressive
Higher risk/reward for experienced traders
```yaml
controller_name: example_name
connector_name: EXCHANGE
trading_pair: PAIR
param1: value
param2: value
```

## Tuning by Market Condition

- **High Volatility**: Adjust params X, Y
- **Low Liquidity**: Adjust params A, B
- **Trending**: Adjust params C, D

## Best Practices

- Start with conservative settings and small position sizes
- Monitor performance before scaling up
- Set appropriate stop losses and position limits

## Additional Notes

Optional: version info, exchange-specific notes, or related docs