# Hummingbot API Reference for AI Assistants

**Quick Start:** This API is accessible at `http://localhost:8000` with interactive docs at `http://localhost:8000/docs`.

## 🤖 MCP Tools (Recommended - Use These First!)

**For AI Assistants:** Before making direct API calls, check if an MCP tool exists for your task. MCP tools provide simplified, high-level access to common operations.

### Essential Setup & Connection

#### `configure_api_servers` - **ALWAYS RUN FIRST**
Configure connection to the Hummingbot API. Run this before using any other MCP tool.

```python
configure_api_servers(
    action="add",
    name="local",
    host="localhost",
    port=8000,
    username="admin",
    password="admin"
)
configure_api_servers(action="set_default", name="local")
```

**When to use:**
- Before any other MCP tool
- When you get connection errors
- After MCP server restarts

---

### Portfolio & Trading

#### `get_portfolio_overview` - **Unified Portfolio View**
Get complete portfolio across CEX, DEX, LP positions, and orders in one call.

```python
get_portfolio_overview(
    account_names=["master_account"],  # Optional filter
    connector_names=["binance", "solana-mainnet-beta"],  # Optional filter
    include_balances=True,
    include_perp_positions=True,
    include_lp_positions=True,
    include_active_orders=True,
    as_distribution=False  # Set True for percentage breakdown
)
```

**Use instead of:**
- `POST /portfolio/state`
- `POST /portfolio/distribution`
- `POST /trading/positions`
- `POST /trading/orders/active`

**When to use:**
- "Show me my portfolio"
- "What are my balances?"
- "Do I have any open positions?"

---

#### `place_order` - Place Exchange Orders
Execute buy/sell orders on CEX exchanges.

```python
place_order(
    connector_name="binance",
    trading_pair="BTC-USDT",
    trade_type="BUY",  # or "SELL"
    amount="$100",  # Use $ prefix for USD value, or specify base amount "0.001"
    order_type="MARKET",  # or "LIMIT"
    price="50000",  # Required for LIMIT orders
    account_name="master_account"
)
```

**Use instead of:** `POST /trading/orders`

---

#### `search_history` - Search Trading History
Search orders, perpetual positions, or CLMM positions.

```python
search_history(
    data_type="orders",  # or "perp_positions", "clmm_positions"
    account_names=["master_account"],
    connector_names=["binance"],
    trading_pairs=["BTC-USDT"],
    status="FILLED",  # Optional: OPEN, CLOSED, FILLED, CANCELED
    start_time=1609459200,  # Unix timestamp
    end_time=1609545600,
    limit=50
)
```

**Use instead of:**
- `POST /trading/orders/search`
- `POST /trading/positions`
- `POST /gateway/clmm/positions/search`

---

#### `set_account_position_mode_and_leverage` - Configure Perpetuals
Set position mode and leverage for perpetual trading.

```python
set_account_position_mode_and_leverage(
    account_name="master_account",
    connector_name="binance_perpetual",
    trading_pair="BTC-USDT",  # Required for leverage
    position_mode="HEDGE",  # or "ONE-WAY"
    leverage=10  # Optional
)
```

**Use instead of:**
- `POST /trading/{account_name}/{connector_name}/position-mode`
- `POST /trading/{account_name}/{connector_name}/leverage`

---

### Exchange Credentials & Setup

#### `setup_connector` - Add Exchange Credentials
Progressive setup flow for adding exchange API keys.

```python
# Step 1: List available exchanges
setup_connector()

# Step 2: Get required fields for specific exchange
setup_connector(connector="binance")

# Step 3: Select account (if needed)
# Step 4: Add credentials
setup_connector(
    connector="binance",
    credentials={
        "binance_api_key": "your_key",
        "binance_api_secret": "your_secret"
    },
    account="master_account"
)
```

**Use instead of:**
- `GET /connectors/`
- `GET /connectors/{connector_name}/config-map`
- `POST /accounts/add-credential/{account_name}/{connector_name}`

