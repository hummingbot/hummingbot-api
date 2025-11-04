#!/bin/bash

# Backend API Setup Script
# This script creates a comprehensive .env file with all configuration options
# following the Pydantic Settings structure established in config.py

set -e  # Exit on any error

# Colors for better output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo "🚀 Backend API Setup"
echo ""

echo -n "Config password [default: admin]: "
read CONFIG_PASSWORD
CONFIG_PASSWORD=${CONFIG_PASSWORD:-admin}

echo -n "API username [default: admin]: "
read USERNAME
USERNAME=${USERNAME:-admin}

echo -n "API password [default: admin]: "
read PASSWORD
PASSWORD=${PASSWORD:-admin}

echo ""
echo -e "${YELLOW}Optional Services${NC}"
echo -n "Enable MCP server for AI assistant usage? (y/n) [default: n]: "
read ENABLE_MCP
ENABLE_MCP=${ENABLE_MCP:-n}

echo -n "Enable Dashboard web interface? (y/n) [default: n]: "
read ENABLE_DASHBOARD
ENABLE_DASHBOARD=${ENABLE_DASHBOARD:-n}

echo ""
echo -e "${YELLOW}Gateway Configuration (Optional)${NC}"
echo -n "Gateway passphrase [default: admin, press Enter to skip]: "
read GATEWAY_PASSPHRASE
GATEWAY_PASSPHRASE=${GATEWAY_PASSPHRASE:-admin}

# Set paths and defaults
BOTS_PATH=$(pwd)

# Use sensible defaults for everything else
DEBUG_MODE="false"
BROKER_HOST="localhost"
BROKER_PORT="1883"
BROKER_USERNAME="admin"
BROKER_PASSWORD="password"
DATABASE_URL="postgresql+asyncpg://hbot:hummingbot-api@localhost:5432/hummingbot_api"
CLEANUP_INTERVAL="300"
FEED_TIMEOUT="600"
AWS_API_KEY=""
AWS_SECRET_KEY=""
S3_BUCKET=""
LOGFIRE_ENV="dev"
BANNED_TOKENS='["NAV","ARS","ETHW","ETHF","NEWT"]'

echo ""
echo -e "${GREEN}✅ Using sensible defaults for MQTT, Database, and other settings${NC}"

echo ""
echo -e "${GREEN}📝 Creating .env file...${NC}"

# Create .env file with proper structure and comments
cat > .env << EOF
# =================================================================
# Backend API Environment Configuration
# Generated on: $(date)
# =================================================================

# =================================================================
# 🔐 Security Configuration
# =================================================================
USERNAME=$USERNAME
PASSWORD=$PASSWORD
DEBUG_MODE=$DEBUG_MODE
CONFIG_PASSWORD=$CONFIG_PASSWORD

# =================================================================
# 🔗 MQTT Broker Configuration (BROKER_*)
# =================================================================
BROKER_HOST=$BROKER_HOST
BROKER_PORT=$BROKER_PORT
BROKER_USERNAME=$BROKER_USERNAME
BROKER_PASSWORD=$BROKER_PASSWORD

# =================================================================
# 💾 Database Configuration (DATABASE_*)
# =================================================================
DATABASE_URL=$DATABASE_URL

# =================================================================
# 📊 Market Data Feed Manager Configuration (MARKET_DATA_*)
# =================================================================
MARKET_DATA_CLEANUP_INTERVAL=$CLEANUP_INTERVAL
MARKET_DATA_FEED_TIMEOUT=$FEED_TIMEOUT

# =================================================================
# ☁️ AWS Configuration (AWS_*) - Optional
# =================================================================
AWS_API_KEY=$AWS_API_KEY
AWS_SECRET_KEY=$AWS_SECRET_KEY
AWS_S3_DEFAULT_BUCKET_NAME=$S3_BUCKET

# =================================================================
# ⚙️ Application Settings
# =================================================================
LOGFIRE_ENVIRONMENT=$LOGFIRE_ENV
BANNED_TOKENS=$BANNED_TOKENS

# =================================================================
# 🌐 Gateway Configuration (GATEWAY_*) - Optional
# =================================================================
GATEWAY_PASSPHRASE=$GATEWAY_PASSPHRASE
GATEWAY_URL=http://localhost:15888

# =================================================================
# 📁 Legacy Settings (maintained for backward compatibility)
# =================================================================
BOTS_PATH=$BOTS_PATH

EOF

echo -e "${GREEN}✅ .env file created successfully!${NC}"
echo ""


