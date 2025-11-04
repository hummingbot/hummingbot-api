# Using Hummingbot API with Claude

This guide shows you how to interact with the Hummingbot API using Claude (claude.ai) and Claude Code (CLI).

## 🤖 Method 1: MCP Server (Recommended)

The Hummingbot MCP server provides natural language access to all API functionality through Claude Desktop or Claude Code.

### Claude Desktop Setup

1. **Enable MCP during Hummingbot API setup**:
   ```bash
   ./setup.sh  # Answer "y" to "Enable MCP server for AI assistant usage?"
   ```

2. **Configure Claude Desktop**:
   - Open (or create) `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
   - Or `%APPDATA%\Claude\claude_desktop_config.json` (Windows)

   Add this configuration:
   ```json
   {
     "mcpServers": {
       "hummingbot": {
         "command": "docker",
         "args": ["exec", "-i", "hummingbot-mcp", "mcp"]
       }
     }
   }
   ```

3. **Restart Claude Desktop**

4. **Start using natural language**:
   - "What are my current portfolio balances?"
   - "Show me active trading bots"
   - "Create a new PMM strategy for ETH-USDT on Binance"
   - "What's the performance of my bots this week?"

### Claude Code (CLI) Setup

1. **Add the MCP server**:
   ```bash
   claude mcp add --transport stdio hummingbot -- docker exec -i hummingbot-mcp mcp
   ```

2. **Use in your terminal**:
   ```bash
   # Claude Code automatically uses the MCP server
   # Just ask questions naturally in your terminal
   ```

3. **Manage the connection**:
   ```bash
   claude mcp list             # List configured servers
   claude mcp get hummingbot   # View server details
   claude mcp remove hummingbot  # Remove server
   ```

## 🔧 Method 2: Direct API Access (Fallback)

If MCP is unavailable, you can interact with the API directly. See @API_REFERENCE.md for the full endpoint list.

## API Reference

The API is accessible at `http://localhost:8000` with interactive Swagger docs at `http://localhost:8000/docs`.

Refer to @API_REFERENCE.md for the full set of endpoints.

### Authentication

All endpoints require HTTP Basic Authentication. Use the username and password configured during setup. 

See @env for the current environment variables.

Example:
```bash
curl -u username:password http://localhost:8000/endpoint
```

## Common Development Commands

Refer to the Installation & Setup section in @README.md for more information.

### Environment Setup
```bash
# First-time setup - creates Docker services and environment
chmod +x setup.sh
./setup.sh

# Install development environment (requires Conda)
make install

# Run in development mode with hot-reloading
./run.sh --dev

# Run in production mode (Docker container)
./run.sh
```

### Code Quality & Testing
```bash
# Format code (automatically enforced by pre-commit hooks)
black --line-length 130 .
isort --line-length 130 --profile black .

# Install pre-commit hooks
make install-pre-commit

# Access API documentation
# Visit http://localhost:8000/docs after starting the API
```

### Docker Operations
```bash
# Build Docker image
make build

# Deploy with Docker Compose
make deploy

# Check running containers
docker ps

# View container logs
docker logs hummingbot-api
```

## High-Level Architecture

### Core Service Architecture
The API follows a microservice pattern where each trading bot runs in its own Docker container, communicating through MQTT with the main API service.

**Key Components:**
1. **FastAPI Application** (`main.py`): Central API with lifespan management for background services
2. **Bot Orchestrator** (`services/bots_orchestrator.py`): Manages bot lifecycle - deployment, monitoring, and archival
3. **Docker Service** (`services/docker_service.py`): Wrapper around Docker SDK for container operations
4. **MQTT Manager** (`utils/mqtt_manager.py`): Handles real-time communication with bot instances
5. **Repository Pattern** (`database/`): Clean data access layer with async PostgreSQL operations