---

### Market Data

#### `get_prices` - Latest Market Prices
Get current prices for multiple trading pairs.

```python
get_prices(
    connector_name="binance",
    trading_pairs=["BTC-USDT", "ETH-USDT", "SOL-USDT"]
)
```

**Use instead of:** `POST /market-data/prices`

---

#### `get_candles` - Price History (OHLCV)
Get candlestick data for technical analysis.

```python
get_candles(
    connector_name="binance",
    trading_pair="BTC-USDT",
    interval="1h",  # "1m", "5m", "15m", "30m", "1h", "4h", "1d"
    days=30  # Days of history
)
```

**Use instead of:** `POST /market-data/historical-candles`

---

#### `get_funding_rate` - Perpetual Funding Rates
Get funding rates for perpetual contracts.

```python
get_funding_rate(
    connector_name="binance_perpetual",
    trading_pair="BTC-USDT"
)
```

**Use instead of:** `POST /market-data/funding-info`

---

#### `get_order_book` - Order Book Analysis
Get order book data with advanced queries.

```python
get_order_book(
    connector_name="binance",
    trading_pair="BTC-USDT",
    query_type="snapshot",  # "volume_for_price", "price_for_volume", etc.
    query_value=50000,  # Required for non-snapshot queries
    is_buy=True  # Required for non-snapshot queries
)
```

**Use instead of:**
- `POST /market-data/order-book`
- `POST /market-data/order-book/price-for-volume`
- `POST /market-data/order-book/volume-for-price`
- `POST /market-data/order-book/vwap-for-volume`

---

### Gateway (DEX Trading)

#### `manage_gateway_container` - Gateway Lifecycle
Start, stop, or check Gateway container status.

```python
# Start Gateway
manage_gateway_container(
    action="start",
    config={
        "passphrase": "admin",
        "image": "hummingbot/gateway:latest",
        "port": 15888
    }
)

# Check status
manage_gateway_container(action="get_status")

# View logs
manage_gateway_container(action="get_logs", tail=100)

# Restart
manage_gateway_container(action="restart")

# Stop
manage_gateway_container(action="stop")
```

**Use instead of:**
- `POST /gateway/start`
- `GET /gateway/status`
- `POST /gateway/stop`
- `POST /gateway/restart`
- `GET /gateway/logs`

---

#### `manage_gateway_config` - Configure DEX Resources
Manage chains, networks, tokens, connectors, pools, and wallets.

```python
# List supported chains
manage_gateway_config(resource_type="chains", action="list")

# List networks
manage_gateway_config(resource_type="networks", action="list")

# Get specific network
manage_gateway_config(
    resource_type="networks",
    action="get",
    network_id="solana-mainnet-beta"
)

# List tokens on network
manage_gateway_config(
    resource_type="tokens",
    action="list",
    network_id="solana-mainnet-beta",
    search="USDC"  # Optional search filter
)

# Add token
manage_gateway_config(
    resource_type="tokens",
    action="add",
    network_id="solana-mainnet-beta",
    token_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    token_symbol="USDC",
    token_decimals=6,
    token_name="USD Coin"
)

# Add wallet
manage_gateway_config(
    resource_type="wallets",
    action="add",
    chain="solana",
    private_key="your_private_key"
)

# List DEX connectors
manage_gateway_config(resource_type="connectors", action="list")

# List pools
manage_gateway_config(
    resource_type="pools",
    action="list",
    connector_name="meteora",
    network="mainnet-beta"
)
```

**Use instead of:**
- `GET /gateway/chains`
- `GET /gateway/networks`
- `GET /gateway/networks/{network_id}`
- `GET /gateway/networks/{network_id}/tokens`
- `POST /gateway/networks/{network_id}/tokens`
- `POST /accounts/gateway/add-wallet`
- `GET /gateway/connectors`
- `GET /gateway/pools`

---

