# Hummingbot API

**The central hub for running Hummingbot trading bots - now with AI assistant integration via MCP (Model Context Protocol).**

A comprehensive RESTful API framework for managing trading operations across multiple exchanges. The Hummingbot API provides a centralized platform to aggregate all your trading functionalities, from basic account management to sophisticated automated trading strategies.

## 🚀 Quick Start

Run the setup script to deploy the Hummingbot API platform:

```bash
git clone https://github.com/hummingbot/hummingbot-api.git
cd hummingbot-api
chmod +x setup.sh
./setup.sh
```

### Setup Process

The script will prompt you for:

1. **Credentials** (required):
   - Config password (for encrypting bot credentials)
   - API username and password

2. **Optional Services**:
   - **MCP server**: For AI assistant integration (Claude, ChatGPT, Gemini)
   - **Dashboard**: For web-based visual interface

3. **Gateway**: Optional passphrase for DEX trading

### What Gets Installed

**Core services** (always installed):
- ✅ **Hummingbot API** (port 8000) - REST API backend
- ✅ **PostgreSQL** - Database for trading data
- ✅ **EMQX** - Message broker for real-time communication
- ✅ **Swagger UI** (port 8000/docs) - API documentation

**Optional services** (enable during setup):
- 🤖 **MCP Server** - For AI assistant integration
- 📊 **Dashboard** (port 8501) - Web interface

### After Setup

**1. Access Swagger UI (Default)**

The API documentation is immediately available:
- URL: http://localhost:8000/docs
- Use the username/password you configured
- Test all API endpoints directly

**2. Connect AI Assistant (If MCP Enabled)**

If you enabled MCP, follow these steps:

