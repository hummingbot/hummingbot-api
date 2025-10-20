#!/bin/bash

# Database Troubleshooting Script
# This script helps diagnose and fix PostgreSQL database initialization issues

set -e

# Colors for better output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "🔧 PostgreSQL Database Troubleshooting Tool"
echo ""

# Check if PostgreSQL container is running
echo -e "${YELLOW}🔍 Checking PostgreSQL container status...${NC}"
if ! docker ps | grep -q hummingbot-postgres; then
    echo -e "${RED}❌ PostgreSQL container is not running!${NC}"
    echo ""
    echo -e "${YELLOW}Starting PostgreSQL container...${NC}"
    docker compose up postgres -d
    sleep 5
fi

# Wait for PostgreSQL to be ready
echo -e "${YELLOW}⏳ Waiting for PostgreSQL to be ready...${NC}"
MAX_RETRIES=30
RETRY_COUNT=0
DB_READY=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if docker exec hummingbot-postgres pg_isready -U postgres > /dev/null 2>&1; then
        DB_READY=true
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo -ne "\r${YELLOW}⏳ Waiting... ($RETRY_COUNT/$MAX_RETRIES)${NC}"
    sleep 2
done
echo ""

if [ "$DB_READY" = false ]; then
    echo -e "${RED}❌ PostgreSQL is not responding. Check logs:${NC}"
    echo "docker logs hummingbot-postgres"
    exit 1
fi

echo -e "${GREEN}✅ PostgreSQL is running!${NC}"
echo ""

# Check current database state
echo -e "${YELLOW}🔍 Checking database configuration...${NC}"

# Check if hbot user exists
USER_EXISTS=$(docker exec hummingbot-postgres psql -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='hbot'" 2>/dev/null)

# Check if database exists
DB_EXISTS=$(docker exec hummingbot-postgres psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='hummingbot_api'" 2>/dev/null)

echo ""
echo -e "${BLUE}Current Status:${NC}"
if [ "$USER_EXISTS" = "1" ]; then
    echo -e "  User 'hbot': ${GREEN}✓ EXISTS${NC}"
else
    echo -e "  User 'hbot': ${RED}✗ MISSING${NC}"
fi

if [ "$DB_EXISTS" = "1" ]; then
    echo -e "  Database 'hummingbot_api': ${GREEN}✓ EXISTS${NC}"
else
    echo -e "  Database 'hummingbot_api': ${RED}✗ MISSING${NC}"
fi
echo ""

# Fix if needed
if [ "$USER_EXISTS" != "1" ] || [ "$DB_EXISTS" != "1" ]; then
    echo -e "${YELLOW}🔧 Fixing database configuration...${NC}"
    echo ""

    # Check if init-db.sql exists
    if [ ! -f "init-db.sql" ]; then
        echo -e "${RED}❌ init-db.sql file not found!${NC}"
        echo "Please ensure you're running this script from the hummingbot-api directory."
        exit 1
    fi

    # Run initialization script
    echo -e "${YELLOW}Running database initialization...${NC}"
    docker exec -i hummingbot-postgres psql -U postgres < init-db.sql

    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}✅ Database initialized successfully!${NC}"
    else
        echo ""
        echo -e "${RED}❌ Failed to initialize database${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✅ Database configuration is correct!${NC}"
fi

# Test connection with hbot user
echo ""
echo -e "${YELLOW}🧪 Testing connection with hbot user...${NC}"
if docker exec hummingbot-postgres psql -U hbot -d hummingbot_api -c "SELECT version();" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Connection successful!${NC}"
else
    echo -e "${RED}❌ Connection failed${NC}"
    echo ""
    echo -e "${YELLOW}Trying to fix permissions...${NC}"

    docker exec -i hummingbot-postgres psql -U postgres << 'EOF'
\c hummingbot_api
GRANT ALL ON SCHEMA public TO hbot;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO hbot;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO hbot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO hbot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO hbot;
EOF

    if docker exec hummingbot-postgres psql -U hbot -d hummingbot_api -c "SELECT version();" > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Permissions fixed! Connection successful!${NC}"
    else
        echo -e "${RED}❌ Still unable to connect. Manual intervention required.${NC}"
        exit 1
    fi
fi

echo ""
echo -e "${GREEN}🎉 Database is ready to use!${NC}"
echo ""
echo -e "${BLUE}Connection Details:${NC}"
echo "  Host: localhost"
echo "  Port: 5432"
echo "  Database: hummingbot_api"
echo "  User: hbot"
echo "  Password: hummingbot-api"
echo ""
echo -e "${YELLOW}You can now start the API with:${NC}"
echo "  make run"
echo "  or"
echo "  docker compose up -d"
echo ""