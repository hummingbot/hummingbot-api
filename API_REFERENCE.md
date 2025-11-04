# Hummingbot API Reference

This document provides a comprehensive reference for all API endpoints available in the Hummingbot API. The API is running at `http://localhost:8000` with interactive documentation at `http://localhost:8000/docs`.

## Authentication

All endpoints require HTTP Basic Authentication. Use the username and password configured during setup.

Example:
```bash
curl -u username:password http://localhost:8000/endpoint
```

## Common Response Patterns

### Paginated Responses
Many endpoints return paginated data with this structure:
```json
{
  "data": [...],
  "next_cursor": "string or null",
  "total": 100
}
```

### Error Responses
```json
{
  "detail": "Error message"
}
```

## API Endpoints by Category

### 🐳 Docker Management (`/docker`)

#### Check Docker Status
```
GET /docker/running
```
Returns whether Docker daemon is running.

#### List Available Images
```
GET /docker/available-images
```
Returns list of available Docker images.

#### Container Management
```
GET /docker/active-containers
GET /docker/exited-containers
POST /docker/clean-exited-containers
POST /docker/pull-image
POST /docker/containers/{container_name}/start
POST /docker/containers/{container_name}/stop
DELETE /docker/containers/{container_name}
GET /docker/containers/{container_name}/logs
POST /docker/archive-container/{container_name}
```

### 💳 Account Management (`/accounts`)

#### List Accounts
```
GET /accounts/
```
Returns list of all account names.

#### Account Operations
```
POST /accounts/add-account
Body: {"account_name": "string"}

POST /accounts/delete-account
Body: {"account_name": "string"}
```

#### Credential Management
```
GET /accounts/{account_name}/credentials
Returns list of configured connectors for account.

POST /accounts/add-credential/{account_name}/{connector_name}
Body: {"api_key": "string", "api_secret": "string", ...}

POST /accounts/delete-credential/{account_name}/{connector_name}
```

### 🔌 Connector Information (`/connectors`)

#### List Connectors
```
GET /connectors/
```

#### Connector Details
```
GET /connectors/{connector_name}/config-map
GET /connectors/{connector_name}/trading-rules?trading_pairs=BTC-USDT,ETH-USDT
GET /connectors/{connector_name}/order-types
```

### 📊 Portfolio Management (`/portfolio`)

#### Current Portfolio State
```
POST /portfolio/state
Body: {
  "account_names": ["account1", "account2"],  // optional
  "connectors": ["binance", "coinbase"]       // optional
}
```

#### Portfolio History
```
POST /portfolio/history
Body: {
  "account_names": ["account1"],
  "connectors": ["binance"],
  "limit": 100,
  "cursor": "previous_cursor"
}
```

#### Portfolio Analytics
```
POST /portfolio/distribution
Body: Same as state/history filters

POST /portfolio/accounts-distribution
Body: Same as state/history filters
```

### 💹 Trading Operations (`/trading`)

#### Place Order
```
POST /trading/orders
Body: {
  "account_name": "string",
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "order_type": "limit",
  "trade_type": "buy",
  "price": 50000,
  "amount": 0.001,
  "client_order_id": "optional_custom_id"
}
```

#### Cancel Order
```
POST /trading/{account_name}/{connector_name}/orders/{client_order_id}/cancel
```

#### Query Positions (Perpetuals)
```
POST /trading/positions
Body: {
  "account_names": ["account1"],
  "connectors": ["binance_perpetual"],
  "trading_pairs": ["BTC-USDT"],
  "limit": 50,
  "cursor": "previous_cursor"
}
```

#### Active Orders
```
POST /trading/orders/active
Body: {
  "account_names": ["account1"],
  "connectors": ["binance"],
  "trading_pairs": ["BTC-USDT", "ETH-USDT"],
  "limit": 100,
  "cursor": "previous_cursor"
}
```

#### Order History
```
POST /trading/orders/search
Body: {
  "account_names": ["account1"],
  "connectors": ["binance"],
  "trading_pairs": ["BTC-USDT"],
  "start_time": 1609459200,  // Unix timestamp
  "end_time": 1609545600,
  "limit": 100,
  "cursor": "previous_cursor"
}
```

#### Trade History
```
POST /trading/trades
Body: Same structure as orders/search
```

#### Funding Payments (Perpetuals)
```
POST /trading/funding-payments
Body: Same structure as orders/search
```

#### Position Mode Management
```
GET /trading/{account_name}/{connector_name}/position-mode
Returns: {"position_mode": "ONEWAY" or "HEDGE"}

POST /trading/{account_name}/{connector_name}/position-mode
Body: {"position_mode": "ONEWAY" or "HEDGE"}
```

#### Leverage Management
```
POST /trading/{account_name}/{connector_name}/leverage
Body: {
  "trading_pair": "BTC-USDT",
  "leverage": 10
}
```

### 🤖 Bot Orchestration (`/bot-orchestration`)

