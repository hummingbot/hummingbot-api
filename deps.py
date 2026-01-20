from fastapi import Request
from services.bots_orchestrator import BotsOrchestrator
from services.accounts_service import AccountsService
from services.docker_service import DockerService
from services.gateway_service import GatewayService
from services.unified_connector_service import UnifiedConnectorService
from services.market_data_service import MarketDataService
from services.trading_service import TradingService
from services.executor_service import ExecutorService
from utils.bot_archiver import BotArchiver
from database import AsyncDatabaseManager


def get_bots_orchestrator(request: Request) -> BotsOrchestrator:
    """Get BotsOrchestrator service from app state."""
    return request.app.state.bots_orchestrator


def get_accounts_service(request: Request) -> AccountsService:
    """Get AccountsService from app state."""
    return request.app.state.accounts_service


def get_docker_service(request: Request) -> DockerService:
    """Get DockerService from app state."""
    return request.app.state.docker_service


def get_gateway_service(request: Request) -> GatewayService:
    """Get GatewayService from app state."""
    return request.app.state.gateway_service


def get_connector_service(request: Request) -> UnifiedConnectorService:
    """Get UnifiedConnectorService from app state."""
    return request.app.state.connector_service


def get_market_data_service(request: Request) -> MarketDataService:
    """Get MarketDataService from app state."""
    return request.app.state.market_data_service


def get_trading_service(request: Request) -> TradingService:
    """Get TradingService from app state."""
    return request.app.state.trading_service


def get_executor_service(request: Request) -> ExecutorService:
    """Get ExecutorService from app state."""
    return request.app.state.executor_service


def get_bot_archiver(request: Request) -> BotArchiver:
    """Get BotArchiver from app state."""
    return request.app.state.bot_archiver


def get_database_manager(request: Request) -> AsyncDatabaseManager:
    """Get AsyncDatabaseManager from app state."""
    return request.app.state.db_manager
