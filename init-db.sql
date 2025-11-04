-- Database Initialization Script
--
-- IMPORTANT: This script serves as a SAFETY NET for edge cases where PostgreSQL's
-- automatic initialization (via POSTGRES_USER/POSTGRES_DB env vars) doesn't complete.
--
-- In most cases, PostgreSQL will automatically create the user and database from the
-- environment variables. However, this script ensures proper initialization when:
-- - Volume data persists from incomplete initialization
-- - Container restarts interrupt the init process
-- - Manual database operations left the system in an inconsistent state
--
-- This script is safe to run multiple times (idempotent)

-- Create the hbot user if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'hbot') THEN
        CREATE ROLE hbot WITH LOGIN PASSWORD 'hummingbot-api';
        RAISE NOTICE 'User hbot created successfully';
    ELSE
        RAISE NOTICE 'User hbot already exists';
    END IF;
END
$$;

-- Create the database if it doesn't exist
SELECT 'CREATE DATABASE hummingbot_api OWNER hbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hummingbot_api')\gexec

-- Grant all privileges on the database
GRANT ALL PRIVILEGES ON DATABASE hummingbot_api TO hbot;

-- Connect to the database and grant schema privileges
\c hummingbot_api hbot

-- Grant privileges on the public schema
GRANT ALL ON SCHEMA public TO hbot;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO hbot;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO hbot;

-- Set default privileges for future objects
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO hbot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO hbot;