#!/bin/bash
# Hummingbot API Setup - Creates .env with sensible defaults

set -e

echo "Hummingbot API Setup"
echo ""

# Check if .env file already exists
if [ -f ".env" ]; then
    echo ".env file already exists. Skipping setup."
    echo ""
    echo ""
    exit 0
fi

# Only prompt for password (most common customization)
read -p "API password [default: admin]: " PASSWORD
PASSWORD=${PASSWORD:-admin}

read -p "Config password [default: admin]: " CONFIG_PASSWORD
CONFIG_PASSWORD=${CONFIG_PASSWORD:-admin}

# Create .env with sensible defaults
cat > .env << EOF
# Hummingbot API Configuration
USERNAME=admin
PASSWORD=$PASSWORD
CONFIG_PASSWORD=$CONFIG_PASSWORD
DEBUG_MODE=false

# MQTT Broker
BROKER_HOST=localhost
BROKER_PORT=1883
BROKER_USERNAME=admin
BROKER_PASSWORD=password

# Database (auto-configured by docker-compose)
DATABASE_URL=postgresql+asyncpg://hbot:hummingbot-api@localhost:5432/hummingbot_api

# Gateway (optional)
GATEWAY_URL=http://localhost:15888
GATEWAY_PASSPHRASE=admin

# Paths
BOTS_PATH=$(pwd)
EOF

echo ""
echo ".env created successfully!"
echo ""
echo "Next steps:"
echo "  make deploy    # Start all services"
echo "  make run       # Run API locally (dev mode)"
echo ""
