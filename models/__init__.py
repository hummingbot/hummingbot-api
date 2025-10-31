"""
Model definitions for the Backend API.

Each model file corresponds to a router file with the same name.
Models are organized by functional domain to match the API structure.
"""

# Bot orchestration models (bot lifecycle management)
from .bot_orchestration import (
    BotAction,
    StartBotAction,
    StopBotAction,
    ImportStrategyAction,
    ConfigureBotAction,
    ShortcutAction,
    BotStatus,
    BotHistoryRequest,
    BotHistoryResponse,
    MQTTStatus,
    AllBotsStatusResponse,
    StopAndArchiveRequest,
    StopAndArchiveResponse,
    V2ScriptDeployment,
    V2ControllerDeployment,
)

# Trading models
from .trading import (
    TradeRequest,
    TradeResponse,
    TokenInfo,
    ConnectorBalance,
    AccountBalance,
    PortfolioState,
    OrderInfo,
    ActiveOrdersResponse,
    OrderSummary,
    TradeInfo,
    TradingRulesInfo,
    OrderTypesResponse,
    OrderFilterRequest,
    ActiveOrderFilterRequest,
    PositionFilterRequest,
    FundingPaymentFilterRequest,
    TradeFilterRequest,
)

# Controller models
from .controllers import (
    ControllerType,
    Controller,
    ControllerResponse,
    ControllerConfig,
    ControllerConfigResponse,
)

# Script models
from .scripts import (
    Script,
    ScriptResponse,
    ScriptConfig,
    ScriptConfigResponse,
)


# Market data models
from .market_data import (
    CandleData,
    CandlesResponse,
    ActiveFeedInfo,
    ActiveFeedsResponse,
    MarketDataSettings,
    TradingRulesResponse,
    SupportedOrderTypesResponse,
    # New enhanced market data models
    PriceRequest,
    PriceData,
    PricesResponse,
    FundingInfoRequest,
    FundingInfoResponse,
    OrderBookRequest,
    OrderBookLevel,
    OrderBookResponse,
    OrderBookQueryRequest,
    VolumeForPriceRequest,
    PriceForVolumeRequest,
    QuoteVolumeForPriceRequest,
    PriceForQuoteVolumeRequest,
    VWAPForVolumeRequest,
    OrderBookQueryResult,
)

# Account models
from .accounts import (
    LeverageRequest,
    PositionModeRequest,
    CredentialRequest,
)


# Docker models
from .docker import DockerImage

# Gateway models (consolidated)
from .gateway import (
    GatewayConfig,
    GatewayStatus,
    GatewayWalletCredential,
    GatewayWalletInfo,
    GatewayBalanceRequest,
    AddPoolRequest,
    AddTokenRequest,
)

# Backtesting models
from .backtesting import BacktestingConfig

# Pagination models
from .pagination import PaginatedResponse, PaginationParams, TimeRangePaginationParams

# Connector models
from .connectors import (
    ConnectorInfo,
    ConnectorConfigMapResponse,
    TradingRule,
    ConnectorTradingRulesResponse,
    ConnectorOrderTypesResponse,
    ConnectorListResponse,
)

# Gateway Trading models (Swap + CLMM only, AMM removed)
from .gateway_trading import (
    # Swap models
    SwapQuoteRequest,
    SwapQuoteResponse,
    SwapExecuteRequest,
    SwapExecuteResponse,
    # CLMM models
    CLMMOpenPositionRequest,
    CLMMOpenPositionResponse,
    CLMMAddLiquidityRequest,
    CLMMRemoveLiquidityRequest,
    CLMMClosePositionRequest,
    CLMMCollectFeesRequest,
    CLMMCollectFeesResponse,
    CLMMPositionsOwnedRequest,
    CLMMPositionInfo,
    CLMMGetPositionInfoRequest,
    CLMMPoolInfoRequest,
    CLMMPoolBin,
    CLMMPoolInfoResponse,
    # Pool info models
    GetPoolInfoRequest,
    PoolInfo,
    # Pool listing models
    TimeBasedMetrics,
    CLMMPoolListItem,
    CLMMPoolListResponse,
)

# Portfolio models
from .portfolio import (
    TokenBalance,
    ConnectorBalances,
    AccountPortfolioState,
    PortfolioStateResponse,
    TokenDistribution,
    PortfolioDistributionResponse,
    AccountDistribution,
    AccountsDistributionResponse,
    HistoricalPortfolioState,
    PortfolioHistoryFilters,
)

# Archived bots models
from .archived_bots import (
    OrderStatus,
    DatabaseStatus,
    BotSummary,
    PerformanceMetrics,
    TradeDetail,
    OrderDetail,
    ExecutorInfo,
    ArchivedBotListResponse,
    BotPerformanceResponse,
    TradeHistoryResponse,
    OrderHistoryResponse,
    ExecutorsResponse,
)