#### `manage_gateway_swaps` - DEX Swaps
Quote and execute swaps on DEX routers (Jupiter, 0x).

```python
# Get quote
manage_gateway_swaps(
    action="quote",
    connector="jupiter",
    network="solana-mainnet-beta",
    trading_pair="SOL-USDC",
    side="BUY",  # or "SELL"
    amount="1.0",  # Amount of base token
    slippage_pct="1.0"
)

# Execute swap
manage_gateway_swaps(
    action="execute",
    connector="jupiter",
    network="solana-mainnet-beta",
    trading_pair="SOL-USDC",
    side="BUY",
    amount="1.0",
    slippage_pct="1.0",
    wallet_address="your_wallet_address"  # Optional
)

# Search swap history
manage_gateway_swaps(
    action="search",
    search_connector="jupiter",
    search_network="solana-mainnet-beta",
    status="CONFIRMED",  # SUBMITTED, CONFIRMED, FAILED
    limit=50
)

# Check transaction status
manage_gateway_swaps(
    action="get_status",
    transaction_hash="your_tx_hash"
)
```

**Use instead of:**
- `POST /gateway/swap/quote`
- `POST /gateway/swap/execute`
- `POST /gateway/swaps/search`
- `GET /gateway/swaps/{transaction_hash}/status`

---

#### `explore_gateway_clmm_pools` - Discover CLMM Pools
Browse concentrated liquidity pools.

```python
# List pools
explore_gateway_clmm_pools(
    action="list_pools",
    connector="meteora",
    page=0,
    limit=50,
    search_term="SOL",  # Optional filter
    sort_key="volume",  # volume, tvl, feetvlratio
    order_by="desc",
    include_unknown=True,
    detailed=False  # Set True for more columns
)

# Get specific pool info
explore_gateway_clmm_pools(
    action="get_pool_info",
    connector="meteora",
    network="solana-mainnet-beta",
    pool_address="pool_address_here"
)
```

**Use instead of:**
- `GET /gateway/clmm/pools`
- `GET /gateway/clmm/pool-info`

---

#### `manage_gateway_clmm_positions` - CLMM Liquidity Positions
Open, close, collect fees from concentrated liquidity positions.

```python
# Open position
manage_gateway_clmm_positions(
    action="open_position",
    connector="meteora",
    network="solana-mainnet-beta",
    pool_address="pool_address",
    lower_price="150",
    upper_price="250",
    base_token_amount="1.0",  # Optional
    quote_token_amount="200",  # Optional
    slippage_pct="1.0",
    wallet_address="your_wallet",  # Optional
    extra_params={"strategyType": 0}  # Connector-specific
)

# Get positions for wallet/pool
manage_gateway_clmm_positions(
    action="get_positions",
    connector="meteora",
    network="solana-mainnet-beta",
    pool_address="pool_address",
    wallet_address="your_wallet"
)

# Collect fees
manage_gateway_clmm_positions(
    action="collect_fees",
    connector="meteora",
    network="solana-mainnet-beta",
    position_address="position_nft_address",
    wallet_address="your_wallet"
)

# Close position
manage_gateway_clmm_positions(
    action="close_position",
    connector="meteora",
    network="solana-mainnet-beta",
    position_address="position_nft_address",
    wallet_address="your_wallet"
)
```

**Use instead of:**
- `POST /gateway/clmm/open`
- `POST /gateway/clmm/positions_owned`
- `POST /gateway/clmm/collect-fees`
- `POST /gateway/clmm/close`

---

### Bot Management

#### `explore_controllers` - Discover Trading Strategies
List and understand available trading controllers (strategies).

```python
# List all controllers
explore_controllers(action="list")

# List by type
explore_controllers(
    action="list",
    controller_type="directional_trading"  # or "market_making", "generic"
)

# Describe specific controller
explore_controllers(
    action="describe",
    controller_name="macd_bb_v1"
)

# Describe specific config
explore_controllers(
    action="describe",
    config_name="my_strategy_config"
)
```

