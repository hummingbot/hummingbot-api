# Using Hummingbot API with Gemini

This guide shows you how to interact with the Hummingbot API using Google Gemini.

## 🤖 Method 1: MCP Server (Recommended)

The Hummingbot MCP server provides natural language access to all API functionality through Gemini.

### Setup via Gemini CLI

1. **Enable MCP during Hummingbot API setup**:
   ```bash
   ./setup.sh  # Answer "y" to "Enable MCP server for AI assistant usage?"
   ```

2. **Add the MCP server using Gemini CLI**:
   ```bash
   gemini mcp add hummingbot \
     --command "docker" \
     --args "run" "--rm" "-i" "-e" "HUMMINGBOT_API_URL=http://host.docker.internal:8000" "-v" "hummingbot_mcp:/root/.hummingbot_mcp" "hummingbot/hummingbot-mcp:latest" \
     --protocol stdio
   ```

3. **Verify the server was added**:
   ```bash
   gemini mcp list
   ```

4. **Start using natural language**:
   - "What are my current portfolio balances?"
   - "Show me active trading bots"
   - "Create a new market making strategy for SOL-USDT"
   - "What's the performance of my bots today?"

### Manual Configuration (Alternative)

#### For Gemini CLI (Global Configuration)

Create or edit `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "hummingbot": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-e", "HUMMINGBOT_API_URL=http://host.docker.internal:8000", "-v", "hummingbot_mcp:/root/.hummingbot_mcp", "hummingbot/hummingbot-mcp:latest"],
      "protocol": "stdio"
    }
  }
}
```

#### For Project-Specific Configuration

Create `.gemini/settings.json` in your project root:

```json
{
  "mcpServers": {
    "hummingbot": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-e", "HUMMINGBOT_API_URL=http://host.docker.internal:8000", "-v", "hummingbot_mcp:/root/.hummingbot_mcp", "hummingbot/hummingbot-mcp:latest"],
      "protocol": "stdio"
    }
  }
}
```

#### For IDE Integration

Create `mcp.json` in your IDE's configuration directory:

```json
{
  "mcpServers": {
    "hummingbot": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-e", "HUMMINGBOT_API_URL=http://host.docker.internal:8000", "-v", "hummingbot_mcp:/root/.hummingbot_mcp", "hummingbot/hummingbot-mcp:latest"],
      "protocol": "stdio"
    }
  }
}
```

### Managing the Connection

```bash
# List all configured MCP servers
gemini mcp list

# View details of the Hummingbot server
gemini mcp get hummingbot

# Remove the server
gemini mcp remove hummingbot
```

## 🔧 Method 2: Direct API Access (Fallback)

If MCP is unavailable, you can interact with the API directly using HTTP requests.

### API Endpoints

The API is accessible at `http://localhost:8000` with interactive Swagger docs at `http://localhost:8000/docs`.

See @API_REFERENCE.md for the complete endpoint reference.

### Authentication

All endpoints require HTTP Basic Authentication:

```bash
curl -u username:password http://localhost:8000/endpoint
```

### Example API Calls

**Get Portfolio State**:
```bash
curl -u admin:admin -X POST http://localhost:8000/portfolio/state \
  -H "Content-Type: application/json" \
  -d '{"account_names": ["master_account"]}'
```

**List Active Bots**:
```bash
curl -u admin:admin http://localhost:8000/bot-orchestration/status
```

**Get Market Prices**:
```bash
curl -u admin:admin -X POST http://localhost:8000/market-data/prices \
  -H "Content-Type: application/json" \
  -d '{
    "connector_name": "binance",
    "trading_pairs": ["BTC-USDT", "ETH-USDT"]
  }'
```

## 🌐 Common Workflows

### Managing Gateway Container (For DEX Trading)

Gateway is required for decentralized exchange (DEX) trading. Use the `manage_gateway_container` MCP tool through natural language commands.

#### Using Natural Language (Recommended)

Once you've configured Gemini with the Hummingbot MCP server, you can manage Gateway using simple commands:

- **"Start Gateway in development mode with passphrase 'admin'"**
  - Launches Gateway container for DEX trading
  - Development mode enables HTTP access and Swagger UI

- **"Check Gateway status"**
  - Verifies if Gateway is running
  - Shows container details, port, and mode

- **"Restart the Gateway container"**
  - Restarts Gateway if it becomes unresponsive
  - Useful for applying configuration changes

- **"Stop Gateway"**
  - Shuts down Gateway when not needed
  - Frees up system resources

