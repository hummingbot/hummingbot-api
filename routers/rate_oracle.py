"""
Rate Oracle router for managing rate oracle configuration and retrieving rates.

Provides CRUD endpoints for rate_oracle_source and global_token configuration,
with persistence to conf_client.yml.
"""

from typing import List
from decimal import Decimal

from fastapi import APIRouter, Request, HTTPException
from hummingbot.core.rate_oracle.rate_oracle import RateOracle, RATE_ORACLE_SOURCES

from models.rate_oracle import (
    RateOracleConfig,
    RateOracleConfigResponse,
    RateOracleConfigUpdateRequest,
    RateOracleConfigUpdateResponse,
    RateOracleSourceConfig,
    GlobalTokenConfig,
    RateRequest,
    RateResponse,
    SingleRateResponse,
)
from utils.file_system import FileSystemUtil

router = APIRouter(tags=["Rate Oracle"], prefix="/rate-oracle")

# Path to conf_client.yml relative to the FileSystemUtil base_path ("bots")
CONF_CLIENT_PATH = "credentials/master_account/conf_client.yml"


def get_rate_oracle(request: Request) -> RateOracle:
    """Get RateOracle instance from the market data feed manager."""
    return request.app.state.market_data_feed_manager.rate_oracle


def get_file_system_util() -> FileSystemUtil:
    """Get FileSystemUtil instance."""
    return FileSystemUtil()


@router.get("/sources", response_model=List[str])
async def get_available_sources():
    """
    Get list of all available rate oracle sources.

    Returns:
        List of available source names that can be configured
    """
    return list(RATE_ORACLE_SOURCES.keys())


@router.get("/config", response_model=RateOracleConfigResponse)
async def get_rate_oracle_config(request: Request):
    """
    Get current rate oracle configuration.

    Returns the current rate_oracle_source and global_token settings,
    along with the list of available sources.

    Returns:
        Current rate oracle configuration and available sources
    """
    try:
        fs_util = get_file_system_util()

        # Read current config from file
        config_data = fs_util.read_yaml_file(CONF_CLIENT_PATH)

        # Extract rate_oracle_source
        rate_oracle_source_data = config_data.get("rate_oracle_source", {})
        source_name = rate_oracle_source_data.get("name", "binance")

        # Extract global_token
        global_token_data = config_data.get("global_token", {})
        global_token_name = global_token_data.get("global_token_name", "USDT")
        global_token_symbol = global_token_data.get("global_token_symbol", "$")

        return RateOracleConfigResponse(
            rate_oracle_source=RateOracleSourceConfig(name=source_name),
            global_token=GlobalTokenConfig(
                global_token_name=global_token_name,
                global_token_symbol=global_token_symbol
            ),
            available_sources=list(RATE_ORACLE_SOURCES.keys())
        )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Configuration file not found: {CONF_CLIENT_PATH}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error reading configuration: {str(e)}"
        )


@router.put("/config", response_model=RateOracleConfigUpdateResponse)
async def update_rate_oracle_config(
    request: Request,
    update_request: RateOracleConfigUpdateRequest
):
    """
    Update rate oracle configuration.

    Updates rate_oracle_source and/or global_token settings. Changes are:
    1. Applied to the running RateOracle instance immediately
    2. Persisted to conf_client.yml

    Args:
        update_request: Configuration updates to apply

    Returns:
        Updated configuration with success status
    """
    try:
        fs_util = get_file_system_util()
        rate_oracle = get_rate_oracle(request)

        # Read current config
        config_data = fs_util.read_yaml_file(CONF_CLIENT_PATH)

        # Track if we made changes
        changes_made = []

        # Update rate_oracle_source if provided
        if update_request.rate_oracle_source is not None:
            new_source_name = update_request.rate_oracle_source.name.value

            # Validate source exists
            if new_source_name not in RATE_ORACLE_SOURCES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid rate oracle source: {new_source_name}. "
                           f"Available sources: {list(RATE_ORACLE_SOURCES.keys())}"
                )

            # Update config data
            if "rate_oracle_source" not in config_data:
                config_data["rate_oracle_source"] = {}
            config_data["rate_oracle_source"]["name"] = new_source_name

            # Update running RateOracle instance
            new_source_class = RATE_ORACLE_SOURCES[new_source_name]
            rate_oracle.source = new_source_class()

            changes_made.append(f"rate_oracle_source updated to {new_source_name}")

        # Update global_token if provided
        if update_request.global_token is not None:
            if "global_token" not in config_data:
                config_data["global_token"] = {}

            if update_request.global_token.global_token_name is not None:
                config_data["global_token"]["global_token_name"] = update_request.global_token.global_token_name
                # Update RateOracle quote token
                rate_oracle.quote_token = update_request.global_token.global_token_name
                changes_made.append(f"global_token_name updated to {update_request.global_token.global_token_name}")

            if update_request.global_token.global_token_symbol is not None:
                config_data["global_token"]["global_token_symbol"] = update_request.global_token.global_token_symbol
                changes_made.append(f"global_token_symbol updated to {update_request.global_token.global_token_symbol}")

        # Persist changes to file
        if changes_made:
            fs_util.dump_dict_to_yaml(CONF_CLIENT_PATH, config_data)

        # Build response
        current_source = config_data.get("rate_oracle_source", {}).get("name", "binance")
        current_global_token = config_data.get("global_token", {})

        return RateOracleConfigUpdateResponse(
            success=True,
            message="; ".join(changes_made) if changes_made else "No changes made",
            config=RateOracleConfig(
                rate_oracle_source=RateOracleSourceConfig(name=current_source),
                global_token=GlobalTokenConfig(
                    global_token_name=current_global_token.get("global_token_name", "USDT"),
                    global_token_symbol=current_global_token.get("global_token_symbol", "$")
                )
            )
        )

    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Configuration file not found: {CONF_CLIENT_PATH}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating configuration: {str(e)}"
        )


