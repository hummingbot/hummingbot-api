# Using Hummingbot API with AI Agents

This guide shows you how to interact with the Hummingbot API using various AI agents including ChatGPT, custom agents, and other MCP-compatible assistants.

## 🤖 Method 1: MCP Server (Recommended)

The Hummingbot MCP server provides natural language access to all API functionality through MCP-compatible AI clients.

### OpenAI ChatGPT (Desktop App)

If OpenAI releases an MCP-compatible desktop client, you can configure it similar to Claude:

1. **Enable MCP during Hummingbot API setup**:
   ```bash
   ./setup.sh  # Answer "y" to "Enable MCP server for AI assistant usage?"
   ```

2. **Configure the MCP server**:
   Add to your ChatGPT configuration file (location may vary):
   ```json
   {
     "mcpServers": {
       "hummingbot": {
         "command": "docker",
         "args": ["run", "--rm", "-i", "-e", "HUMMINGBOT_API_URL=http://host.docker.internal:8000", "-v", "hummingbot_mcp:/root/.hummingbot_mcp", "hummingbot/hummingbot-mcp:latest"]
       }
     }
   }
   ```

3. **Start using natural language**:
   - "Show me my portfolio across all exchanges"
   - "What bots are currently running?"
   - "Create a grid trading strategy for BTC-USDT"
   - "Analyze my trading performance this month"

### Custom MCP Clients

For custom implementations, connect to the MCP server using stdio transport:

**Python Example**:
```python
import subprocess
import json

# Start the MCP server process
process = subprocess.Popen([
    "docker", "run", "--rm", "-i", "-e", "HUMMINGBOT_API_URL=http://host.docker.internal:8000", "-v", "hummingbot_mcp:/root/.hummingbot_mcp", "hummingbot/hummingbot-mcp:latest"
],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True
)

# Send JSON-RPC request
request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {}
}
process.stdin.write(json.dumps(request) + "\n")
process.stdin.flush()

# Read response
response = process.stdout.readline()
print(json.loads(response))
```

**Node.js Example**:
```javascript
const { spawn } = require('child_process');

// Start MCP server
const mcp = spawn('docker', ['run', '--rm', '-i', '-e', 'HUMMINGBOT_API_URL=http://host.docker.internal:8000', '-v', 'hummingbot_mcp:/root/.hummingbot_mcp', 'hummingbot/hummingbot-mcp:latest']);

// Send request
const request = {
  jsonrpc: '2.0',
  id: 1,
  method: 'tools/list',
  params: {}
};

mcp.stdin.write(JSON.stringify(request) + '\n');

// Handle response
mcp.stdout.on('data', (data) => {
  console.log(JSON.parse(data.toString()));
});
```

### Available MCP Tools

The Hummingbot MCP server provides these tools:

- **Portfolio Management**: `get_portfolio_balances`, `get_portfolio_distribution`
- **Bot Operations**: `list_bots`, `start_bot`, `stop_bot`, `get_bot_status`
- **Market Data**: `get_prices`, `get_order_book`, `get_candles`
- **Order Management**: `place_order`, `cancel_order`, `get_active_orders`
- **Account Management**: `list_accounts`, `add_credentials`
- **Strategy Management**: `list_strategies`, `get_strategy_template`

For a complete list, use the `tools/list` MCP method.

## 🔧 Method 2: Direct API Access (Standard HTTP)

All AI agents can interact with the API using standard HTTP requests.

### API Endpoints

The API is accessible at `http://localhost:8000` with interactive Swagger docs at `http://localhost:8000/docs`.

See @API_REFERENCE.md for the complete endpoint reference.

### Authentication

All endpoints require HTTP Basic Authentication:

```bash
curl -u username:password http://localhost:8000/endpoint
```

Use the username and password you configured during setup (stored in `.env`).

### Common API Operations

**1. Get Portfolio Balances**:
```bash
curl -u admin:admin -X POST http://localhost:8000/portfolio/state \
  -H "Content-Type: application/json" \
  -d '{}'
```

