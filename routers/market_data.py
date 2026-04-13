import asyncio
import logging
import time

from fastapi import APIRouter, Request, HTTPException, Depends
from hummingbot.data_feed.candles_feed.data_types import HistoricalCandlesConfig, CandlesConfig
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory, UnsupportedConnectorException

from config import settings
from models.market_data import CandlesConfigRequest
from services.market_data_service import MarketDataService
from models import (
    PriceRequest, PricesResponse, FundingInfoRequest, FundingInfoResponse,
    OrderBookRequest, OrderBookResponse, OrderBookLevel,
    VolumeForPriceRequest, PriceForVolumeRequest, QuoteVolumeForPriceRequest,
    PriceForQuoteVolumeRequest, VWAPForVolumeRequest, OrderBookQueryResult,
    AddTradingPairRequest, RemoveTradingPairRequest, TradingPairResponse
)
from deps import get_market_data_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Market Data"], prefix="/market-data")


@router.post("/candles")
async def get_candles(request: Request, candles_config: CandlesConfigRequest):
    """
    Get real-time candles data for a specific trading pair.

    This endpoint uses the MarketDataProvider to get or create a candles feed that will
    automatically start and maintain real-time updates. Subsequent requests with the same
    configuration will reuse the existing feed for up-to-date data.

    Args:
        request: FastAPI request object
        candles_config: Configuration for the candles including connector, trading_pair, interval, and max_records

    Returns:
        Real-time candles data or error message
    """
    available = list(CandlesFactory._candles_map.keys())
    if candles_config.connector_name not in CandlesFactory._candles_map:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported connector '{candles_config.connector_name}'. "
                   f"Available connectors: {available}"
        )

    if "-" not in candles_config.trading_pair:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trading pair format '{candles_config.trading_pair}'. "
                   f"Expected format: BASE-QUOTE (e.g., BTC-USDT)"
        )

    try:
        market_data_service: MarketDataService = request.app.state.market_data_service

        candles_cfg = CandlesConfig(
            connector=candles_config.connector_name, trading_pair=candles_config.trading_pair,
            interval=candles_config.interval, max_records=candles_config.max_records)
        candles_feed = market_data_service.get_candles_feed(candles_cfg)

        # Wait for the candles feed to be ready with a timeout
        timeout = settings.market_data.candles_ready_timeout
        start = time.time()
        while not candles_feed.ready:
            if time.time() - start > timeout:
                # Clean up the stale feed so it doesn't stay cached
                market_data_service.stop_candle_feed(candles_cfg)
                raise HTTPException(
                    status_code=504,
                    detail=f"Candle feed for {candles_config.connector_name} "
                           f"{candles_config.trading_pair} did not become ready within "
                           f"{timeout}s. The trading pair may not exist on this exchange."
                )
            await asyncio.sleep(0.1)

        df = candles_feed.candles_df

        if df is not None and not df.empty:
            df = df.tail(candles_config.max_records)
            df = df.drop_duplicates(subset=["timestamp"], keep="last")
            return df.to_dict(orient="records")
        else:
            raise HTTPException(status_code=404, detail="No candles data available")

    except HTTPException:
        raise
    except UnsupportedConnectorException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error fetching candles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error fetching candles: {str(e)}")