# Enable Dashboard if requested
if [[ "$ENABLE_DASHBOARD" =~ ^[Yy]$ ]]; then
    echo -e "${GREEN}📊 Enabling Dashboard in docker-compose.yml...${NC}"

    # Remove the comment line first
    sed -i.bak '/^  # Uncomment to enable Dashboard (optional web interface)/d' docker-compose.yml

    # Uncomment the dashboard service lines
    sed -i.bak '/^  # dashboard:/,/^  #       - emqx-bridge$/s/^  # /  /' docker-compose.yml

    # Remove backup file
    rm -f docker-compose.yml.bak

    echo -e "${GREEN}✅ Dashboard enabled!${NC}"
    echo ""
fi

# Display configuration summary
echo -e "${BLUE}📋 Configuration Summary${NC}"
echo "======================="
echo -e "${CYAN}Security:${NC} Username: $USERNAME, Debug: $DEBUG_MODE"
echo -e "${CYAN}Broker:${NC} $BROKER_HOST:$BROKER_PORT"
echo -e "${CYAN}Database:${NC} ${DATABASE_URL%%@*}@[hidden]"
echo -e "${CYAN}Market Data:${NC} Cleanup: ${CLEANUP_INTERVAL}s, Timeout: ${FEED_TIMEOUT}s"
echo -e "${CYAN}Environment:${NC} $LOGFIRE_ENV"

if [ -n "$AWS_API_KEY" ]; then
    echo -e "${CYAN}AWS:${NC} Configured with S3 bucket: $S3_BUCKET"
else
    echo -e "${CYAN}AWS:${NC} Not configured (optional)"
fi

echo ""
echo -e "${GREEN}🎉 Setup Complete!${NC}"
echo ""

# Check if password verification file exists
if [ ! -f "bots/credentials/master_account/.password_verification" ]; then
    echo -e "${YELLOW}📌 Note:${NC} Password verification file will be created on first startup"
    echo -e "   Location: ${BLUE}bots/credentials/master_account/.password_verification${NC}"
    echo ""
fi

echo -e "Next steps:"
echo "1. Review the .env file if needed: cat .env"
echo "2. Install dependencies: make install"
echo "3. Start the API: make run"
echo ""
echo -e "${PURPLE}💡 Pro tip:${NC} You can modify environment variables in .env file anytime"
echo -e "${PURPLE}📚 Documentation:${NC} Check config.py for all available settings"
echo -e "${PURPLE}🔒 Security:${NC} The password verification file secures bot credentials"
echo ""
echo -e "${GREEN}🐳 Starting services (API, EMQX, PostgreSQL)...${NC}"

# Start all services (MCP and Dashboard are optional - see docker-compose.yml)
docker compose up -d &
docker pull hummingbot/hummingbot:latest &

# Wait for both operations to complete
wait

echo -e "${GREEN}✅ All Docker containers started!${NC}"
echo ""

# Wait for PostgreSQL to be ready
echo -e "${YELLOW}⏳ Waiting for PostgreSQL to initialize...${NC}"
sleep 5

# Check PostgreSQL connection
MAX_RETRIES=30
RETRY_COUNT=0
DB_READY=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if docker exec hummingbot-postgres pg_isready -U hbot -d hummingbot_api > /dev/null 2>&1; then
        DB_READY=true
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo -ne "\r${YELLOW}⏳ Waiting for database... ($RETRY_COUNT/$MAX_RETRIES)${NC}"
    sleep 2
done
echo ""