**Use instead of:**
- `GET /controllers/`
- `GET /controllers/{controller_type}/{controller_name}`
- `GET /controllers/configs/`

---

#### `modify_controllers` - Create/Update Strategies
Create, update, or delete controller templates and configs.

```python
# Create controller config
modify_controllers(
    action="upsert",
    target="config",
    config_name="my_pmm_strategy",
    config_data={
        "controller_name": "macd_bb_v1",
        "controller_type": "directional_trading",
        # ... other config parameters
    }
)

# Update bot-specific config
modify_controllers(
    action="upsert",
    target="config",
    config_name="my_pmm_strategy",
    config_data={...},
    bot_name="my_bot",
    confirm_override=True
)

# Delete config
modify_controllers(
    action="delete",
    target="config",
    config_name="old_strategy"
)
```

**Use instead of:**
- `POST /controllers/configs/{config_name}`
- `PUT /controllers/configs/{config_name}`
- `DELETE /controllers/configs/{config_name}`
- `POST /controllers/{controller_type}/{controller_name}`

---

#### `deploy_bot_with_controllers` - Deploy Trading Bot
Deploy a bot with controller configurations.

```python
deploy_bot_with_controllers(
    bot_name="my_trading_bot",
    controllers_config=["strategy_config_1", "strategy_config_2"],
    account_name="master_account",
    max_global_drawdown_quote=1000,  # Optional stop-loss
    max_controller_drawdown_quote=500,  # Optional per-strategy stop
    image="hummingbot/hummingbot:latest"
)
```

**Use instead of:** `POST /bot-orchestration/deploy-v2-controllers`

---

#### `get_active_bots_status` - Monitor Running Bots
Get status of all active trading bots.

```python
get_active_bots_status()
```

**Returns:** Bot status, PnL, volume, latest logs, errors

**Use instead of:** `GET /bot-orchestration/status`

---

#### `get_bot_logs` - Detailed Bot Logs
Search and filter bot logs.

```python
get_bot_logs(
    bot_name="my_trading_bot",
    log_type="error",  # "error", "general", "all"
    limit=50,
    search_term="connection"  # Optional filter
)
```

**Use instead of:** `GET /bot-orchestration/{bot_name}/status` (for logs)

---

#### `manage_bot_execution` - Start/Stop Bots
Control bot and controller execution.

```python
# Stop entire bot permanently
manage_bot_execution(
    bot_name="my_trading_bot",
    action="stop_bot"
)

# Stop specific controllers
manage_bot_execution(
    bot_name="my_trading_bot",
    action="stop_controllers",
    controller_names=["strategy_1", "strategy_2"]
)

# Start/resume controllers
manage_bot_execution(
    bot_name="my_trading_bot",
    action="start_controllers",
    controller_names=["strategy_1"]
)
```

**Use instead of:**
- `POST /bot-orchestration/stop-bot`
- `POST /bot-orchestration/stop-and-archive-bot/{bot_name}`

---

## 📋 Direct API Endpoints (Use When MCP Tools Don't Exist)

**Authentication:** All endpoints require HTTP Basic Auth.

```bash
curl -u username:password http://localhost:8000/endpoint
```

---

### 🐳 Docker Management (`/docker`)

```
GET    /docker/running
GET    /docker/available-images/
GET    /docker/active-containers
GET    /docker/exited-containers
POST   /docker/pull-image/
GET    /docker/pull-status/
POST   /docker/clean-exited-containers
POST   /docker/start-container/{container_name}
POST   /docker/stop-container/{container_name}
POST   /docker/remove-container/{container_name}
```

**Use Cases:**
- Check if Docker daemon is running
- Pull latest Hummingbot images
- Manage container lifecycle
- Clean up exited containers

---

### 💳 Account Management (`/accounts`)

**MCP tools exist** for most operations. Use direct API only for:

```
GET    /accounts/                              # List all accounts
POST   /accounts/add-account                    # Create new account
POST   /accounts/delete-account                 # Remove account
GET    /accounts/{account_name}/credentials     # List credentials
```