@router.post("/historical-candles")
async def get_historical_candles(request: Request, config: HistoricalCandlesConfig):
    """
    Get historical candles data for a specific trading pair.

    Args:
        config: Configuration for historical candles including connector, trading pair, interval, start and end time

    Returns:
        Historical candles data or error message
    """
    available = list(CandlesFactory._candles_map.keys())
    if config.connector_name not in CandlesFactory._candles_map:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported connector '{config.connector_name}'. "
                   f"Available connectors: {available}"
        )

    if "-" not in config.trading_pair:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trading pair format '{config.trading_pair}'. "
                   f"Expected format: BASE-QUOTE (e.g., BTC-USDT)"
        )

    try:
        market_data_service: MarketDataService = request.app.state.market_data_service

        candles_config = CandlesConfig(
            connector=config.connector_name,
            trading_pair=config.trading_pair,
            interval=config.interval
        )

        candles = market_data_service.get_candles_feed(candles_config)

        timeout = settings.market_data.candles_ready_timeout
        historical_data = await asyncio.wait_for(
            candles.get_historical_candles(config=config),
            timeout=timeout
        )

        if historical_data is not None and not historical_data.empty:
            return historical_data.to_dict(orient="records")
        else:
            raise HTTPException(status_code=404, detail="No historical data available")

    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Historical candles request for {config.connector_name} "
                   f"{config.trading_pair} timed out after "
                   f"{settings.market_data.candles_ready_timeout}s. "
                   f"The trading pair may not exist or the time range may be too large."
        )
    except UnsupportedConnectorException as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid parameters: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error fetching historical candles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error fetching historical candles: {str(e)}")


@router.get("/active-feeds")
async def get_active_feeds(request: Request):
    """
    Get information about currently active market data feeds.
    
    Args:
        request: FastAPI request object to access application state
        
    Returns:
        Dictionary with active feeds information including last access times and expiration
    """
    try:
        market_data_service: MarketDataService = request.app.state.market_data_service
        return market_data_service.get_active_feeds_info()
    except Exception as e:
        return {"error": str(e)}


@router.get("/settings")
async def get_market_data_settings():
    """
    Get current market data settings for debugging.
    
    Returns:
        Dictionary with current market data configuration including cleanup and timeout settings
    """
    from config import settings
    return {
        "cleanup_interval": settings.market_data.cleanup_interval,
        "feed_timeout": settings.market_data.feed_timeout,
        "description": "cleanup_interval: seconds between cleanup runs, feed_timeout: seconds before unused feeds expire"
    }


@router.get("/available-candle-connectors")
async def get_available_candle_connectors():
    """
    Get list of available connectors that support candle data feeds.
    
    Returns:
        List of connector names that can be used for fetching candle data
    """
    return list(CandlesFactory._candles_map.keys())


# Enhanced Market Data Endpoints