#### Using MCP Tool Directly

If you're building custom integrations, you can call the `manage_gateway_container` tool directly:

```python
# 1. Configure API connection (first time only)
send_request({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "configure_api_servers",
        "arguments": {
            "api_url": "http://host.docker.internal:8000",
            "username": "admin",
            "password": "admin"
        }
    }
})

# 2. Start Gateway container
send_request({
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": "manage_gateway_container",
        "arguments": {
            "action": "start",
            "config": {
                "passphrase": "admin",
                "dev_mode": true,
                "image": "hummingbot/gateway:latest",
                "port": 15888
            }
        }
    }
})

# 3. Check Gateway status
send_request({
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
        "name": "manage_gateway_container",
        "arguments": {
            "action": "get_status"
        }
    }
})

# 4. Restart Gateway (if needed)
send_request({
    "jsonrpc": "2.0",
    "id": 4,
    "method": "tools/call",
    "params": {
        "name": "manage_gateway_container",
        "arguments": {
            "action": "restart"
        }
    }
})

# 5. Stop Gateway
send_request({
    "jsonrpc": "2.0",
    "id": 5,
    "method": "tools/call",
    "params": {
        "name": "manage_gateway_container",
        "arguments": {
            "action": "stop"
        }
    }
})
```

#### Important Notes
- **Development mode** (`dev_mode: true`): HTTP access on port 15888, Swagger UI available at `http://localhost:15888/docs`
- **Production mode** (`dev_mode: false`): HTTPS with certificates required, more secure for production use
- **Passphrase**: Used to encrypt/decrypt DEX wallet keys - store it securely
- **Port**: Default is 15888, ensure it's available on your system

## 📚 Additional Resources

- **API Reference**: See @API_REFERENCE.md for all available endpoints
- **README**: See @README.md for complete setup instructions
- **Swagger UI**: http://localhost:8000/docs (interactive API documentation)

## 🆘 Troubleshooting

**MCP server not responding**:
```bash
# Check if MCP container is running
docker ps | grep hummingbot-mcp

# If not, re-enable during setup
./setup.sh  # Answer "y" to MCP prompt
```

**Configuration not loading**:
- Verify the JSON syntax in your configuration file
- Ensure Docker is running
- Check that the hummingbot-mcp container exists

**Authentication errors**:
- Verify username and password in `.env` file
- Ensure the API is running: `docker ps | grep hummingbot-api`

## MCP Tools Best Practices

### Using `configure_api_servers` for Connection Management

**Before using any MCP tools**, always ensure the API server is properly configured:

```python
# Check if connection is working - if any MCP tool fails, reconnect:
configure_api_servers(action="add", name="local", host="localhost", port=8000, username="admin", password="admin")
configure_api_servers(action="set_default", name="local")
```

### Using `get_portfolio_overview` for Token Balances

**Preferred method for checking balances**:
- Use `get_portfolio_overview()` instead of direct API calls
- Includes CEX balances, DEX balances, LP positions, and active orders in one call
- Automatically handles all account types (Hyperliquid, Solana, Ethereum, etc.)

```python
# Get complete portfolio overview
get_portfolio_overview(
    include_balances=True,
    include_perp_positions=False,
    include_lp_positions=True,
    include_active_orders=True,
    as_distribution=False
)
```

### Common MCP Connection Issue

**Error**:
```
Error executing tool get_portfolio_overview: ❌ Failed to connect to Hummingbot API at http://docker.host.internal:8000

Connection failed after 3 attempts.

💡 Solutions:
  1. Check if the API is running and accessible
  2. Verify your credentials are correct
  3. Use 'configure_api_servers' tool for setup

Original error: Cannot connect to host docker.host.internal:8000 ssl:default [Name or service not known]
```

**Root Cause**: The MCP tool loses connection to the API server. This happens when:
- MCP server reconnects/restarts
- API credentials are not cached
- Network configuration changes

**Solution**: Reconfigure the API server connection before retrying:

```python
# Step 1: Add server configuration
configure_api_servers(
    action="add",
    name="local",
    host="localhost",
    port=8000,
    username="admin",
    password="admin"
)

# Step 2: Set as default
configure_api_servers(action="set_default", name="local")

# Step 3: Retry the operation
get_portfolio_overview(include_balances=True)
```

**Prevention**: Always check connection before using other MCP tools. If you see any connection error, immediately run `configure_api_servers` to restore the connection.