#### Bot Status
```
GET /bot-orchestration/status
Returns status of all active bots.

GET /bot-orchestration/{bot_name}/status
Returns specific bot status.

GET /bot-orchestration/mqtt
Returns MQTT connection status and discovered bots.
```

#### Bot History
```
GET /bot-orchestration/{bot_name}/history?start_time=1609459200&end_time=1609545600
```

#### Bot Lifecycle
```
POST /bot-orchestration/start-bot
Body: {
  "bot_name": "my_bot",
  "script": "pure_market_making_simple_dca_bollinger",
  "config_params": {...}
}

POST /bot-orchestration/stop-bot
Body: {"bot_name": "my_bot"}

POST /bot-orchestration/stop-and-archive-bot/{bot_name}
```

#### Deploy V2 Strategies
```
POST /bot-orchestration/deploy-v2-script
Body: {
  "bot_name": "my_v2_bot",
  "script": "v2_directional_rsi",
  "config": {...}
}

POST /bot-orchestration/deploy-v2-controllers
Body: {
  "bot_name": "my_controller_bot",
  "controller_type": "directional_trading",
  "controller_name": "macd_bb_v1",
  "config": [...controller configs...]
}
```

### 📊 Market Data (`/market-data`)

#### Real-time Candles
```
POST /market-data/candles
Body: {
  "connector_name": "binance",
  "trading_pairs": ["BTC-USDT", "ETH-USDT"],
  "intervals": ["1m", "5m", "1h"],
  "max_records": 1000
}
```

#### Historical Candles
```
POST /market-data/historical-candles
Body: {
  "connector_name": "binance",
  "trading_pairs": ["BTC-USDT"],
  "intervals": ["1h"],
  "start_time": 1609459200,
  "end_time": 1609545600
}
```

#### Price Data
```
POST /market-data/prices
Body: {
  "connector_name": "binance",
  "trading_pairs": ["BTC-USDT", "ETH-USDT"]
}
```

#### Funding Info (Perpetuals)
```
POST /market-data/funding-info
Body: {
  "connector_name": "binance_perpetual",
  "trading_pairs": ["BTC-USDT"]
}
```

#### Order Book
```
POST /market-data/order-book
Body: {
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "depth": 50
}
```

#### Order Book Analytics
```
POST /market-data/order-book/price-for-volume
Body: {
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "volume": 10,
  "side": "buy"  // or "sell"
}

POST /market-data/order-book/volume-for-price
Body: {
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "price": 50000,
  "side": "buy"
}

POST /market-data/order-book/vwap-for-volume
Body: {
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "volume": 10,
  "side": "buy"
}
```

#### Active Feeds
```
GET /market-data/active-feeds
```

### 📋 Strategy Management

#### Controllers (`/controllers`)
```
GET /controllers/?controller_type=directional_trading
GET /controllers/configs/
GET /controllers/configs/{config_id}
POST /controllers/configs/
PUT /controllers/configs/{config_id}
DELETE /controllers/configs/{config_id}
GET /controllers/{controller_type}/{controller_name}/config-template
```

#### Scripts (`/scripts`)
```
GET /scripts/
GET /scripts/configs/
GET /scripts/configs/{config_id}
POST /scripts/configs/
PUT /scripts/configs/{config_id}
DELETE /scripts/configs/{config_id}
GET /scripts/{script_name}/config-template
```

### 🔄 Backtesting (`/backtesting`)

```
POST /backtesting/run-backtesting
Body: {
  "config": {
    "controller_name": "directional_trading.macd_bb_v1",
    "controller_type": "directional_trading",
    "controller_config": [...],
    "start_time": 1609459200,
    "end_time": 1609545600,
    "backtesting_resolution": "1m",
    "trade_cost": 0.0006
  }
}
```

### 📈 Archived Bot Analytics (`/archived-bots`)

```
GET /archived-bots/
GET /archived-bots/{db_path}/status
GET /archived-bots/{db_path}/summary
GET /archived-bots/{db_path}/performance
GET /archived-bots/{db_path}/trades
GET /archived-bots/{db_path}/executors-config
GET /archived-bots/{db_path}/executors
GET /archived-bots/{db_path}/general-data/{table_name}
```

### 🌐 Gateway Management (`/gateway`)

Gateway provides access to decentralized exchanges (DEX) and blockchain operations.

#### Gateway Lifecycle

```
POST /gateway/start
Body: {
  "passphrase": "your-secure-passphrase",
  "dev_mode": true  // Set to false for production
}

GET /gateway/status
Returns current Gateway status and connection info.

POST /gateway/stop
Stops the Gateway service.

POST /gateway/restart
Restarts the Gateway service.

GET /gateway/logs
Returns recent Gateway logs.
```

#### Gateway Configuration

