import secrets
from contextlib import asynccontextmanager
from typing import Annotated

import logfire
import logging
from dotenv import load_dotenv

# Load environment variables early
load_dotenv()

VERSION = "1.0.1"

# Monkey patch save_to_yml to prevent writes to library directory
def patched_save_to_yml(yml_path, cm):
    """Patched version of save_to_yml that prevents writes to library directory"""
    import logging
    logger = logging.getLogger(__name__)
    logger.debug(f"Skipping config write to {yml_path} (patched for API mode)")
    # Do nothing - this prevents the original function from trying to write to the library directory

# Apply the patch before importing hummingbot components
from hummingbot.client.config import config_helpers
config_helpers.save_to_yml = patched_save_to_yml

from hummingbot.core.rate_oracle.rate_oracle import RateOracle

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.client.config.config_crypt import ETHKeyFileSecretManger

from utils.security import BackendAPISecurity
from services.bots_orchestrator import BotsOrchestrator
from services.accounts_service import AccountsService
from services.docker_service import DockerService
from services.market_data_feed_manager import MarketDataFeedManager
from utils.bot_archiver import BotArchiver
from routers import (
    accounts,
    archived_bots,
    backtesting,
    bot_orchestration,
    connectors,
    controllers,
    docker,
    market_data,
    portfolio,
    scripts,
    trading
)

from config import settings


# Set up logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Enable debug logging for MQTT manager
logging.getLogger('services.mqtt_manager').setLevel(logging.DEBUG)


# Get settings from Pydantic Settings
username = settings.security.username
password = settings.security.password
debug_mode = settings.security.debug_mode

# Security setup
security = HTTPBasic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for the FastAPI application.
    Handles startup and shutdown events.
    """
    # Ensure password verification file exists and is valid
    secrets_manager = ETHKeyFileSecretManger(password=settings.security.config_password)
    if BackendAPISecurity.new_password_required() \
            or not BackendAPISecurity.validate_password(secrets_manager):
        BackendAPISecurity.store_password_verification(secrets_manager)
        logging.info("Created password verification file for master_account")

    # Initialize MarketDataProvider with empty connectors (will use non-trading connectors)
    market_data_provider = MarketDataProvider(connectors={})

    # Initialize MarketDataFeedManager with lifecycle management
    market_data_feed_manager = MarketDataFeedManager(
        market_data_provider=market_data_provider,
        rate_oracle=RateOracle.get_instance(),
        cleanup_interval=settings.market_data.cleanup_interval,
        feed_timeout=settings.market_data.feed_timeout
    )

    # Initialize services
    bots_orchestrator = BotsOrchestrator(
        broker_host=settings.broker.host,
        broker_port=settings.broker.port,
        broker_username=settings.broker.username,
        broker_password=settings.broker.password
    )

    accounts_service = AccountsService(
        account_update_interval=settings.app.account_update_interval,
        market_data_feed_manager=market_data_feed_manager
    )
    docker_service = DockerService()
    bot_archiver = BotArchiver(
        settings.aws.api_key,
        settings.aws.secret_key,
        settings.aws.s3_default_bucket_name
    )

    # Initialize database
    await accounts_service.ensure_db_initialized()

    # Store services in app state
    app.state.bots_orchestrator = bots_orchestrator
    app.state.accounts_service = accounts_service
    app.state.docker_service = docker_service
    app.state.bot_archiver = bot_archiver
    app.state.market_data_feed_manager = market_data_feed_manager

    # Start services
    bots_orchestrator.start()
    accounts_service.start()
    market_data_feed_manager.start()

    yield

    # Shutdown services
    bots_orchestrator.stop()
    await accounts_service.stop()

    # Stop market data feed manager (which will stop all feeds)
    market_data_feed_manager.stop()

    # Clean up docker service
    docker_service.cleanup()

    # Close database connections
    await accounts_service.db_manager.close()


# Initialize FastAPI with metadata and lifespan
app = FastAPI(
    title="Hummingbot API",
    description="API for managing Hummingbot trading instances",
    version=VERSION,
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Modify in production to specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logfire.configure(send_to_logfire="if-token-present", environment=settings.app.logfire_environment, service_name="hummingbot-api")
logfire.instrument_fastapi(app)

def auth_user(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """Authenticate user using HTTP Basic Auth"""
    current_username_bytes = credentials.username.encode("utf8")
    correct_username_bytes = f"{username}".encode("utf8")
    is_correct_username = secrets.compare_digest(
        current_username_bytes, correct_username_bytes
    )
    current_password_bytes = credentials.password.encode("utf8")
    correct_password_bytes = f"{password}".encode("utf8")
    is_correct_password = secrets.compare_digest(
        current_password_bytes, correct_password_bytes
    )
    if not (is_correct_username and is_correct_password) and not debug_mode:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# Include all routers with authentication
app.include_router(docker.router, dependencies=[Depends(auth_user)])
app.include_router(accounts.router, dependencies=[Depends(auth_user)])
app.include_router(connectors.router, dependencies=[Depends(auth_user)])
app.include_router(portfolio.router, dependencies=[Depends(auth_user)])
app.include_router(trading.router, dependencies=[Depends(auth_user)])
app.include_router(bot_orchestration.router, dependencies=[Depends(auth_user)])
app.include_router(controllers.router, dependencies=[Depends(auth_user)])
app.include_router(scripts.router, dependencies=[Depends(auth_user)])
app.include_router(market_data.router, dependencies=[Depends(auth_user)])
app.include_router(backtesting.router, dependencies=[Depends(auth_user)])
app.include_router(archived_bots.router, dependencies=[Depends(auth_user)])

@app.get("/")
async def root():
    """API root endpoint returning basic information."""
    return {
        "name": "Hummingbot API",
        "version": VERSION,
        "status": "running",
    }