**Claude Desktop:**
1. Install from [https://claude.ai/download](https://claude.ai/download)
2. Add to your config file:
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

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
3. Restart Claude Desktop
4. Try: "Show me my portfolio balances"

**3. Access Dashboard (If Enabled)**

If you enabled Dashboard during setup:
- URL: http://localhost:8501
- Use the same username/password from setup

## What is Hummingbot API?

The Hummingbot API is designed to be your central hub for trading operations, offering:

- **🤖 AI Assistant Integration**: Control your trading with natural language via MCP (Claude, ChatGPT, Gemini)
- **Multi-Exchange Account Management**: Create and manage multiple trading accounts across different exchanges
- **Portfolio Monitoring**: Real-time balance tracking and portfolio distribution analysis
- **Trade Execution**: Execute trades, manage orders, and monitor positions across all your accounts
- **Automated Trading**: Deploy and control Hummingbot instances with automated strategies
- **Strategy Management**: Add, configure, and manage trading strategies in real-time
- **Complete Flexibility**: Build any trading product on top of this robust API framework

## 🎯 Ways to Interact with Hummingbot API

Choose the method that best fits your workflow:

### 1. 🔧 Swagger UI - API Documentation (Default)
**Interactive REST API documentation and testing**

- **Best for**: Developers and power users who want full control
- **Advantages**:
  - Complete API access - all endpoints available
  - Direct endpoint testing
  - Integration development
  - No additional setup required
- **Setup**: Automatically available after running setup
- **Access**: http://localhost:8000/docs

### 2. 🤖 MCP - AI Assistant (Optional)
**Natural language trading commands through Claude, ChatGPT, or Gemini**

- **Best for**: Users who prefer conversational interaction
- **Advantages**:
  - Natural language commands
  - Full access to all API features
  - Contextual help and explanations
  - Complex multi-step operations made simple
- **Setup**: Answer "y" when prompted during setup, then connect your AI assistant
- **Example**: "Show me my best performing strategies this week"

### 3. 📊 Dashboard - Web Interface (Optional)
**Visual interface for common operations**

- **Best for**: Users who prefer graphical interfaces
- **Advantages**:
  - Intuitive visual workflows
  - Real-time charts and graphs
  - Quick access to common tasks
- **Limitations**: Not all API functions available (focused on core features)
- **Setup**: Answer "y" when prompted during setup
- **Access**: http://localhost:8501

Whether you're building a trading dashboard, implementing algorithmic strategies, or creating a comprehensive trading platform, the Hummingbot API provides all the tools you need.

## 🔌 Setting Up MCP with Claude Code

If you're using Claude Code (the CLI tool), you can connect to the Hummingbot MCP server directly from your development environment.

### Quick Setup

1. **Enable MCP during setup** (if not already done):
   ```bash
   ./setup.sh  # Answer "y" to "Enable MCP server for AI assistant usage?"
   ```

2. **Add the MCP server to Claude Code**:
   ```bash
   claude mcp add --transport stdio hummingbot -- docker exec -i hummingbot-mcp mcp
   ```

   This configures Claude Code to communicate with the Hummingbot MCP server running in Docker.

3. **Start using Hummingbot in Claude Code**:
   - Open your terminal with Claude Code
   - Use natural language commands to interact with your trading operations:
     ```
     "What are my current portfolio balances?"
     "Show me active trading bots"
     "Create a new market making strategy for ETH-USDT"
     ```

### Configuration File

The command above automatically creates/updates `.mcp.json` in your project root:

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

### Managing the Connection

**List configured MCP servers:**
```bash
claude mcp list
```

**View server details:**
```bash
claude mcp get hummingbot
```

**Remove the server:**
```bash
claude mcp remove hummingbot
```

### Prerequisites

- Claude Code CLI installed (see [Claude Code documentation](https://docs.claude.com/en/docs/claude-code))
- MCP service enabled during Hummingbot API setup
- Docker running with `hummingbot-mcp` container active

### Verify Setup

Check that the MCP container is running:
```bash
docker ps | grep hummingbot-mcp
```

If the container isn't running, re-run setup with MCP enabled:
```bash
./setup.sh  # Answer "y" to MCP prompt
```

## 🌐 Gateway Setup (For DEX Trading)

Gateway is required for decentralized exchange (DEX) trading. The Hummingbot API can manage Gateway containers for you - no separate installation needed!

### Option 1: Using Swagger UI (API)

1. **Access Swagger UI**: http://localhost:8000/docs
2. **Navigate to Gateway endpoints**: Look for `/manage-gateway` or similar endpoints
3. **Start Gateway**:
   ```json
   POST /manage-gateway
   {
     "action": "start",
     "passphrase": "your-secure-passphrase",
     "dev_mode": true
   }
   ```

The API automatically handles OS-specific networking:
- **macOS/Windows**: Uses `host.docker.internal` to connect to the API
- **Linux**: Uses appropriate network configuration

### Option 2: Using MCP AI Assistant

If you enabled MCP during setup, you can manage Gateway with natural language:

**Example commands:**
- "Start Gateway in development mode with passphrase 'admin'"
- "Check Gateway status"
- "Stop the Gateway container"
- "Restart Gateway with a new passphrase"

The `manage_gateway_container` MCP tool will:
- Pull the Gateway Docker image if needed
- Start the container with proper configuration
- Configure networking based on your OS
- Report Gateway status and connection info

### Verify Gateway is Running

**Check container status:**
```bash
docker ps | grep gateway
```

**View Gateway logs:**
```bash
docker logs gateway -f
```

**Test Gateway API** (dev mode only):
```bash
curl http://localhost:15888/
```

### Gateway Access

Once running, Gateway will be available at:
- **Development mode**: `http://localhost:15888`
- **Production mode**: `https://localhost:15888` (requires certificates)
- **API Documentation**: `http://localhost:15888/docs` (dev mode only)

### Troubleshooting

**Gateway won't start:**
- Ensure Docker is running
- Check if port 15888 is available
- Review logs: `docker logs gateway`

**Connection issues:**
- Verify Gateway URL in your `.env` file
- macOS/Windows users: Ensure `host.docker.internal` is accessible
- Linux users: Check network configuration

## 🐳 Docker Compose Architecture

The Hummingbot API uses Docker Compose to orchestrate multiple services into a complete trading platform:

### Services Overview

```yaml
services:
  # dashboard:      # Optional - Web UI (enable during setup or uncomment manually)
  hummingbot-api:   # Core FastAPI backend (port 8000) - Always installed
  emqx:            # MQTT message broker (port 1883) - Always installed
  postgres:        # PostgreSQL database (port 5432) - Always installed
```

### Network Configuration

All services communicate via the `emqx-bridge` Docker network:
- **Internal communication**: Services reference each other by container name (e.g., `hummingbot-api:8000`)
- **External access**: Exposed ports allow access from your host machine

### Environment Variables

The setup script creates a `.env` file with all necessary configuration:

```bash
# Security
USERNAME=admin                    # API authentication username
PASSWORD=admin                    # API authentication password
CONFIG_PASSWORD=admin             # Bot credentials encryption key

# Services (auto-configured)
BROKER_HOST=emqx
DATABASE_URL=postgresql+asyncpg://hbot:hummingbot-api@postgres:5432/hummingbot_api
```

### Persistent Storage

Docker volumes ensure data persistence:
- `postgres-data`: Trading data and bot performance
- `emqx-data`, `emqx-log`, `emqx-etc`: Message broker state

## System Dependencies

The platform includes these essential services:

### 1. PostgreSQL Database
Stores all trading data including:
- Orders and trade history
- Account states and balances
- Positions and funding payments
- Performance metrics

**Note:** The database is automatically initialized using environment variables. The included `init-db.sql` serves as a safety net.

### 2. EMQX Message Broker
Enables real-time communication with trading bots:
- Receives live updates from running bots
- Sends commands to control bot execution
- Handles real-time data streaming

## Installation & Setup

### Prerequisites
- Docker and Docker Compose installed
- Git for cloning the repository

### Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/hummingbot/hummingbot-api.git
   cd hummingbot-api
   ```

2. **Make setup script executable and run it**
   ```bash
   chmod +x setup.sh
   ./setup.sh
   ```

3. **Configure your environment**
   During setup, you'll configure several important variables:

   - **Config Password**: Used to encrypt and hash API keys and credentials for security
   - **Username & Password**: Basic authentication credentials for API access (used by dashboards and other systems)
   - **Additional configurations**: Available in the `.env` file including:
     - Broker configuration (EMQX settings)
     - Database URL
     - Market data cleanup settings
     - AWS S3 configuration (experimental)
     - Banned tokens list (for delisted tokens)

4. **Set up monitoring (Production recommended)**
   For production deployments, add observability through Logfire:
   ```bash
   export LOGFIRE_TOKEN=your_token_here
   ```
   Learn more: [Logfire Documentation](https://logfire.pydantic.dev/docs/)

After running `setup.sh`, the required Docker images (EMQX, PostgreSQL, and Hummingbot) will be running and ready.

## Running the API

You have two deployment options depending on your use case:

### For Users (Production/Simple Deployment)
```bash
./run.sh
```
This runs the API in a Docker container - simple and isolated.

### For Developers (Development Environment)
1. **Install Conda** (if not already installed)
2. **Set up the development environment**
   ```bash
   make install
   ```
   This creates a Conda environment with all dependencies.

3. **Run in development mode**
   ```bash
   ./run.sh --dev
   ```
   This starts the API from source with hot-reloading enabled.

## 🤖 MCP AI Assistant Integration

### Claude Desktop (Recommended)

1. **Install Claude Desktop**
   - Download from [https://claude.ai/download](https://claude.ai/download)

2. **Configure the MCP Server**
   - Open (or create) your Claude Desktop config file:
     - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
     - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

3. **Add the Hummingbot MCP configuration:**
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

4. **Restart Claude Desktop**

5. **Start using Hummingbot with natural language:**
   - "What are my current portfolio balances across all exchanges?"
   - "Show me my open orders on Binance"
   - "Create a PMM strategy for SOL-USDT on Kraken"
   - "What's the current spread for BTC-USDT on multiple exchanges?"
   - "Start Gateway in development mode"

### ChatGPT / OpenAI

1. **Install the OpenAI CLI** (if available in your region)
   - Follow OpenAI's official MCP setup guide

2. **Configure the MCP server** similar to Claude Desktop:
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

### Google Gemini

1. **Install Gemini CLI** (if available)
   - Refer to Google's MCP integration documentation

2. **Add Hummingbot MCP server** to your Gemini configuration

### Available MCP Capabilities

Once connected, your AI assistant can:
- 📊 **Portfolio Management**: View balances, positions, and P&L across exchanges
- 📈 **Market Data**: Get real-time prices, orderbook depth, and funding rates
- 🤖 **Bot Control**: Create, start, stop, and monitor trading bots
- 📋 **Order Management**: Place, cancel, and track orders
- 🔍 **Performance Analytics**: Analyze trading performance and statistics
- ⚙️ **Strategy Configuration**: Create and modify trading strategies
- 🌐 **Gateway Management**: Start, stop, and configure the Gateway container for DEX trading

## Getting Started (Alternative Methods)

Once the API is running, you can also access it directly:

### Option 1: Web Dashboard
1. **Access the Dashboard**: Go to `http://localhost:8501`
2. **Login**: Use the username and password you configured during setup
3. **Explore**: Navigate through the visual interface

### Option 2: Swagger UI (API Documentation)
1. **Visit the API Documentation**: Go to `http://localhost:8000/docs`
2. **Authenticate**: Use the username and password you configured during setup
3. **Test endpoints**: Use the Swagger interface to test API functionality

## API Overview

The Hummingbot API is organized into several functional routers:

### 🐳 Docker Management (`/docker`)
- Check Docker daemon status and health
- Pull new Docker images with async support
- Start, stop, and remove containers
- Monitor active and exited containers
- Clean up exited containers
- Archive container data locally or to S3
- Track image pull status and progress

### 💳 Account Management (`/accounts`)
- Create and delete trading accounts
- Add/remove exchange credentials
- List available credentials per account
- Basic account configuration

### 🔌 Connector Discovery (`/connectors`)
**Provides exchange connector information and configuration**
- List available exchange connectors
- Get connector configuration requirements
- Retrieve trading rules and constraints
- Query supported order types per connector

### 📊 Portfolio Management (`/portfolio`)
**Centralized portfolio tracking and analytics**
- **Real-time Portfolio State**: Current balances across all accounts
- **Portfolio History**: Time-series data with cursor-based pagination
- **Token Distribution**: Aggregate holdings by token across exchanges
- **Account Distribution**: Percentage-based portfolio allocation analysis
- **Advanced Filtering**: Filter by account names and connectors

### 💹 Trading Operations (`/trading`)
**Enhanced with POST-based filtering and comprehensive order/trade management**
- **Order Placement**: Execute trades with advanced order types
- **Order Cancellation**: Cancel specific orders by ID
- **Position Tracking**: Real-time perpetual positions with PnL data
- **Active Orders**: Live order monitoring from connector in-flight orders
- **Order History**: Paginated historical orders with advanced filtering
- **Trade History**: Complete execution records with filtering
- **Funding Payments**: Historical funding payment tracking for perpetuals
- **Position Modes**: Configure HEDGE/ONEWAY modes for perpetual trading
- **Leverage Management**: Set and adjust leverage per trading pair

### 🤖 Bot Orchestration (`/bot-orchestration`)
- Monitor bot status and MQTT connectivity
- Deploy V2 scripts and controllers
- Start/stop bots with configurable parameters
- Stop and archive bots with background task support
- Retrieve bot performance history
- Real-time bot status monitoring

### 📋 Strategy Management
- **Controllers** (`/controllers`): Manage V2 strategy controllers
  - CRUD operations on controller files
  - Controller configuration management
  - Bot-specific controller configurations
  - Template retrieval for new configs
- **Scripts** (`/scripts`): Handle traditional Hummingbot scripts
  - CRUD operations on script files
  - Script configuration management
  - Configuration templates

### 📊 Market Data (`/market-data`)
**Professional market data analysis and real-time feeds**
- **Price Discovery**: Real-time prices, funding rates, mark/index prices
- **Candle Data**: Real-time and historical candles with multiple intervals
- **Order Book Analysis**: 
  - Live order book snapshots
  - Price impact calculations
  - Volume queries at specific price levels
  - VWAP (Volume-Weighted Average Price) calculations
- **Feed Management**: Active feed monitoring with automatic cleanup

### 🔄 Backtesting (`/backtesting`)
- Run strategy backtests against historical data
- Support for controller configurations
- Customizable trade costs and resolution

### 📈 Archived Bot Analytics (`/archived-bots`)
**Comprehensive analysis of stopped bot performance**
- List and discover archived bot databases
- Performance metrics and trade analysis
- Historical order and trade retrieval
- Position and executor data extraction
- Controller configuration recovery
- Support for both V1 and V2 bot architectures

## Configuration

### Environment Variables
Key configuration options available in `.env`:

- **CONFIG_PASSWORD**: Encrypts API keys and credentials
- **USERNAME/PASSWORD**: API authentication credentials
- **BROKER_HOST/PORT**: EMQX message broker settings
- **DATABASE_URL**: PostgreSQL connection string
- **ACCOUNT_UPDATE_INTERVAL**: Balance update frequency (minutes)
- **AWS_API_KEY/AWS_SECRET_KEY**: S3 archiving (optional)
- **BANNED_TOKENS**: Comma-separated list of tokens to exclude
- **LOGFIRE_TOKEN**: Observability and monitoring (production)

### Bot Instance Structure
Each bot maintains its own isolated environment:
```
bots/instances/hummingbot-{name}/
├── conf/           # Configuration files
├── data/           # Bot databases and state
└── logs/           # Execution logs
```

## Development

### Code Quality Tools
```bash
# Install pre-commit hooks
make install-pre-commit

# Format code (runs automatically)
black --line-length 130 .
isort --line-length 130 --profile black .
```

### Testing
The API includes comprehensive backtesting capabilities. Test using:
- Backtesting router for strategy validation
- Swagger UI at `http://localhost:8000/docs`
- Integration testing with live containers

## Architecture

### Core Components
1. **FastAPI Application**: HTTP API with Basic Auth
2. **Docker Service**: Container lifecycle management
3. **Bot Orchestrator**: Strategy deployment and monitoring
4. **Accounts Service**: Multi-exchange account management
5. **Market Data Manager**: Real-time feeds and historical data
6. **MQTT Broker**: Real-time bot communication

### Data Models
- Orders and trades with multi-account support
- Portfolio states and balance tracking
- Position management for perpetual trading
- Historical performance analytics

## Authentication

All API endpoints require HTTP Basic Authentication. Include your configured credentials in all requests:

```bash
curl -u username:password http://localhost:8000/endpoint
```

## Troubleshooting

### Database Connection Issues

If you encounter PostgreSQL database connection errors (such as "role 'hbot' does not exist" or "database 'hummingbot_api' does not exist"), use the automated fix script:

```bash
chmod +x fix-database.sh
./fix-database.sh
```

This script will:
1. Check if PostgreSQL is running
2. Verify that the `hbot` user and `hummingbot_api` database exist
3. Automatically fix any missing configuration
4. Test the connection to ensure everything works

#### Manual Database Verification

If you prefer to check manually:

```bash
# Check if containers are running
docker ps | grep -E "hummingbot-postgres|hummingbot-broker"

# Check PostgreSQL logs
docker logs hummingbot-postgres

# Verify database connection
docker exec -it hummingbot-postgres psql -U hbot -d hummingbot_api

# If connection fails, run the initialization script
docker exec -i hummingbot-postgres psql -U postgres < init-db.sql
```

#### Complete Database Reset

If you need to start fresh (⚠️ this will delete all data):

```bash
# Stop all containers and remove volumes
docker compose down -v

# Restart setup
./setup.sh
```

### EMQX Broker Issues

If bots can't connect to the broker:

```bash
# Check EMQX status
docker logs hummingbot-broker

# Restart EMQX
docker compose restart emqx

# Access EMQX dashboard (if needed)
# http://localhost:18083
# Default credentials: admin/public
```

### Common Issues

**Issue**: API won't start - "Database connection failed"
- **Solution**: Run `./fix-database.sh` to repair the database configuration

**Issue**: Bot containers won't start
- **Solution**: Check Docker daemon is running and you have sufficient resources

**Issue**: Can't access API at localhost:8000
- **Solution**: Verify the API container is running: `docker ps | grep hummingbot-api`

**Issue**: Authentication fails
- **Solution**: Check your USERNAME and PASSWORD in the `.env` file

**Issue**: Old bot data causing conflicts
- **Solution**: Clean up old volumes: `docker compose down -v` (⚠️ deletes data)

## Support & Documentation

- **API Documentation**: Available at `http://localhost:8000/docs` when running
- **Detailed Examples**: Check the `CLAUDE.md` file for comprehensive API usage examples
- **Issues**: Report bugs and feature requests through the project's issue tracker
- **Database Troubleshooting**: Use `./fix-database.sh` for automated fixes
---

Ready to start trading? Deploy your first account and start exploring the powerful capabilities of the Hummingbot API!