### Request Flow Example
1. User sends authenticated request to API endpoint
2. Router validates request and calls appropriate service
3. Service orchestrates operations (e.g., starting a bot involves Docker service + MQTT setup)
4. Bot containers publish updates via MQTT
5. API aggregates real-time data from MQTT and database

### Bot Instance Management
Each bot maintains isolated state in `/bots/instances/hummingbot-{name}/`:
- Configuration files in `conf/`
- SQLite database in `data/`
- Execution logs in `logs/`

The API never directly modifies bot files - all communication happens through MQTT commands.

### Authentication & Security
- HTTP Basic Auth for API access (configured in `.env`)
- Config password encrypts exchange credentials using Fernet
- Credentials stored encrypted in `/bots/credentials/`

### Database Schema
PostgreSQL stores aggregated data from all bots:
- `orders`: All order history with exchange info
- `trades`: Executed trades with fees
- `account_balances`: Periodic balance snapshots
- `positions`: Perpetual contract positions
- `funding_payments`: Funding payment history

### Real-time Data Flow
1. Bots publish state updates to MQTT topics
2. API subscribes to relevant topics
3. Services process updates and store in PostgreSQL
4. Clients can query aggregated data via REST endpoints

## Key Development Patterns

### Async-First Design
All database operations and external calls use async/await. When adding new features:
```python
async def your_function():
    async with get_db_session() as session:
        # Database operations
```

### Service Layer Pattern
Business logic lives in `/services`, not in routers. Routers should only handle HTTP concerns.

### Error Handling
The API uses FastAPI's exception handling. Services should raise clear exceptions that routers can catch.

### Configuration Management
All configuration uses Pydantic Settings (`config.py`). Environment variables override defaults.

## Important Considerations

### Bot State Synchronization
- Account balances update every `ACCOUNT_UPDATE_INTERVAL` minutes
- Real-time updates come from MQTT, historical data from database
- Always check both sources for complete picture

### Docker Container Lifecycle
- Starting a bot: Creates container, waits for MQTT connection
- Stopping a bot: Graceful shutdown, optional archival to S3/local
- Failed containers remain for debugging (clean with `/docker/clean-exited`)

### Market Data Feeds
- Feeds auto-cleanup after inactivity
- Each feed runs in a background task
- Memory management crucial for long-running feeds

### Performance Optimization
- Use pagination for large datasets
- Cursor-based pagination preferred over offset
- Background tasks for long operations (archival, bulk updates)

## Troubleshooting

### Common Errors

#### Password Verification File Missing
**Error**: `[Errno 2] No such file or directory: 'bots/credentials/master_account/.password_verification'`

**Cause**: This error occurs when trying to add credentials before running the initial setup.

**Solution**: Run `./setup.sh` to initialize the environment. This script:
- Creates necessary directory structures
- Sets up Docker services (PostgreSQL, MQTT broker)
- Initializes the master_account with required configuration files
- Creates the `.password_verification` file needed for credential encryption

**Prevention**: Always run `./setup.sh` before attempting to add exchange credentials or perform account operations.

## Common Workflows

### 1. Adding Exchange Credentials
1. List available connectors: `GET /connectors/`
   - Returns all supported exchanges (binance, coinbase, kraken, etc.)
2. Get required configuration fields: `GET /connectors/{connector_name}/config-map`
   - Returns which fields are needed (api_key, api_secret, etc.)
   - Shows field types and whether they're required
3. Gather credential values from the user
   - Ask the user to provide values for each required field
   - Ensure all required fields from step 2 are collected
4. Add credentials: `POST /accounts/add-credential/{account_name}/{connector_name}`
   - Provide the required fields from config-map
   - Credentials are encrypted and stored securely