**Note:** Use `setup_connector` MCP tool for adding credentials instead of:
- `POST /accounts/add-credential/{account_name}/{connector_name}`
- `POST /accounts/delete-credential/{account_name}/{connector_name}`

---

### 🔌 Connector Information (`/connectors`)

**MCP tool exists:** Use `setup_connector()` for progressive flow.

Direct API endpoints:
```
GET    /connectors/                                    # List all exchanges
GET    /connectors/{connector_name}/config-map         # Get required credentials
GET    /connectors/{connector_name}/order-types        # Supported order types
GET    /connectors/{connector_name}/trading-rules      # Min/max amounts, tick sizes
```

**Example:**
```bash
# Get Binance trading rules
curl -u admin:admin "http://localhost:8000/connectors/binance/trading-rules?trading_pairs=BTC-USDT,ETH-USDT"
```

---

### 📊 Portfolio Management (`/portfolio`)

**MCP tool exists:** Use `get_portfolio_overview()` instead of these:

```
POST   /portfolio/state                    # Current balances (use MCP tool!)
POST   /portfolio/distribution             # Token breakdown (use MCP tool!)
POST   /portfolio/accounts-distribution    # Account allocation (use MCP tool!)
POST   /portfolio/history                  # Historical portfolio values
```

**When to use direct API:**
- Need cursor-based pagination for portfolio history
- Building custom portfolio analytics

---

### 💹 Trading Operations (`/trading`)

**MCP tools exist** for most operations:
- `place_order()` for placing orders
- `search_history()` for order/trade history
- `get_portfolio_overview()` for active orders and positions
- `set_account_position_mode_and_leverage()` for perpetual settings

**Direct API only needed for:**

```
GET    /trading/{account_name}/{connector_name}/position-mode
       # Get current position mode (HEDGE/ONEWAY)
```

---

### 🤖 Bot Orchestration (`/bot-orchestration`)

**MCP tools exist:**
- `deploy_bot_with_controllers()` - Deploy bots
- `get_active_bots_status()` - Monitor bots
- `get_bot_logs()` - View logs
- `manage_bot_execution()` - Start/stop

**Direct API for advanced use:**

```
GET    /bot-orchestration/bot-runs                     # Bot run history
GET    /bot-orchestration/bot-runs/stats               # Aggregate stats
GET    /bot-orchestration/bot-runs/{bot_run_id}        # Specific run details
POST   /bot-orchestration/deploy-v2-script             # Deploy V2 scripts
POST   /bot-orchestration/start-bot                    # Start V1 bots
GET    /bot-orchestration/mqtt                         # MQTT status
GET    /bot-orchestration/{bot_name}/history           # Bot performance history
```

---

### 📋 Strategy Management

#### Controllers (`/controllers`)

**MCP tools:** `explore_controllers()`, `modify_controllers()`

**Direct API for:**
```
GET    /controllers/{controller_type}/{controller_name}/config/template
       # Get JSON template for config
POST   /controllers/{controller_type}/{controller_name}/config/validate
       # Validate config before deploying
```

#### Scripts (`/scripts`)

```
GET    /scripts/                                    # List available scripts
GET    /scripts/{script_name}                       # Get script code
POST   /scripts/{script_name}                       # Upload custom script
DELETE /scripts/{script_name}                       # Remove script
GET    /scripts/{script_name}/config/template       # Get config template
GET    /scripts/configs/                            # List script configs
POST   /scripts/configs/{config_name}               # Create config
DELETE /scripts/configs/{config_name}               # Delete config
```

---

### 📊 Market Data (`/market-data`)

**MCP tools exist:**
- `get_prices()` - Current prices
- `get_candles()` - OHLCV data
- `get_funding_rate()` - Funding rates
- `get_order_book()` - Order book analysis

**Direct API for real-time feeds:**