@router.post("/rates", response_model=RateResponse)
async def get_rates(request: Request, rate_request: RateRequest):
    """
    Get rates for specified trading pairs.

    Uses the configured rate oracle source to fetch current rates.

    Args:
        rate_request: List of trading pairs to get rates for

    Returns:
        Rates for the requested trading pairs
    """
    try:
        rate_oracle = get_rate_oracle(request)

        rates = {}
        for pair in rate_request.trading_pairs:
            try:
                rate = rate_oracle.get_pair_rate(pair)
                rates[pair] = float(rate) if rate and rate != Decimal("0") else None
            except Exception:
                rates[pair] = None

        return RateResponse(
            source=rate_oracle.source.name,
            quote_token=rate_oracle.quote_token,
            rates=rates
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching rates: {str(e)}"
        )


@router.get("/rate/{trading_pair}", response_model=SingleRateResponse)
async def get_single_rate(request: Request, trading_pair: str):
    """
    Get rate for a single trading pair.

    Args:
        trading_pair: Trading pair in format BASE-QUOTE (e.g., BTC-USDT)

    Returns:
        Rate for the specified trading pair
    """
    try:
        rate_oracle = get_rate_oracle(request)

        rate = rate_oracle.get_pair_rate(trading_pair)
        rate_value = float(rate) if rate and rate != Decimal("0") else None

        return SingleRateResponse(
            trading_pair=trading_pair,
            rate=rate_value,
            source=rate_oracle.source.name,
            quote_token=rate_oracle.quote_token
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching rate for {trading_pair}: {str(e)}"
        )


@router.get("/rate-async/{trading_pair}", response_model=SingleRateResponse)
async def get_rate_async(request: Request, trading_pair: str):
    """
    Get rate for a trading pair using async fetch (direct from exchange).

    This bypasses the cached prices and fetches directly from the source.
    Useful when cached data may be stale or not yet initialized.

    Args:
        trading_pair: Trading pair in format BASE-QUOTE (e.g., BTC-USDT)

    Returns:
        Rate for the specified trading pair
    """
    try:
        rate_oracle = get_rate_oracle(request)

        rate = await rate_oracle.rate_async(trading_pair)
        rate_value = float(rate) if rate and rate != Decimal("0") else None

        return SingleRateResponse(
            trading_pair=trading_pair,
            rate=rate_value,
            source=rate_oracle.source.name,
            quote_token=rate_oracle.quote_token
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching async rate for {trading_pair}: {str(e)}"
        )


@router.get("/prices")
async def get_cached_prices(request: Request):
    """
    Get all cached prices from the rate oracle.

    Returns the complete price dictionary that the rate oracle has fetched
    from its configured source.

    Returns:
        Dictionary of all cached prices
    """
    try:
        rate_oracle = get_rate_oracle(request)

        prices = rate_oracle.prices
        # Convert Decimal to float for JSON serialization
        float_prices = {pair: float(price) for pair, price in prices.items()}

        return {
            "source": rate_oracle.source.name,
            "quote_token": rate_oracle.quote_token,
            "prices_count": len(float_prices),
            "prices": float_prices
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching cached prices: {str(e)}"
        )