Example workflow:
```bash
# 1. Check what connectors are available
# Why: First, I need to see which exchanges are supported by the API
GET /connectors/

# 2. Get config requirements for Binance
# Why: I need to know what credentials Binance requires so I can ask you for the right information
GET /connectors/binance/config-map
# Returns: {"binance_api_key": {"prompt": "Enter your Binance API key", "is_secure": true, "is_connect_key": true},
#           "binance_api_secret": {"prompt": "Enter your Binance API secret", "is_secure": true}}

# 3. Gather credentials from user
# Why: I need to collect your API credentials to connect to your Binance account
# Ask user: "Please provide your Binance API key"
# Ask user: "Please provide your Binance API secret"

# 4. Add credentials
# Why: Now I will securely store your credentials encrypted with your config password
POST /accounts/add-credential/my_account/binance
Body: {
  "binance_api_key": "your_api_key_here",
  "binance_api_secret": "your_api_secret_here"
}
```

#### XRPL Example:
```bash
# 1. Get XRPL config requirements
# Why: I need to check what configuration XRPL connector requires
GET /connectors/xrpl/config-map
# Returns: ["xrpl_secret_key", "wss_node_urls", "custom_markets", "max_request_per_minute"]

# 2. Gather XRPL credentials from user
# Why: I need to collect your XRPL wallet credentials and optional configuration
# Ask user: "Please provide your XRPL secret key"
# Ask user: "Please provide WebSocket node URLs (optional, defaults to public nodes)"
# Ask user: "Please provide custom markets configuration (optional)"
# Ask user: "Please provide max requests per minute (optional)"

# 3. Add XRPL credentials
# Why: Now I will securely store your XRPL credentials encrypted with your config password
POST /accounts/add-credential/my_account/xrpl
Body: {
  "xrpl_secret_key": "your_xrpl_secret_key_here",
  "wss_node_urls": ["wss://s1.ripple.com", "wss://s2.ripple.com"],  // optional
  "custom_markets": {},  // optional
  "max_request_per_minute": 300  // optional
}
```

### 2. Analyzing Portfolio
1. Get current portfolio state: `POST /portfolio/state`
   - Returns real-time balances across all accounts
   - Can filter by specific accounts or connectors
2. Retrieve historical data: `POST /portfolio/history`
   - Returns time-series portfolio values
   - Supports cursor-based pagination for large datasets
3. Analyze token distribution: `POST /portfolio/distribution`
   - Shows percentage allocation by token
   - Aggregates across all exchanges
4. Review account distribution: `POST /portfolio/accounts-distribution`
   - Shows percentage allocation by account
   - Useful for risk management

Example workflow:
```bash
# 1. Get current portfolio snapshot
# Why: I'm checking your current balances across selected accounts and exchanges
POST /portfolio/state
Body: {
  "account_names": ["trading_account", "savings_account"],
  "connectors": ["binance", "coinbase"]
}
# Returns: {"balances": [{"token": "BTC", "total": 0.5, "available": 0.4, "locked": 0.1, "usd_value": 25000}...]}

# 2. Get historical portfolio performance
# Why: I'm retrieving your portfolio history to analyze performance over time
POST /portfolio/history
Body: {
  "account_names": ["trading_account"],
  "limit": 100,
  "cursor": null
}
# Returns: {"data": [{"timestamp": 1234567890, "total_usd_value": 50000, "balances": {...}}...], "next_cursor": "..."}

# 3. Analyze token distribution
# Why: I'm calculating how your portfolio is distributed across different tokens
POST /portfolio/distribution
Body: {
  "account_names": ["trading_account", "savings_account"]
}
# Returns: {"BTC": {"amount": 0.5, "usd_value": 25000, "percentage": 50.0}, 
#           "ETH": {"amount": 10, "usd_value": 20000, "percentage": 40.0}...}

# 4. Check account distribution
# Why: I'm analyzing how your total portfolio value is spread across your different accounts
POST /portfolio/accounts-distribution
Body: {}  # No filter returns all accounts
# Returns: {"trading_account": {"usd_value": 40000, "percentage": 80.0},
#           "savings_account": {"usd_value": 10000, "percentage": 20.0}}
```

### 3. Fetching Market Data
1. Start real-time candle feed: `POST /market-data/candles`
   - Creates persistent websocket connection
   - Auto-cleanup after inactivity
