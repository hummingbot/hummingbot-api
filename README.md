# Hummingbot API

A REST API for managing Hummingbot trading bots across multiple exchanges, with AI assistant integration via MCP.

## Quick Start

```bash
git clone https://github.com/hummingbot/hummingbot-api.git
cd hummingbot-api
make setup    # Creates .env (prompts for passwords)
make deploy   # Starts all services
```

That's it! The API is now running at http://localhost:8000

## Available Commands

| Command | Description |
|---------|-------------|
| `make setup` | Create `.env` file with configuration |
| `make deploy` | Start all services (API, PostgreSQL, EMQX) |
| `make stop` | Stop all services |
| `make run` | Run API locally in dev mode |
| `make install` | Install conda environment for development |
| `make build` | Build Docker image |
| `make tailscale-status` | Show Tailscale connection status |

## Services

After `make deploy`, these services are available:

| Service | URL | Description |
|---------|-----|-------------|
| **API** | http://localhost:8000 | REST API |
| **Swagger UI** | http://localhost:8000/docs | Interactive API documentation |
| **PostgreSQL** | localhost:5432 | Database |
| **EMQX** | localhost:1883 | MQTT broker |
| **EMQX Dashboard** | http://localhost:18083 | Broker admin (admin/public) |

## Connect AI Assistant (MCP)

### Claude Code (CLI)

```bash
claude mcp add --transport stdio hummingbot -- \
  docker run --rm -i \
  -e HUMMINGBOT_API_URL=http://host.docker.internal:8000 \
  -v hummingbot_mcp:/root/.hummingbot_mcp \
  hummingbot/hummingbot-mcp:latest
```

Then use natural language:
- "Show my portfolio balances"
- "Set up my Binance account"
- "Create a market making strategy for ETH-USDT"

### Claude Desktop

Add to your config file:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

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

Restart Claude Desktop after adding.

## Gateway (DEX Trading)

Gateway enables decentralized exchange trading. Start it via MCP:

> "Start Gateway in development mode with passphrase 'admin'"

Or via API at http://localhost:8000/docs using the Gateway endpoints.

Once running, Gateway is available at http://localhost:15888

## Configuration

The `.env` file contains all configuration. Key settings:

```bash
USERNAME=admin              # API username
PASSWORD=admin              # API password
CONFIG_PASSWORD=admin       # Encrypts bot credentials
DATABASE_URL=...            # PostgreSQL connection
GATEWAY_URL=...             # Gateway URL (for DEX)
```

Edit `.env` and restart with `make deploy` to apply changes.

## Secure Connection via Tailscale

[Tailscale](https://tailscale.com) creates a private WireGuard network (tailnet) that makes the API accessible only to devices on your tailnet — no open ports, no firewall rules needed.

Use this when running on a VPS or cloud server and want to access the API privately from another machine (e.g. Condor or MCP tools).

### Prerequisites: Get a Tailscale auth key

1. Create a free account at [tailscale.com](https://tailscale.com)
2. Go to **Settings → Keys**: [tailscale.com/admin/settings/keys](https://tailscale.com/admin/settings/keys)
3. Click **Generate auth key** — check **Reusable** for multiple deployments
4. Copy the key (starts with `tskey-auth-`)

### Setup

Run `make setup` and answer `y` when prompted:

> Use Tailscale for secure private networking? [y/N]

This adds the following to `.env`:

```bash
TAILSCALE_ENABLED=true
TAILSCALE_AUTH_KEY=tskey-auth-...
TAILSCALE_HOSTNAME=hummingbot-api   # MagicDNS hostname on your tailnet
```

### Deploy

```bash
make deploy
```

When `TAILSCALE_ENABLED=true`, this automatically runs:

```bash
docker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d
```

A Tailscale sidecar container joins your tailnet using `network_mode: host`. The API is then reachable at `http://hummingbot-api:8000` from any device on the same tailnet — port 8000 is not exposed publicly.

### Connecting MCP tools via Tailscale

Once on the same tailnet, use the MagicDNS hostname instead of `localhost`:

```bash
claude mcp add --transport stdio hummingbot -- \
  docker run --rm -i \
  -e HUMMINGBOT_API_URL=http://hummingbot-api:8000 \
  -v hummingbot_mcp:/root/.hummingbot_mcp \
  hummingbot/hummingbot-mcp:latest
```

### Dev mode

When `TAILSCALE_ENABLED=true`, `make run` will automatically install Tailscale if needed, connect to your tailnet, and bind uvicorn to `127.0.0.1` only (Tailscale handles external access).

### Check status

```bash
make tailscale-status
```

## API Features

- **Portfolio**: Balances, positions, P&L across all exchanges
- **Trading**: Place orders, manage positions, track history
- **Bots**: Deploy, monitor, and control trading bots
- **Market Data**: Prices, orderbooks, candles, funding rates
- **Strategies**: Create and manage trading strategies

Full API documentation at http://localhost:8000/docs

## Development

```bash
make install              # Create conda environment
conda activate hummingbot-api
make run                  # Run with hot-reload
```

## Troubleshooting

**API won't start?**
```bash
docker compose logs hummingbot-api
```

**Database issues?**
```bash
docker compose down -v    # Reset all data
make deploy               # Fresh start
```

**Check service status:**
```bash
docker ps | grep hummingbot
```

**Tailscale not connecting?**
```bash
make tailscale-status     # Check tailnet peers
```
Confirm the node appears in `tailscale status` and that MagicDNS is enabled in your Tailscale admin console.

## Support

- **API Docs**: http://localhost:8000/docs
- **Issues**: https://github.com/hummingbot/hummingbot-api/issues
