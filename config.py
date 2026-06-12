import logging
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrokerSettings(BaseSettings):
    """MQTT Broker configuration for bot communication."""

    host: str = Field(default="localhost", description="MQTT broker host")
    port: int = Field(default=1883, description="MQTT broker port")
    username: str = Field(default="admin", description="MQTT broker username")
    password: str = Field(default="password", description="MQTT broker password")
    performance_dump_interval: int = Field(default=5, description="Controller performance dump interval in minutes")

    model_config = SettingsConfigDict(env_prefix="BROKER_", extra="ignore")


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    url: str = Field(
        default="postgresql+asyncpg://hbot:hummingbot-api@localhost:5432/hummingbot_api",
        description="Database connection URL"
    )

    model_config = SettingsConfigDict(env_prefix="DATABASE_", extra="ignore")


class MarketDataSettings(BaseSettings):
    """Market data feed manager configuration."""

    cleanup_interval: int = Field(
        default=300,
        description="How often to run feed cleanup in seconds"
    )
    feed_timeout: int = Field(
        default=600,
        description="How long to keep unused feeds alive in seconds"
    )
    candles_ready_timeout: int = Field(
        default=30,
        description="How long to wait for a candle feed to become ready in seconds"
    )
    ws_heartbeat_interval: int = Field(
        default=30,
        description="WebSocket heartbeat interval in seconds"
    )
    ws_min_update_interval: float = Field(
        default=0.25,
        description="Minimum allowed WebSocket subscription update interval in seconds"
    )
    ws_max_update_interval: float = Field(
        default=60.0,
        description="Maximum allowed WebSocket subscription update interval in seconds"
    )

    model_config = SettingsConfigDict(env_prefix="MARKET_DATA_", extra="ignore")


# Insecure default credential values (SEC-018), mapped to the environment variables that override them.
# They are kept only for local development convenience and MUST be overridden in production deployments.
_INSECURE_SECURITY_DEFAULTS = {
    "USERNAME": "admin",
    "PASSWORD": "admin",
    "CONFIG_PASSWORD": "a",
}


class SecuritySettings(BaseSettings):
    """Security and authentication configuration.

    All fields are read from environment variables without a prefix (or from .env):
    - USERNAME: API basic auth username (default "admin" — local development only, never use in production)
    - PASSWORD: API basic auth password (default "admin" — local development only, never use in production)
    - CONFIG_PASSWORD: password used to encrypt ALL connector credentials (default "a" — local development only,
      never use in production)
    - DEBUG_MODE: disables basic auth entirely when true (never enable in production)
    """

    username: str = Field(default="admin", description="API basic auth username (override via USERNAME in production)")
    password: str = Field(default="admin", description="API basic auth password (override via PASSWORD in production)")
    debug_mode: bool = Field(default=False, description="Enable debug mode (disables auth)")
    config_password: str = Field(
        default="a",
        description="Bot configuration encryption password (override via CONFIG_PASSWORD in production)"
    )

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore"  # Ignore extra environment variables
    )

    def insecure_defaults_in_use(self) -> List[str]:
        """Return the env var names of security settings still set to their insecure default values."""
        current_values = {"USERNAME": self.username, "PASSWORD": self.password, "CONFIG_PASSWORD": self.config_password}
        return [name for name, default in _INSECURE_SECURITY_DEFAULTS.items() if current_values[name] == default]


def warn_if_insecure_security_defaults(security: SecuritySettings) -> List[str]:
    """Emit a high-severity log if any security setting still uses its insecure default value (SEC-018).

    Returns the list of env var names that are still at their defaults (empty list when fully configured).
    """
    insecure = security.insecure_defaults_in_use()
    if insecure:
        logging.critical(
            "SECURITY WARNING: insecure default credentials in use for: %s. "
            "Anyone who can reach this API can authenticate with the default basic auth credentials, and all "
            "connector credentials are encrypted with a trivially guessable password. "
            "Set the USERNAME, PASSWORD and CONFIG_PASSWORD environment variables (e.g. in .env) before deploying "
            "to production. Do NOT run a production deployment with these defaults.",
            ", ".join(insecure),
        )
    return insecure


class AWSSettings(BaseSettings):
    """AWS configuration for S3 archiving."""

    api_key: str = Field(default="", description="AWS API key")
    secret_key: str = Field(default="", description="AWS secret key")
    s3_default_bucket_name: str = Field(default="", description="Default S3 bucket for archiving")

    model_config = SettingsConfigDict(env_prefix="AWS_", extra="ignore")


class GatewaySettings(BaseSettings):
    """Gateway service configuration."""

    url: str = Field(
        default="http://localhost:15888",
        description="Gateway service URL (use 'http://gateway:15888' when running in Docker)"
    )

    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore")


class AppSettings(BaseSettings):
    """Main application settings."""

    # Static paths
    controllers_path: str = "bots/conf/controllers"
    controllers_module: str = "bots.controllers"
    password_verification_path: str = "credentials/master_account/.password_verification"

    # Environment-configurable settings
    logfire_environment: str = Field(
        default="dev",
        description="Logfire environment name"
    )

    # Account state update interval
    account_update_interval: int = Field(
        default=5,
        description="How often to update account states in minutes"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )


class Settings(BaseSettings):
    """Combined application settings."""

    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    market_data: MarketDataSettings = Field(default_factory=MarketDataSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    aws: AWSSettings = Field(default_factory=AWSSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    app: AppSettings = Field(default_factory=AppSettings)

    # Direct banned_tokens field to handle env parsing
    banned_tokens: List[str] = Field(
        default=["NAV", "ARS", "ETHW", "ETHF"],
        description="List of banned trading tokens"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore"
    )


# Create global settings instance
settings = Settings()