2. Get current prices: `POST /market-data/prices`
   - Returns spot prices for multiple pairs
3. Analyze order book: `POST /market-data/order-book`
   - Returns bid/ask levels with depth
4. Calculate market metrics: Various order book analytics endpoints
   - Price impact for volume
   - VWAP calculations
   - Volume at price levels

Example workflow:
```bash
# 1. Start real-time candle feed
# Why: I'm establishing a real-time data feed to monitor price movements
POST /market-data/candles
Body: {
  "connector_name": "binance",
  "trading_pairs": ["BTC-USDT", "ETH-USDT"],
  "intervals": ["1m", "5m"],
  "max_records": 1000
}
# Returns: {"feed_id": "candles_binance_123", "status": "running"}

# 2. Get current prices
# Why: I need to check the current market prices before placing any orders
POST /market-data/prices
Body: {
  "connector_name": "binance",
  "trading_pairs": ["BTC-USDT", "ETH-USDT"]
}
# Returns: {"BTC-USDT": {"price": 50000.00, "timestamp": 1234567890},
#           "ETH-USDT": {"price": 3000.00, "timestamp": 1234567890}}

# 3. Get order book snapshot
# Why: I'm analyzing market depth to understand liquidity and potential price impact
POST /market-data/order-book
Body: {
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "depth": 20
}
# Returns: {"timestamp": 1234567890, 
#           "bids": [[49999.00, 0.5], [49998.00, 1.0]...],
#           "asks": [[50001.00, 0.3], [50002.00, 0.8]...]}

# 4. Calculate VWAP for large order
# Why: I'm calculating the average price you would pay if you execute a large order
POST /market-data/order-book/vwap-for-volume
Body: {
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "volume": 10,
  "side": "buy"
}
# Returns: {"vwap": 50015.50, "avg_price": 50015.50}
```

### 4. Executing Trades
1. Check connector capabilities: `GET /connectors/{connector_name}/order-types`
   - Returns supported order types (limit, market, stop-loss, etc.)
2. Get trading rules: `GET /connectors/{connector_name}/trading-rules`
   - Returns min/max order amounts, tick sizes, minimum notional values
3. Verify current price: `POST /market-data/prices`
   - Ensures order price is reasonable
4. Place order: `POST /trading/orders`
   - Must respect trading rules constraints
5. Monitor order status: `POST /trading/orders/active`
   - Track order execution progress
6. Cancel if needed: `POST /trading/{account_name}/{connector_name}/orders/{order_id}/cancel`

Example workflow:
```bash
# 1. Check supported order types
# Why: I need to verify what order types Binance supports before placing orders
GET /connectors/binance/order-types
# Returns: ["limit", "limit_maker", "market", "stop_loss_limit"]

# 2. Get trading rules for BTC-USDT
# Why: I'm checking minimum order sizes and price increments to ensure your order is valid
GET /connectors/binance/trading-rules?trading_pairs=BTC-USDT
# Returns: {"BTC-USDT": {"min_order_size": 0.00001, "max_order_size": 9000, 
#           "min_price_increment": 0.01, "min_base_amount_increment": 0.00001,
#           "min_notional_size": 5.0}}

# 3. Check current market price
# Why: I need to know the current price to place a competitive limit order
POST /market-data/prices
Body: {"connector_name": "binance", "trading_pairs": ["BTC-USDT"]}
# Returns: {"BTC-USDT": {"price": 50000.00, "timestamp": 1234567890}}

# 4. Place limit order
# Why: I'm placing your buy order slightly below market price to get a better fill
POST /trading/orders
Body: {
  "account_name": "trading_account",
  "connector_name": "binance",
  "trading_pair": "BTC-USDT",
  "order_type": "limit",
  "trade_type": "buy",
  "price": 49900.00,  # Below market for limit buy
  "amount": 0.001     # Total value: 49.90 USD (above min_notional_size)
}
# Returns: {"client_order_id": "HMBot-123456", "exchange_order_id": "BIN-789", "status": "open"}

# 5. Monitor active orders
# Why: I'm checking the status of your order to see if it has been filled
POST /trading/orders/active
Body: {
  "account_names": ["trading_account"],
  "connectors": ["binance"]
}
# Returns: {"data": [{"client_order_id": "HMBot-123456", "status": "open", "filled_amount": 0}...]}

# 6. Cancel order if needed
# Why: If the order hasn't filled and you want to cancel it, I can do that now
POST /trading/trading_account/binance/orders/HMBot-123456/cancel
# Returns: {"success": true, "exchange_order_id": "BIN-789"}
```

