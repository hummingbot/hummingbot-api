# Hummingbot API

A REST API for managing Hummingbot trading bots across multiple exchanges, with AI assistant integration via MCP.

> **Why we recommend Tailscale for production**
>
> Hummingbot API controls real trading: orders, balances, bots, and stored exchange keys. That has always required strong passwords and careful configuration—but **the risk surface has grown**. Tools like **MCP**, **Condor agents**, and other AI assistants make powerful API actions easier to trigger, while cloud VPSes are constantly scanned for open ports like **8000**.
>
> **Tailscale is one safeguard you can add**: it puts the API on a private encrypted network so only your devices can reach it, without publishing port 8000 to the internet. It does **not** replace proper security—use strong API and config passwords, keep exchange keys protected, and avoid exposing sensitive services publicly. Tailscale also works when the API and clients run on the **same machine**.

## Quick Start

**Recommended (Docker):** install from an empty directory with the [Hummingbot Deploy](https://github.com/hummingbot/deploy) helper script. **Docker** must be installed and running first.

### Before you install (production)

For VPS or remote deployments, prepare Tailscale first:

1. Create a free account at [tailscale.com](https://tailscale.com)
2. Generate a **reusable** auth key at [Settings → Keys](https://login.tailscale.com/admin/settings/keys) (starts with `tskey-auth-`)
3. Enable **[MagicDNS](https://login.tailscale.com/admin/dns)** in the Tailscale admin console

Full walkthrough: [hummingbot.org Tailscale guide](https://hummingbot.org/hummingbot-api/tailscale/) · [Securing Condor and Hummingbot API with Tailscale](https://hummingbot.org/blog/posts/securing-condor-and-hummingbot-api-with-tailscale/)

### Install

```bash
curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh | bash -s -- --hummingbot-api
```

The script clones **`hummingbot-api`**, runs **`make setup`** (creates **`.env`**), pulls Compose images, and runs **`make deploy`**, which starts the **API**, **PostgreSQL**, and **EMQX** containers.

The setup script prompts for:

- **Credentials** — API username/password (HTTP Basic Auth) and config password (encrypts bot credentials)
- **Tailscale** — answer **`y`** when asked *Use Tailscale for secure private networking?* and paste your auth key (default hostname: **`hummingbot-api`**)

If the script finishes but services did not start, run:

```bash
cd hummingbot-api
make setup
make deploy
```

### Access the API

| Where you connect from | URL |
|------------------------|-----|
| Same machine as the API | `http://localhost:8000` |
| Another device on your tailnet (Condor, MCP, browser) | `http://hummingbot-api:8000` |

Use your API username and password for all requests. **Do not open port 8000 on your public firewall** when Tailscale is enabled.

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

After hummingbot-api is running, these services are available:

| Service | Local URL | Tailnet URL (when Tailscale enabled) | Description |
|---------|-----------|--------------------------------------|-------------|
| **API** | http://localhost:8000 | http://hummingbot-api:8000 | REST API |
| **Swagger UI** | http://localhost:8000/docs | http://hummingbot-api:8000/docs | Interactive API documentation |
| **PostgreSQL** | localhost:5432 | — | Database |
| **EMQX** | localhost:1883 | — | MQTT broker |
| **EMQX Dashboard** | http://localhost:18083 | — | Broker admin (admin/public) |

## Connect AI Assistant (MCP)

> **Production:** use `http://hummingbot-api:8000` (MagicDNS) instead of `localhost` when MCP runs on a different device than the API. Both must be on the same Tailscale account.

### Claude Code (CLI)

```bash
claude mcp add --transport stdio hummingbot -- \
  docker run --rm -i \
  -e HUMMINGBOT_API_URL=http://hummingbot-api:8000 \
  -v hummingbot_mcp:/root/.hummingbot_mcp \
  hummingbot/hummingbot-mcp:latest
```

For local-only dev on the same machine, use `http://host.docker.internal:8000` instead.

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
      "args": ["run", "--rm", "-i", "-e", "HUMMINGBOT_API_URL=http://hummingbot-api:8000", "-v", "hummingbot_mcp:/root/.hummingbot_mcp", "hummingbot/hummingbot-mcp:latest"]
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

# Tailscale (recommended for production)
TAILSCALE_ENABLED=true
TAILSCALE_AUTH_KEY=tskey-auth-...
TAILSCALE_HOSTNAME=hummingbot-api   # MagicDNS hostname on your tailnet
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
5. Enable **[MagicDNS](https://login.tailscale.com/admin/dns)** in the Tailscale admin console

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

From another device on your tailnet:

```bash
curl -u YOUR_USERNAME:YOUR_PASSWORD http://hummingbot-api:8000/health
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

- **Docs**: https://hummingbot.org/hummingbot-api/
- **Tailscale guide**: https://hummingbot.org/hummingbot-api/tailscale/
- **API Docs**: http://localhost:8000/docs
- **Issues**: https://github.com/hummingbot/hummingbot-api/issues