@router.post("/prices", response_model=PricesResponse)
async def get_prices(
    request: PriceRequest,
    market_data_manager: MarketDataService = Depends(get_market_data_service)
):
    """
    Get current prices for specified trading pairs from a connector.
    
    Args:
        request: Price request with connector name and trading pairs
        market_data_manager: Injected market data feed manager
        
    Returns:
        Current prices for the specified trading pairs
        
    Raises:
        HTTPException: 500 if there's an error fetching prices
    """
    try:
        prices = await market_data_manager.get_prices(
            request.connector_name, 
            request.trading_pairs
        )
        
        if "error" in prices:
            raise HTTPException(status_code=500, detail=prices["error"])
            
        return PricesResponse(
            connector=request.connector_name,
            prices=prices,
            timestamp=time.time()
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching prices: {str(e)}")


@router.post("/funding-info", response_model=FundingInfoResponse)
async def get_funding_info(
    request: FundingInfoRequest,
    market_data_manager: MarketDataService = Depends(get_market_data_service)
):
    """
    Get funding information for a perpetual trading pair.
    
    Args:
        request: Funding info request with connector name and trading pair
        market_data_manager: Injected market data feed manager
        
    Returns:
        Funding information including rates, timestamps, and prices
        
    Raises:
        HTTPException: 400 for non-perpetual connectors, 500 for other errors
    """
    try:
        if "_perpetual" not in request.connector_name.lower():
            raise HTTPException(status_code=400, detail="Funding info is only available for perpetual trading pairs.")
        funding_info = await market_data_manager.get_funding_info(
            request.connector_name, 
            request.trading_pair
        )
        
        if "error" in funding_info:
            if "not supported" in funding_info["error"]:
                raise HTTPException(status_code=400, detail=funding_info["error"])
            else:
                raise HTTPException(status_code=500, detail=funding_info["error"])
            
        return FundingInfoResponse(**funding_info)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching funding info: {str(e)}")


@router.post("/order-book", response_model=OrderBookResponse)
async def get_order_book(
    request: OrderBookRequest,
    market_data_manager: MarketDataService = Depends(get_market_data_service)
):
    """
    Get order book snapshot with specified depth.
    
    Args:
        request: Order book request with connector, trading pair, and depth
        market_data_manager: Injected market data feed manager
        
    Returns:
        Order book snapshot with bids and asks
        
    Raises:
        HTTPException: 500 if there's an error fetching order book
    """
    try:
        order_book_data = await market_data_manager.get_order_book_data(
            request.connector_name,
            request.trading_pair,
            request.depth
        )
        
        if "error" in order_book_data:
            raise HTTPException(status_code=500, detail=order_book_data["error"])
            
        # Convert to response format - data comes as [price, amount] lists
        bids = [OrderBookLevel(price=bid[0], amount=bid[1]) for bid in order_book_data["bids"]]
        asks = [OrderBookLevel(price=ask[0], amount=ask[1]) for ask in order_book_data["asks"]]
        
        return OrderBookResponse(
            trading_pair=order_book_data["trading_pair"],
            bids=bids,
            asks=asks,
            timestamp=order_book_data["timestamp"]
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching order book: {str(e)}")


# Order Book Query Endpoints

@router.post("/order-book/price-for-volume", response_model=OrderBookQueryResult)
async def get_price_for_volume(
    request: PriceForVolumeRequest,
    market_data_manager: MarketDataService = Depends(get_market_data_service)
):
    """
    Get the price required to fill a specific volume on the order book.
    
    Args:
        request: Request with connector, trading pair, volume, and side
        market_data_manager: Injected market data feed manager
        
    Returns:
        Order book query result with price and volume information
    """
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            volume=request.volume
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
            
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


@router.post("/order-book/volume-for-price", response_model=OrderBookQueryResult)
async def get_volume_for_price(
    request: VolumeForPriceRequest,
    market_data_manager: MarketDataService = Depends(get_market_data_service)
):
    """
    Get the volume available at a specific price level on the order book.
    
    Args:
        request: Request with connector, trading pair, price, and side
        market_data_manager: Injected market data feed manager
        
    Returns:
        Order book query result with volume information
    """
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            price=request.price
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
            
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


@router.post("/order-book/price-for-quote-volume", response_model=OrderBookQueryResult)
async def get_price_for_quote_volume(
    request: PriceForQuoteVolumeRequest,
    market_data_manager: MarketDataService = Depends(get_market_data_service)
):
    """
    Get the price required to fill a specific quote volume on the order book.
    
    Args:
        request: Request with connector, trading pair, quote volume, and side
        market_data_manager: Injected market data feed manager
        
    Returns:
        Order book query result with price and volume information
    """
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            quote_volume=request.quote_volume
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
            
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


@router.post("/order-book/quote-volume-for-price", response_model=OrderBookQueryResult)
async def get_quote_volume_for_price(
    request: QuoteVolumeForPriceRequest,
    market_data_manager: MarketDataService = Depends(get_market_data_service)
):
    """
    Get the quote volume available at a specific price level on the order book.
    
    Args:
        request: Request with connector, trading pair, price, and side
        market_data_manager: Injected market data feed manager
        
    Returns:
        Order book query result with quote volume information
    """
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            quote_price=request.price
        )
        
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
            
        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


@router.post("/order-book/vwap-for-volume", response_model=OrderBookQueryResult)
async def get_vwap_for_volume(
    request: VWAPForVolumeRequest,
    market_data_manager: MarketDataService = Depends(get_market_data_service)
):
    """
    Get the VWAP (Volume Weighted Average Price) for a specific volume on the order book.
    
    Args:
        request: Request with connector, trading pair, volume, and side
        market_data_manager: Injected market data feed manager
        
    Returns:
        Order book query result with VWAP information
    """
    try:
        result = await market_data_manager.get_order_book_query_result(
            request.connector_name,
            request.trading_pair,
            request.is_buy,
            vwap_volume=request.volume
        )

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        return OrderBookQueryResult(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in order book query: {str(e)}")


# Trading Pair Management Endpoints

@router.post("/trading-pair/add", response_model=TradingPairResponse)
async def add_trading_pair(
    request: AddTradingPairRequest,
    market_data_service: MarketDataService = Depends(get_market_data_service)
):
    """
    Initialize order book for a trading pair.

    This endpoint dynamically adds a trading pair to a connector's order book tracker.
    It uses the best available connector (trading connectors are preferred over data connectors).

    Args:
        request: Request with connector name, trading pair, optional account name, and timeout

    Returns:
        TradingPairResponse with success status and message

    Raises:
        HTTPException: 500 if initialization fails
    """
    try:
        success = await market_data_service.initialize_order_book(
            connector_name=request.connector_name,
            trading_pair=request.trading_pair,
            account_name=request.account_name,
            timeout=request.timeout
        )

        if success:
            return TradingPairResponse(
                success=True,
                connector_name=request.connector_name,
                trading_pair=request.trading_pair,
                message=f"Order book initialized for {request.trading_pair}"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize order book for {request.trading_pair}"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error initializing order book: {str(e)}"
        )


@router.post("/trading-pair/remove", response_model=TradingPairResponse)
async def remove_trading_pair(
    request: RemoveTradingPairRequest,
    market_data_service: MarketDataService = Depends(get_market_data_service)
):
    """
    Remove a trading pair from order book tracking.

    This endpoint removes a trading pair from a connector's order book tracker,
    cleaning up resources for pairs that are no longer needed.

    Args:
        request: Request with connector name, trading pair, and optional account name

    Returns:
        TradingPairResponse with success status and message

    Raises:
        HTTPException: 500 if removal fails
    """
    try:
        success = await market_data_service.remove_trading_pair(
            connector_name=request.connector_name,
            trading_pair=request.trading_pair,
            account_name=request.account_name
        )

        if success:
            return TradingPairResponse(
                success=True,
                connector_name=request.connector_name,
                trading_pair=request.trading_pair,
                message=f"Trading pair {request.trading_pair} removed"
            )
        else:
            return TradingPairResponse(
                success=False,
                connector_name=request.connector_name,
                trading_pair=request.trading_pair,
                message=f"Trading pair {request.trading_pair} not found or already removed"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error removing trading pair: {str(e)}"
        )


# Order Book Tracker Diagnostics Endpoints

@router.get("/order-book/diagnostics/{connector_name}")
async def get_order_book_diagnostics(
    connector_name: str,
    account_name: str = None,
    market_data_service: MarketDataService = Depends(get_market_data_service)
):
    """
    Get diagnostics for a connector's order book tracker.

    Returns detailed information about the order book tracker status including:
    - Task status (running/crashed)
    - WebSocket connection status
    - Metrics (messages processed, latency, etc.)
    - Current order book state

    Args:
        connector_name: The connector to diagnose (e.g., "binance")
        account_name: Optional account name for trading connectors

    Returns:
        Diagnostic information dictionary
    """
    try:
        diagnostics = market_data_service.get_order_book_tracker_diagnostics(
            connector_name=connector_name,
            account_name=account_name
        )
        return diagnostics
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting diagnostics: {str(e)}"
        )


@router.post("/order-book/restart/{connector_name}")
async def restart_order_book_tracker(
    connector_name: str,
    account_name: str = None,
    market_data_service: MarketDataService = Depends(get_market_data_service)
):
    """
    Restart the order book tracker for a connector.

    Use this endpoint when the order book is stale (WebSocket disconnected).
    This will:
    1. Stop the existing order book tracker
    2. Restart it with the same trading pairs
    3. Wait for the WebSocket to reconnect

    Args:
        connector_name: The connector to restart (e.g., "binance")
        account_name: Optional account name for trading connectors

    Returns:
        Restart status with success/failure and trading pairs
    """
    try:
        result = await market_data_service.restart_order_book_tracker(
            connector_name=connector_name,
            account_name=account_name
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error restarting order book tracker: {str(e)}"
        )


