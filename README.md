# Hummingbot API

A comprehensive RESTful API framework for managing trading operations across multiple exchanges. The Hummingbot API provides a centralized platform to aggregate all your trading functionalities, from basic account management to sophisticated automated trading strategies.

## What is Hummingbot API?

The Hummingbot API is designed to be your central hub for trading operations, offering:

- **Multi-Exchange Account Management**: Create and manage multiple trading accounts across different exchanges
- **Portfolio Monitoring**: Real-time balance tracking and portfolio distribution analysis
- **Trade Execution**: Execute trades, manage orders, and monitor positions across all your accounts
- **Automated Trading**: Deploy and control Hummingbot instances with automated strategies
- **Strategy Management**: Add, configure, and manage trading strategies in real-time
- **Complete Flexibility**: Build any trading product on top of this robust API framework

Whether you're building a trading dashboard, implementing algorithmic strategies, or creating a comprehensive trading platform, the Hummingbot API provides all the tools you need.

## System Dependencies

The Hummingbot API requires two essential services to function properly:

### 1. PostgreSQL Database
Stores all trading data including:
- Orders and trade history
- Account states and balances
- Positions and funding payments
- Performance metrics

**Note:** The database is automatically initialized using environment variables (`POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD`). The included `init-db.sql` script serves as a safety net for edge cases where automatic initialization doesn't complete properly.

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

## Getting Started

Once the API is running, you can access it at `http://localhost:8000`

### First Steps
1. **Visit the API Documentation**: Go to `http://localhost:8000/docs` to explore the interactive Swagger documentation
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