```
GET /gateway/connectors
Returns list of available DEX connectors.

GET /gateway/connectors/{connector_name}
Returns details for a specific connector.

GET /gateway/chains
Returns list of supported blockchain chains.

GET /gateway/networks
Returns list of available networks.

GET /gateway/networks/{network_id}
Returns details for a specific network.

GET /gateway/networks/{network_id}/tokens
Returns available tokens on a network.

GET /gateway/networks/{network_id}/tokens/{token_address}
Returns details for a specific token.
```

#### Wallet Management

```
POST /accounts/gateway/add-wallet
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "private_key": "your_private_key"
}

GET /accounts/gateway/wallets
Returns list of configured Gateway wallets.

GET /accounts/gateway/{chain}/{address}
Returns details for a specific wallet.
```

#### Swap Operations

```
POST /gateway/swap/quote
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "connector": "uniswap",
  "base": "WETH",
  "quote": "USDC",
  "amount": "1.0",
  "side": "BUY"
}
Returns swap quote including expected price and gas estimates.

POST /gateway/swap/execute
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "connector": "uniswap",
  "address": "wallet_address",
  "base": "WETH",
  "quote": "USDC",
  "amount": "1.0",
  "side": "BUY",
  "allowedSlippage": "1.0"
}
Executes the swap transaction.

GET /gateway/swaps/search
Returns history of swap transactions.

GET /gateway/swaps/summary
Returns swap transaction summary statistics.

GET /gateway/swaps/{transaction_hash}/status
Returns status of a specific swap transaction.
```

#### Liquidity Pool Management

```
GET /gateway/pools
Returns available liquidity pools.

GET /gateway/clmm/pools
Returns CLMM (Concentrated Liquidity Market Maker) pools.

POST /gateway/clmm/pool-info
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "connector": "uniswap",
  "token0": "WETH",
  "token1": "USDC",
  "fee": "MEDIUM"
}
Returns detailed pool information.
```

#### CLMM Position Management

```
POST /gateway/clmm/open
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "connector": "uniswap",
  "address": "wallet_address",
  "token0": "WETH",
  "token1": "USDC",
  "fee": "MEDIUM",
  "lowerPrice": "1800",
  "upperPrice": "2200",
  "amount0": "1.0",
  "amount1": "2000"
}
Opens a new CLMM position.

POST /gateway/clmm/close
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "connector": "uniswap",
  "address": "wallet_address",
  "position_id": "position_nft_id"
}
Closes an existing CLMM position.

POST /gateway/clmm/collect-fees
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "connector": "uniswap",
  "address": "wallet_address",
  "position_id": "position_nft_id"
}
Collects accumulated fees from a position.

GET /gateway/clmm/positions_owned
Returns all CLMM positions owned by the wallet.

POST /gateway/clmm/positions/search
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "connector": "uniswap",
  "address": "wallet_address"
}
Searches for CLMM positions with filters.

GET /gateway/clmm/positions/{position_address}/events
Returns events history for a specific position.
```

### ⛓️ Chain-Specific Endpoints

Gateway provides direct blockchain access for balance checking, gas estimation, and transaction polling.

#### Solana Chain (`/chains/solana`)

```
GET /chains/solana/status
Returns Solana network status and connection info.

GET /chains/solana/estimate-gas
Returns estimated gas/fees for Solana transactions.

POST /chains/solana/balances
Body: {
  "chain": "solana",
  "network": "mainnet-beta",
  "address": "wallet_address",
  "tokenSymbols": ["SOL", "USDC"]  // optional, returns all if empty
}
Returns SOL and SPL token balances for the wallet.

POST /chains/solana/poll
Body: {
  "chain": "solana",
  "network": "mainnet-beta",
  "txHash": "transaction_signature"
}
Polls for transaction status and confirmation.
```

#### Ethereum Chain (`/chains/ethereum`)

```
GET /chains/ethereum/status
Returns Ethereum network status and connection info.

GET /chains/ethereum/estimate-gas
Returns estimated gas fees for Ethereum transactions.

POST /chains/ethereum/balances
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "address": "wallet_address",
  "tokenSymbols": ["ETH", "USDC", "WETH"]  // optional
}
Returns ETH and ERC-20 token balances for the wallet.

POST /chains/ethereum/poll
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "txHash": "transaction_hash"
}
Polls for transaction status and receipt.

POST /chains/ethereum/allowances
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "address": "wallet_address",
  "spender": "spender_address",
  "tokenSymbols": ["USDC", "WETH"]
}
Returns current token allowances for a spender.

POST /chains/ethereum/approve
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "address": "wallet_address",
  "spender": "spender_address",
  "token": "USDC",
  "amount": "1000"  // optional, max if not specified
}
Approves token spending for a contract.

POST /chains/ethereum/wrap
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "address": "wallet_address",
  "amount": "1.0"
}
Wraps ETH to WETH.

POST /chains/ethereum/unwrap
Body: {
  "chain": "ethereum",
  "network": "mainnet",
  "address": "wallet_address",
  "amount": "1.0"
}
Unwraps WETH to ETH.
```