if [ "$DB_READY" = true ]; then
    echo -e "${GREEN}✅ PostgreSQL is ready!${NC}"

    # Verify database and user exist
    echo -e "${YELLOW}🔍 Verifying database configuration...${NC}"

    # Check if hbot user exists
    USER_EXISTS=$(docker exec hummingbot-postgres psql -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='hbot'" 2>/dev/null)

    # Check if database exists
    DB_EXISTS=$(docker exec hummingbot-postgres psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='hummingbot_api'" 2>/dev/null)

    if [ "$USER_EXISTS" = "1" ] && [ "$DB_EXISTS" = "1" ]; then
        echo -e "${GREEN}✅ Database 'hummingbot_api' and user 'hbot' verified successfully!${NC}"
    else
        echo -e "${YELLOW}⚠️  Database initialization may be incomplete. Running manual initialization...${NC}"

        # Run the init script manually
        docker exec -i hummingbot-postgres psql -U postgres < init-db.sql

        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✅ Database manually initialized successfully!${NC}"
        else
            echo -e "${RED}❌ Failed to initialize database. See troubleshooting below.${NC}"
        fi
    fi
else
    echo -e "${RED}❌ PostgreSQL failed to start within timeout period${NC}"
    echo ""
    echo -e "${YELLOW}Troubleshooting steps:${NC}"
    echo "1. Check PostgreSQL logs: docker logs hummingbot-postgres"
    echo "2. Verify container status: docker ps -a | grep postgres"
    echo "3. Try removing old volumes: docker compose down -v && docker compose up emqx postgres -d"
    echo "4. Manually verify database: docker exec -it hummingbot-postgres psql -U postgres"
    echo ""
fi

echo -e "${GREEN}✅ Setup completed!${NC}"
echo ""

# Display services information
echo -e "${BLUE}🎉 Your Hummingbot API Platform is Running!${NC}"
echo "========================================="
echo ""
echo -e "${CYAN}Available Services:${NC}"
echo -e "  🔧 ${GREEN}API${NC}            - http://localhost:8000"
echo -e "  📚 ${GREEN}API Docs${NC}       - http://localhost:8000/docs (Swagger UI)"
echo -e "  📡 ${GREEN}EMQX Broker${NC}    - localhost:1883"
echo -e "  💾 ${GREEN}PostgreSQL${NC}     - localhost:5432"

if [[ "$ENABLE_MCP" =~ ^[Yy]$ ]]; then
    echo -e "  🤖 ${GREEN}MCP Server${NC}     - AI Assistant integration (connect Claude/ChatGPT/Gemini)"
fi

if [[ "$ENABLE_DASHBOARD" =~ ^[Yy]$ ]]; then
    echo -e "  📊 ${GREEN}Dashboard${NC}      - http://localhost:8501"
fi

echo ""

echo -e "${YELLOW}📝 Next Steps:${NC}"
echo ""
echo "1. ${CYAN}Access the API:${NC}"
echo "   • Swagger UI: http://localhost:8000/docs (full REST API documentation)"

if [[ "$ENABLE_MCP" =~ ^[Yy]$ ]]; then
    echo ""
    echo "2. ${CYAN}Connect an AI Assistant (MCP enabled):${NC}"
    echo ""
    echo "   ${GREEN}Claude Desktop Setup:${NC}"
    echo "   a. Install Claude Desktop from: ${BLUE}https://claude.ai/download${NC}"
    echo "   b. Add this to your Claude config file:"
    echo -e "      ${PURPLE}macOS:${NC} ~/Library/Application Support/Claude/claude_desktop_config.json"
    echo -e "      ${PURPLE}Windows:${NC} %APPDATA%\\Claude\\claude_desktop_config.json"
    echo ""
    echo '      {'
    echo '        "mcpServers": {'
    echo '          "hummingbot": {'
    echo '            "command": "docker",'
    echo '            "args": ["run", "--rm", "-i", "-e", "HUMMINGBOT_API_URL=http://host.docker.internal:8000", "-v", "hummingbot_mcp:/root/.hummingbot_mcp", "hummingbot/hummingbot-mcp:latest"]'
    echo '          }'
    echo '        }'
    echo '      }'
    echo ""
    echo "   c. Restart Claude Desktop"
    echo "   d. Try commands like:"
    echo '      - "Show me my portfolio balances"'
    echo '      - "Create a market making strategy for ETH-USDT on Binance"'
fi

if [[ "$ENABLE_DASHBOARD" =~ ^[Yy]$ ]]; then
    echo ""
    echo "3. ${CYAN}Access Dashboard:${NC}"
    echo "   • Web UI: http://localhost:8501"
fi

echo ""
echo -e "${CYAN}Available Access Methods:${NC}"
echo "  ✅ Swagger UI (http://localhost:8000/docs) - Full REST API"

if [[ "$ENABLE_MCP" =~ ^[Yy]$ ]]; then
    echo "  ✅ MCP - AI Assistant integration (Claude, ChatGPT, Gemini)"
else
    echo "  ⚪ MCP - Run setup.sh again to enable AI assistant"
fi

if [[ "$ENABLE_DASHBOARD" =~ ^[Yy]$ ]]; then
    echo "  ✅ Dashboard (http://localhost:8501) - Web interface"
else
    echo "  ⚪ Dashboard - Run setup.sh again to enable web UI"
fi

echo ""

echo -e "${PURPLE}💡 Tips:${NC}"
echo "  • View logs: docker compose logs -f"
echo "  • Stop services: docker compose down"
echo "  • Restart services: docker compose restart"
echo ""

echo -e "${GREEN}Ready to start trading! 🤖💰${NC}"
echo ""