### 5. Orchestrating Bots
1. Create account and add credentials: See workflow 1
2. Choose strategy type:
   - V1 Scripts: Traditional Hummingbot scripts
   - V2 Scripts: Next-gen scripts with enhanced features
   - V2 Controllers: Advanced multi-strategy controllers
3. Get strategy configuration template: `GET /scripts/{script_name}/config-template`
4. Deploy bot with configuration: `POST /bot-orchestration/start-bot` or `POST /bot-orchestration/deploy-v2-script`
5. Monitor bot status: `GET /bot-orchestration/{bot_name}/status`
6. Review performance: `GET /bot-orchestration/{bot_name}/history`
7. Stop and archive when done: `POST /bot-orchestration/stop-and-archive-bot/{bot_name}`

Example workflow:
```bash
# 1. List available V2 scripts
# Why: I need to see what automated trading strategies are available
GET /scripts/
# Returns: ["v2_directional_rsi", "v2_bollinger_dca", "v2_macd_bb_v1"...]

# 2. Get configuration template
# Why: I'm checking what parameters the RSI strategy needs so I can configure it properly
GET /scripts/v2_directional_rsi/config-template
# Returns: {"script_name": "v2_directional_rsi", "config": {"connector": "", "trading_pair": "", "rsi_period": 14...}}

# 3. Deploy V2 script bot
# Why: I'm launching your automated RSI trading bot with your specified configuration
POST /bot-orchestration/deploy-v2-script
Body: {
  "bot_name": "rsi_bot_btc",
  "script": "v2_directional_rsi",
  "config": {
    "connector": "binance",
    "trading_pair": "BTC-USDT",
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "order_amount": 0.001
  }
}
# Returns: {"bot_name": "rsi_bot_btc", "status": "starting", "container_name": "hummingbot-rsi_bot_btc"}

# 4. Check bot status
# Why: I'm verifying that your bot is running properly and connected to the exchange
GET /bot-orchestration/rsi_bot_btc/status
# Returns: {"bot_name": "rsi_bot_btc", "status": "running", "mqtt_connected": true, 
#           "last_update": 1234567890, "active_orders": 1}

# 5. Get bot performance history
# Why: I'm retrieving your bot's trading performance to analyze its effectiveness
GET /bot-orchestration/rsi_bot_btc/history?start_time=1234567800&end_time=1234567890
# Returns: {"orders": [...], "trades": [...], "performance": {"total_pnl": 150.50, "win_rate": 0.65}}

# 6. Stop and archive bot
# Why: I'm stopping your bot and archiving its data for future analysis
POST /bot-orchestration/stop-and-archive-bot/rsi_bot_btc
# Returns: {"status": "stopped", "archive_path": "/bots/archived/rsi_bot_btc_20240704.tar.gz"}
```

## Error Codes

- `400`: Bad Request - Invalid parameters
- `401`: Unauthorized - Authentication required
- `404`: Not Found - Resource doesn't exist
- `422`: Unprocessable Entity - Validation error
- `500`: Internal Server Error - Server issue

## Rate Limiting

No built-in rate limiting. Consider implementing client-side throttling for production use.

## WebSocket Support

Not available. Use polling for real-time updates or integrate with MQTT broker directly for bot events.