**2. List Active Bots**:
```bash
curl -u admin:admin http://localhost:8000/bot-orchestration/status
```

**3. Get Market Prices**:
```bash
curl -u admin:admin -X POST http://localhost:8000/market-data/prices \
  -H "Content-Type: application/json" \
  -d '{
    "connector_name": "binance",
    "trading_pairs": ["BTC-USDT", "ETH-USDT"]
  }'
```

**4. Place an Order**:
```bash
curl -u admin:admin -X POST http://localhost:8000/trading/orders \
  -H "Content-Type: application/json" \
  -d '{
    "account_name": "master_account",
    "connector_name": "binance",
    "trading_pair": "BTC-USDT",
    "order_type": "limit",
    "trade_type": "buy",
    "price": 50000,
    "amount": 0.001
  }'
```

**5. Start a Trading Bot**:
```bash
curl -u admin:admin -X POST http://localhost:8000/bot-orchestration/deploy-v2-script \
  -H "Content-Type: application/json" \
  -d '{
    "bot_name": "my_pmm_bot",
    "script": "v2_with_controllers",
    "config": {
      "connector": "binance",
      "trading_pair": "ETH-USDT",
      "total_amount_quote": 100
    }
  }'
```

### Integration Examples

**Python with requests**:
```python
import requests
from requests.auth import HTTPBasicAuth

auth = HTTPBasicAuth('admin', 'admin')
base_url = 'http://localhost:8000'

# Get portfolio state
response = requests.post(
    f'{base_url}/portfolio/state',
    json={},
    auth=auth
)
print(response.json())
```

**JavaScript with fetch**:
```javascript
const username = 'admin';
const password = 'admin';
const baseURL = 'http://localhost:8000';

const headers = {
  'Content-Type': 'application/json',
  'Authorization': 'Basic ' + btoa(`${username}:${password}`)
};

// Get portfolio state
fetch(`${baseURL}/portfolio/state`, {
  method: 'POST',
  headers: headers,
  body: JSON.stringify({})
})
.then(res => res.json())
.then(data => console.log(data));
```

## 📚 API Reference

For complete API documentation, see:
- **@API_REFERENCE.md**: Full endpoint reference with request/response examples
- **Swagger UI**: http://localhost:8000/docs (interactive documentation)
- **@README.md**: Setup instructions and architecture overview

## 🆘 Troubleshooting

**MCP Server Issues**:
```bash
# Check if MCP container is running
docker ps | grep hummingbot-mcp

# View MCP logs
docker logs hummingbot-mcp

# Restart MCP
docker compose restart hummingbot-mcp
```

**API Connection Issues**:
```bash
# Check if API is running
docker ps | grep hummingbot-api

# View API logs
docker logs hummingbot-api

# Test API connectivity
curl -u admin:admin http://localhost:8000/
```

**Authentication Errors**:
- Verify credentials in `.env` file
- Ensure you're using the correct username and password
- Check that the API container is running

**Docker Issues**:
```bash
# Ensure Docker is running
docker ps

# Restart all services
docker compose restart

# View all logs
docker compose logs -f
```

## 🚀 Next Steps

1. **Explore the API**: Visit http://localhost:8000/docs
2. **Read API Reference**: See @API_REFERENCE.md for all endpoints
3. **Set up credentials**: Add exchange API keys via `/accounts/add-credential`
4. **Deploy a bot**: Start with a simple PMM or DCA strategy
5. **Monitor performance**: Use portfolio and bot status endpoints

## 💡 Tips for AI Agent Integration

1. **Use MCP when possible**: More natural language interface, automatic tool discovery
2. **Handle authentication**: Store credentials securely in your agent's configuration
3. **Implement retry logic**: API calls may timeout, implement exponential backoff
4. **Parse responses carefully**: All responses are JSON, handle errors appropriately
5. **Use Swagger UI**: Test endpoints manually before integrating into your agent