```
POST   /market-data/candles
       # Start persistent candle feed (WebSocket-like)
       Body: {
         "connector_name": "binance",
         "trading_pairs": ["BTC-USDT"],
         "intervals": ["1m", "5m"],
         "max_records": 1000
       }

GET    /market-data/active-feeds
       # List active real-time feeds

GET    /market-data/settings
       # Get market data configuration
```

---

### 🔄 Backtesting (`/backtesting`)

**No MCP tool.** Use direct API:

```
POST   /backtesting/run-backtesting
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

---

### 📈 Archived Bot Analytics (`/archived-bots`)

**No MCP tool.** Use direct API for analyzing stopped bots:

```
GET    /archived-bots/                               # List archived databases
GET    /archived-bots/{db_path}/status               # Bot configuration
GET    /archived-bots/{db_path}/summary              # Performance summary
GET    /archived-bots/{db_path}/performance          # Detailed metrics
GET    /archived-bots/{db_path}/orders               # Historical orders
GET    /archived-bots/{db_path}/trades               # Trade history
GET    /archived-bots/{db_path}/positions            # Position history
GET    /archived-bots/{db_path}/controllers          # Controller configs
GET    /archived-bots/{db_path}/executors            # Executor data
```

---

### 🌐 Gateway Endpoints (DEX & Blockchain Operations)

**MCP tools exist** for most Gateway operations. Use direct API only for specific needs.

#### Gateway Lifecycle (`/gateway`)

```
GET    /gateway/status              # Get Gateway status
POST   /gateway/start               # Start Gateway container
POST   /gateway/stop                # Stop Gateway container
POST   /gateway/restart             # Restart Gateway container
GET    /gateway/logs                # Get Gateway logs
```

**Note:** Use `manage_gateway_container` MCP tool instead of these endpoints.

---

#### Gateway Configuration (`/gateway`)

```
GET    /gateway/chains              # List supported blockchain chains
GET    /gateway/connectors          # List available DEX connectors
GET    /gateway/connectors/{connector_name}
       # Get specific connector configuration
POST   /gateway/connectors/{connector_name}
       # Update connector configuration

GET    /gateway/networks            # List all networks
GET    /gateway/networks/{network_id}
       # Get specific network config (e.g., "solana-mainnet-beta")
POST   /gateway/networks/{network_id}
       # Update network configuration

GET    /gateway/networks/{network_id}/tokens
       # List tokens available on network
POST   /gateway/networks/{network_id}/tokens
       # Add custom token to network
       Body: {
         "token_address": "token_contract_address",
         "token_symbol": "SYMBOL",
         "token_decimals": 18,
         "token_name": "Token Name"
       }
DELETE /gateway/networks/{network_id}/tokens/{token_address}
       # Remove token from network

GET    /gateway/pools               # List liquidity pools
POST   /gateway/pools               # Add custom pool
```

**Note:** Use `manage_gateway_config` MCP tool for easier configuration management.

---

### 💱 Gateway Swaps (`/gateway/swap`)

**MCP tool exists:** Use `manage_gateway_swaps()` for DEX trading.

```
POST   /gateway/swap/quote
       # Get swap quote with pricing and gas estimates
       Body: {
         "chain": "solana",
         "network": "mainnet-beta",
         "connector": "jupiter",
         "base": "SOL",
         "quote": "USDC",
         "amount": "1.0",
         "side": "BUY",
         "allowedSlippage": "1.0"
       }

POST   /gateway/swap/execute
       # Execute the swap transaction
       Body: {
         "chain": "solana",
         "network": "mainnet-beta",
         "connector": "jupiter",
         "address": "wallet_address",
         "base": "SOL",
         "quote": "USDC",
         "amount": "1.0",
         "side": "BUY",
         "allowedSlippage": "1.0"
       }

GET    /gateway/swaps/{transaction_hash}/status
       # Check transaction status