__all__ = [
    # Bot orchestration models
    "BotAction",
    "StartBotAction",
    "StopBotAction",
    "ImportStrategyAction",
    "ConfigureBotAction",
    "ShortcutAction",
    "BotStatus",
    "BotHistoryRequest",
    "BotHistoryResponse",
    "MQTTStatus",
    "AllBotsStatusResponse",
    "StopAndArchiveRequest",
    "StopAndArchiveResponse",
    "V2ScriptDeployment",
    "V2ControllerDeployment",
    # Trading models
    "TradeRequest",
    "TradeResponse",
    "TokenInfo",
    "ConnectorBalance",
    "AccountBalance",
    "PortfolioState",
    "OrderInfo",
    "ActiveOrdersResponse",
    "OrderSummary",
    "TradeInfo",
    "TradingRulesInfo",
    "OrderTypesResponse",
    "OrderFilterRequest",
    "ActiveOrderFilterRequest",
    "PositionFilterRequest",
    "FundingPaymentFilterRequest",
    "TradeFilterRequest",
    # Controller models
    "ControllerType",
    "Controller",
    "ControllerResponse",
    "ControllerConfig",
    "ControllerConfigResponse",
    # Script models
    "Script",
    "ScriptResponse",
    "ScriptConfig",
    "ScriptConfigResponse",
    # Market data models
    "CandleData",
    "CandlesResponse",
    "ActiveFeedInfo",
    "ActiveFeedsResponse",
    "MarketDataSettings",
    "TradingRulesResponse",
    "SupportedOrderTypesResponse",
    # New enhanced market data models
    "PriceRequest",
    "PriceData",
    "PricesResponse",
    "FundingInfoRequest",
    "FundingInfoResponse",
    "OrderBookRequest",
    "OrderBookLevel",
    "OrderBookResponse",
    "OrderBookQueryRequest",
    "VolumeForPriceRequest",
    "PriceForVolumeRequest",
    "QuoteVolumeForPriceRequest",
    "PriceForQuoteVolumeRequest",
    "VWAPForVolumeRequest",
    "OrderBookQueryResult",
    # Account models
    "LeverageRequest",
    "PositionModeRequest",
    "CredentialRequest",
    # Docker models
    "DockerImage",
    # Gateway models
    "GatewayConfig",
    "GatewayStatus",
    "GatewayWalletCredential",
    "GatewayWalletInfo",
    "GatewayBalanceRequest",
    "AddPoolRequest",
    "AddTokenRequest",
    # Backtesting models
    "BacktestingConfig",
    # Pagination models
    "PaginatedResponse",
    "PaginationParams",
    "TimeRangePaginationParams",
    # Connector models
    "ConnectorInfo",
    "ConnectorConfigMapResponse",
    "TradingRule",
    "ConnectorTradingRulesResponse",
    "ConnectorOrderTypesResponse",
    "ConnectorListResponse",
    # Gateway Trading models
    "SwapQuoteRequest",
    "SwapQuoteResponse",
    "SwapExecuteRequest",
    "SwapExecuteResponse",
    "CLMMOpenPositionRequest",
    "CLMMOpenPositionResponse",
    "CLMMAddLiquidityRequest",
    "CLMMRemoveLiquidityRequest",
    "CLMMClosePositionRequest",
    "CLMMCollectFeesRequest",
    "CLMMCollectFeesResponse",
    "CLMMPositionsOwnedRequest",
    "CLMMPositionInfo",
    "CLMMGetPositionInfoRequest",
    "CLMMPoolInfoRequest",
    "CLMMPoolBin",
    "CLMMPoolInfoResponse",
    "GetPoolInfoRequest",
    "PoolInfo",
    "TimeBasedMetrics",
    "CLMMPoolListItem",
    "CLMMPoolListResponse",
    # Portfolio models
    "TokenBalance",
    "ConnectorBalances",
    "AccountPortfolioState",
    "PortfolioStateResponse",
    "TokenDistribution",
    "PortfolioDistributionResponse",
    "AccountDistribution",
    "AccountsDistributionResponse",
    "HistoricalPortfolioState",
    "PortfolioHistoryFilters",
    # Archived bots models
    "OrderStatus",
    "DatabaseStatus",
    "BotSummary",
    "PerformanceMetrics",
    "TradeDetail",
    "OrderDetail",
    "ExecutorInfo",
    "ArchivedBotListResponse",
    "BotPerformanceResponse",
    "TradeHistoryResponse",
    "OrderHistoryResponse",
    "ExecutorsResponse",
]