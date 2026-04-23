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
| `make run-https` | Run API locally with HTTPS cert/key |
| `make install` | Install conda environment for development |
| `make build` | Build Docker image |
| `make generate-certs` | Interactive SSL cert generation helper |
| `make show-certs` | Show SSL cert paths and whether files exist |

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

## HTTPS / SSL Setup

By default, `hummingbot-api` runs on HTTP (`http://localhost:8000`). To expose HTTPS directly, run Uvicorn with SSL cert/key files.

### Automated setup (recommended)

Use the built-in interactive flow:

```bash
make generate-certs
```

This will:
- prompt whether to enable HTTPS
- generate CA + server cert/key under `./certs`
- optionally generate client cert/key for mTLS
- print the exact paths to copy into Condor config

Then run:

```bash
make run-https
```

### Run on custom HTTPS port

```bash
uvicorn main:app --host 0.0.0.0 --port 8443 \
  --ssl-certfile /path/to/server.pem \
  --ssl-keyfile /path/to/server.key
```

Then your API is available at `https://<host>:8443`.

### Generate local certificates (OpenSSL quickstart)

Use this for local/dev self-signed CA workflows:

```bash
# Local CA
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
  -out ca.pem -subj "/CN=Hummingbot Local CA"

# Server cert for API host (example: localhost)
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=localhost"
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out server.pem -days 825 -sha256
```

### Optional mTLS (client certificate auth)

If your deployment requires mTLS, generate a client cert/key signed by the same CA:

```bash
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr -subj "/CN=condor-client"
openssl x509 -req -in client.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out client.pem -days 825 -sha256
```

You can then configure Condor with:
- `ca_bundle_path` -> `ca.pem`
- `client_cert_path` -> `client.pem`
- `client_key_path` -> `client.key`

### Connect Condor to HTTPS API

In Condor `config.yml`:

```yaml
servers:
  production:
    host: api.example.com
    port: 8443
    protocol: https
    tls_verify: true
    ca_bundle_path: /path/to/ca.pem
    client_cert_path: /path/to/client.pem     # optional (mTLS)
    client_key_path: /path/to/client.key      # optional (mTLS)
    username: admin
    password: strong_password
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

## Support

- **API Docs**: http://localhost:8000/docs
- **Issues**: https://github.com/hummingbot/hummingbot-api/issues
