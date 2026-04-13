"""
Model definitions for the Backend API.

Each model file corresponds to a router file with the same name.
Models are organized by functional domain to match the API structure.
"""

# Account models
from .accounts import CredentialRequest, LeverageRequest, PositionModeRequest

# Archived bots models
from .archived_bots import (
    ArchivedBotListResponse,
    BotPerformanceResponse,
    BotSummary,
    DatabaseStatus,
    ExecutorInfo,
    ExecutorsResponse,
    OrderDetail,
    OrderHistoryResponse,
    OrderStatus,
    PerformanceMetrics,
    TradeDetail,
    TradeHistoryResponse,
)

# Backtesting models
from .backtesting import BacktestingConfig

# Bot orchestration models (bot lifecycle management)
from .bot_orchestration import (
    AllBotsStatusResponse,
    BotAction,
    BotHistoryRequest,
    BotHistoryResponse,
    BotStatus,
    ConfigureBotAction,
    ImportStrategyAction,
    MQTTStatus,
    ShortcutAction,
    StartBotAction,
    StopAndArchiveRequest,
    StopAndArchiveResponse,
    StopBotAction,
    V2ControllerDeployment,
)

# Connector models
from .connectors import (
    ConnectorConfigMapResponse,
    ConnectorInfo,
    ConnectorListResponse,
    ConnectorOrderTypesResponse,
    ConnectorTradingRulesResponse,
    TradingRule,
)

# Controller models
from .controllers import Controller, ControllerConfig, ControllerConfigResponse, ControllerResponse, ControllerType

# Docker models
from .docker import DockerImage

# Executor models
from .executors import (
    CreateExecutorRequest,
    CreateExecutorResponse,
    ExecutorDetailResponse,
    ExecutorFilterRequest,
    ExecutorResponse,
    ExecutorsSummaryResponse,
    StopExecutorRequest,
    StopExecutorResponse,
)

# Gateway models (consolidated)
from .gateway import (
    AddPoolRequest,
    AddTokenRequest,
    CreateWalletRequest,
    GatewayBalanceRequest,
    GatewayConfig,
    GatewayStatus,
    GatewayWalletCredential,
    GatewayWalletInfo,
    SendTransactionRequest,
    ShowPrivateKeyRequest,
)

# Gateway Trading models (Swap + CLMM only, AMM removed)
from .gateway_trading import (  # Swap models; CLMM models; Pool info models; Pool listing models
    CLMMAddLiquidityRequest,
    CLMMClosePositionRequest,
    CLMMCollectFeesRequest,
    CLMMCollectFeesResponse,
    CLMMGetPositionInfoRequest,
    CLMMOpenPositionRequest,
    CLMMOpenPositionResponse,
    CLMMPoolBin,
    CLMMPoolInfoRequest,
    CLMMPoolInfoResponse,
    CLMMPoolListItem,
    CLMMPoolListResponse,
    CLMMPositionInfo,
    CLMMPositionsOwnedRequest,
    CLMMRemoveLiquidityRequest,
    GetPoolInfoRequest,
    PoolInfo,
    SwapExecuteRequest,
    SwapExecuteResponse,
    SwapQuoteRequest,
    SwapQuoteResponse,
    TimeBasedMetrics,
)

# Market data models
from .market_data import (  # New enhanced market data models; Trading pair management models
    ActiveFeedInfo,
    ActiveFeedsResponse,
    AddTradingPairRequest,
    CandleData,
    CandlesResponse,
    FundingInfoRequest,
    FundingInfoResponse,
    MarketDataSettings,
    OrderBookLevel,
    OrderBookQueryRequest,
    OrderBookQueryResult,
    OrderBookRequest,
    OrderBookResponse,
    PriceData,
    PriceForQuoteVolumeRequest,
    PriceForVolumeRequest,
    PriceRequest,
    PricesResponse,
    QuoteVolumeForPriceRequest,
    RemoveTradingPairRequest,
    SupportedOrderTypesResponse,
    TradingPairResponse,
    TradingRulesResponse,
    VolumeForPriceRequest,
    VWAPForVolumeRequest,
)

# Pagination models
from .pagination import PaginatedResponse, PaginationParams, TimeRangePaginationParams

# Portfolio models
from .portfolio import (
    AccountDistribution,
    AccountPortfolioState,
    AccountsDistributionResponse,
    ConnectorBalances,
    HistoricalPortfolioState,
    PortfolioDistributionResponse,
    PortfolioHistoryFilters,
    PortfolioStateResponse,
    TokenBalance,
    TokenDistribution,
)

# Rate Oracle models
from .rate_oracle import (
    GlobalTokenConfig,
    RateOracleConfig,
    RateOracleConfigResponse,
    RateOracleConfigUpdateRequest,
    RateOracleConfigUpdateResponse,
    RateOracleSourceConfig,
    RateOracleSourceEnum,
    RateRequest,
    RateResponse,
    SingleRateResponse,
)

# Script models
from .scripts import Script, ScriptConfig, ScriptConfigResponse, ScriptResponse

# Trading models
from .trading import (
    AccountBalance,
    ActiveOrderFilterRequest,
    ActiveOrdersResponse,
    ConnectorBalance,
    FundingPaymentFilterRequest,
    OrderFilterRequest,
    OrderInfo,
    OrderSummary,
    OrderTypesResponse,
    PortfolioState,
    PositionFilterRequest,
    TokenInfo,
    TradeFilterRequest,
    TradeInfo,
    TradeRequest,
    TradeResponse,
    TradingRulesInfo,
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
    # Trading pair management models
    "AddTradingPairRequest",
    "RemoveTradingPairRequest",
    "TradingPairResponse",
    # Account models
    "LeverageRequest",
    "PositionModeRequest",
    "CredentialRequest",
    # Docker models
    "DockerImage",
    # Gateway models
    "GatewayConfig",
    "GatewayStatus",
    "CreateWalletRequest",
    "ShowPrivateKeyRequest",
    "SendTransactionRequest",
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
    # Rate Oracle models
    "RateOracleSourceEnum",
    "GlobalTokenConfig",
    "RateOracleSourceConfig",
    "RateOracleConfig",
    "RateOracleConfigResponse",
    "RateOracleConfigUpdateRequest",
    "RateOracleConfigUpdateResponse",
    "RateRequest",
    "RateResponse",
    "SingleRateResponse",
    # Executor models
    "CreateExecutorRequest",
    "CreateExecutorResponse",
    "StopExecutorRequest",
    "StopExecutorResponse",
    "ExecutorFilterRequest",
    "ExecutorResponse",
    "ExecutorDetailResponse",
    "ExecutorsSummaryResponse",
]