POST   /gateway/swaps/search
       # Search swap transaction history
       Body: {
         "chain": "solana",
         "network": "mainnet-beta",
         "connector": "jupiter",
         "address": "wallet_address",
         "status": "CONFIRMED",  # SUBMITTED, CONFIRMED, FAILED
         "start_time": 1609459200,
         "end_time": 1609545600,
         "limit": 50
       }

GET    /gateway/swaps/summary
       # Get aggregated swap statistics
```

---

### 🏊 Gateway CLMM (`/gateway/clmm`)

**MCP tools exist:**
- `explore_gateway_clmm_pools()` - Pool discovery
- `manage_gateway_clmm_positions()` - Position management

```
GET    /gateway/clmm/pools
       # List CLMM pools with filtering and sorting
       Query params: connector, page, limit, search, sort_key, order_by

GET    /gateway/clmm/pool-info
       # Get detailed info for specific pool
       Query params: chain, network, connector, token0, token1, fee

POST   /gateway/clmm/open
       # Open new concentrated liquidity position
       Body: {
         "chain": "solana",
         "network": "mainnet-beta",
         "connector": "meteora",
         "address": "wallet_address",
         "pool_address": "pool_address",
         "lower_price": "150.0",
         "upper_price": "250.0",
         "base_token_amount": "1.0",
         "quote_token_amount": "200.0",
         "slippage": "1.0"
       }

POST   /gateway/clmm/close
       # Close CLMM position (remove all liquidity)
       Body: {
         "chain": "solana",
         "network": "mainnet-beta",
         "connector": "meteora",
         "address": "wallet_address",
         "position_address": "position_nft_address"
       }

POST   /gateway/clmm/collect-fees
       # Collect accumulated fees from position
       Body: {
         "chain": "solana",
         "network": "mainnet-beta",
         "connector": "meteora",
         "address": "wallet_address",
         "position_address": "position_nft_address"
       }

POST   /gateway/clmm/positions_owned
       # Get all positions owned by wallet in specific pool
       Body: {
         "chain": "solana",
         "network": "mainnet-beta",
         "connector": "meteora",
         "address": "wallet_address",
         "pool_address": "pool_address"
       }

GET    /gateway/clmm/positions/{position_address}/events
       # Get event history for specific position

POST   /gateway/clmm/positions/search
       # Search CLMM positions with filters
       Body: {
         "chain": "solana",
         "network": "mainnet-beta",
         "connector": "meteora",
         "address": "wallet_address",
         "status": "OPEN",  # OPEN, CLOSED
         "start_time": 1609459200,
         "end_time": 1609545600
       }
```

---

## 🆘 Common Error Handling

### MCP Connection Lost

**Error:**
```
Error executing tool: ❌ Failed to connect to Hummingbot API
Connection failed after 3 attempts.
```

**Solution:** Reconnect immediately:
```python
configure_api_servers(action="add", name="local", host="localhost", port=8000, username="admin", password="admin")
configure_api_servers(action="set_default", name="local")
# Retry your operation
```

### Authentication Errors (401)

Check credentials in `.env` file match what you're using.

### Validation Errors (422)

Read the error detail - usually missing required parameters or invalid values.

### Resource Not Found (404)

- Bot doesn't exist
- Connector name misspelled
- Database path incorrect

---

## 💡 AI Assistant Tips

1. **Always use MCP tools first** - They handle complexity for you
2. **Start with `configure_api_servers`** - Establishes connection
3. **Use `get_portfolio_overview`** - Single call for complete portfolio
4. **Progressive disclosure** - Tools like `setup_connector` guide you step-by-step
5. **Check MCP tool errors** - Reconnect immediately if connection fails
6. **Read error messages** - They usually tell you exactly what's wrong

---

## 📚 Additional Resources

- **Interactive API Docs**: http://localhost:8000/docs (Swagger UI)
- **Setup Guides**: See CLAUDE.md, AGENTS.md, GEMINI.md
- **Architecture**: See README.md
- **Troubleshooting**: See README.md Troubleshooting section
