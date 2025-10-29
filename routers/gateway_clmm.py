"""
Gateway CLMM Router - Handles DEX CLMM liquidity operations via Hummingbot Gateway.
Supports CLMM connectors (Meteora, Raydium, Uniswap V3) for concentrated liquidity positions.
"""
import logging
from typing import List, Optional
from decimal import Decimal
import aiohttp

from fastapi import APIRouter, Depends, HTTPException, Query

from deps import get_accounts_service, get_database_manager
from services.accounts_service import AccountsService
from database import AsyncDatabaseManager
from database.repositories import GatewayCLMMRepository
from models import (
    CLMMOpenPositionRequest,
    CLMMOpenPositionResponse,
    CLMMAddLiquidityRequest,
    CLMMRemoveLiquidityRequest,
    CLMMClosePositionRequest,
    CLMMCollectFeesRequest,
    CLMMCollectFeesResponse,
    CLMMPositionsOwnedRequest,
    CLMMPositionInfo,
    CLMMPoolInfoResponse,
    CLMMPoolListItem,
    CLMMPoolListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway CLMM"], prefix="/gateway")


async def fetch_meteora_pools(
    page: int = 0,
    limit: int = 50,
    search_term: Optional[str] = None,
    sort_key: Optional[str] = "volume",
    order_by: Optional[str] = "desc",
    include_unknown: bool = True
) -> Optional[dict]:
    """
    Fetch available pools from Meteora API.

    Args:
        page: Page number (default: 0)
        limit: Results per page (default: 50)
        search_term: Search term to filter pools
        sort_key: Sort key (tvl, volume, feetvlratio, etc.)
        order_by: Sort order (asc, desc)
        include_unknown: Include pools with unverified tokens

    Returns:
        Dictionary with pools from Meteora API, or None if failed
    """
    try:
        url = "https://dlmm-api.meteora.ag/pair/all_by_groups"
        params = {
            "page": page,
            "limit": limit,
            "include_unknown": str(include_unknown).lower()  # Convert boolean to lowercase string
        }

        if search_term:
            params["search_term"] = search_term
        if sort_key:
            params["sort_key"] = sort_key
        if order_by:
            params["order_by"] = order_by

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers={"accept": "application/json"}) as response:
                response.raise_for_status()
                data = await response.json()
                return data
    except aiohttp.ClientError as e:
        logger.error(f"Failed to fetch pools from Meteora API: {e}")
        return None
    except Exception as e:
        logger.error(f"Error fetching Meteora pools: {e}", exc_info=True)
        return None


async def fetch_raydium_pool_info(pool_address: str) -> Optional[dict]:
    """
    Fetch pool info from Raydium API.

    Args:
        pool_address: Pool contract address

    Returns:
        Dictionary with pool info from Raydium API, or None if failed
    """
    try:
        url = f"https://api-v3.raydium.io/pools/line/position?id={pool_address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"accept": "application/json"}) as response:
                response.raise_for_status()
                data = await response.json()

                if not data.get("success"):
                    logger.error(f"Raydium API returned unsuccessful response: {data}")
                    return None

                return data
    except aiohttp.ClientError as e:
        logger.error(f"Failed to fetch pool info from Raydium API: {e}")
        return None
    except Exception as e:
        logger.error(f"Error fetching Raydium pool info: {e}", exc_info=True)
        return None


def transform_raydium_to_clmm_response(raydium_data: dict, pool_address: str) -> dict:
    """
    Transform Raydium API response to match Gateway's CLMMPoolInfoResponse format.

    Args:
        raydium_data: Response from Raydium API
        pool_address: Pool contract address

    Returns:
        Dictionary matching Gateway's pool info structure
    """
    pool_data = raydium_data.get("data", {})
    line_data = pool_data.get("line", [])

    if not line_data:
        raise ValueError("No liquidity bins found in Raydium pool data")

    # Sort bins by tick to find the active bin
    sorted_bins = sorted(line_data, key=lambda x: x.get("tick", 0))

    # Calculate active bin (the one with mid-range tick)
    # For Raydium, we need to determine the current active bin based on the pool state
    # We'll use the middle bin as a proxy for active bin
    active_bin_idx = len(sorted_bins) // 2
    active_bin = sorted_bins[active_bin_idx]

    # Calculate total liquidity across all bins
    total_base_liquidity = sum(Decimal(str(bin_data.get("liquidity", 0))) for bin_data in line_data)
    total_quote_liquidity = total_base_liquidity  # Approximation

    # Extract min and max ticks
    min_tick = sorted_bins[0].get("tick", 0) if sorted_bins else 0
    max_tick = sorted_bins[-1].get("tick", 0) if sorted_bins else 0

    # Convert ticks to bin IDs (assuming 1:1 mapping for simplicity)
    min_bin_id = min_tick
    max_bin_id = max_tick
    active_bin_id = active_bin.get("tick", 0)

    # Get current price from active bin
    current_price = Decimal(str(active_bin.get("price", 0)))

    # Transform bins to match Gateway format
    bins = []
    for bin_data in line_data[:100]:  # Limit to 100 bins for performance
        liquidity = Decimal(str(bin_data.get("liquidity", 0)))
        bins.append({
            "binId": bin_data.get("tick", 0),
            "price": Decimal(str(bin_data.get("price", 0))),
            "baseTokenAmount": liquidity,
            "quoteTokenAmount": liquidity  # Approximation
        })

    # Return in Gateway-compatible format
    return {
        "address": pool_address,
        "baseTokenAddress": "unknown",  # Not provided by Raydium API
        "quoteTokenAddress": "unknown",  # Not provided by Raydium API
        "binStep": 1,  # Default value, not provided by Raydium API
        "feePct": Decimal("0.25"),  # Typical Raydium CLMM fee
        "price": current_price,
        "baseTokenAmount": total_base_liquidity,
        "quoteTokenAmount": total_quote_liquidity,
        "activeBinId": active_bin_id,
        "dynamicFeePct": None,
        "minBinId": min_bin_id,
        "maxBinId": max_bin_id,
        "bins": bins
    }


def get_transaction_status_from_response(gateway_response: dict) -> str:
    """
    Determine transaction status from Gateway response.

    Gateway returns status field in the response:
    - status: 1 = confirmed
    - status: 0 = pending/submitted

    Returns:
        "CONFIRMED" if status == 1
        "SUBMITTED" if status == 0 or not present
    """
    status = gateway_response.get("status")

    # Status 1 means transaction is confirmed on-chain
    if status == 1:
        return "CONFIRMED"

    # Status 0 or missing means submitted but not confirmed yet
    return "SUBMITTED"


def get_native_gas_token(chain: str) -> str:
    """
    Get the native gas token symbol for a blockchain.

    Args:
        chain: Blockchain name (e.g., 'solana', 'ethereum', 'polygon')

    Returns:
        Gas token symbol (e.g., 'SOL', 'ETH', 'MATIC')
    """
    gas_token_map = {
        "solana": "SOL",
        "ethereum": "ETH",
        "polygon": "MATIC",
        "avalanche": "AVAX",
        "optimism": "ETH",
        "arbitrum": "ETH",
        "base": "ETH",
        "bsc": "BNB",
        "cronos": "CRO",
    }
    return gas_token_map.get(chain.lower(), "UNKNOWN")


async def _refresh_position_data(position, accounts_service: AccountsService, clmm_repo: GatewayCLMMRepository):
    """
    Refresh position data from Gateway and update database.

    This updates:
    - in_range status
    - liquidity amounts
    - pending fees
    - position status (if closed externally)
    """
    try:
        # Parse network to get chain and network name
        chain, network = accounts_service.gateway_client.parse_network_id(position.network)

        # Get wallet address for the position
        wallet_address = position.wallet_address

        # Get all positions for this pool and find our specific position
        try:
            positions_list = await accounts_service.gateway_client.clmm_positions_owned(
                connector=position.connector,
                network=network,
                wallet_address=wallet_address,
                pool_address=position.pool_address
            )

            # Find our specific position in the list
            result = None
            if isinstance(positions_list, list):
                for pos in positions_list:
                    if pos.get("address") == position.position_address:
                        result = pos
                        break

            # If position not found, it was closed externally
            if result is None:
                logger.info(f"Position {position.position_address} not found on Gateway, marking as CLOSED")
                await clmm_repo.close_position(position.position_address)
                return

        except Exception as e:
            # If we can't fetch positions, log error but don't mark as closed
            logger.error(f"Error fetching position from Gateway: {e}")
            return

        # Extract current state
        current_price = Decimal(str(result.get("price", 0)))
        lower_price = Decimal(str(result.get("lowerPrice", 0))) if result.get("lowerPrice") else Decimal("0")
        upper_price = Decimal(str(result.get("upperPrice", 0))) if result.get("upperPrice") else Decimal("0")

        # Calculate in_range status
        in_range = "UNKNOWN"
        if current_price > 0 and lower_price > 0 and upper_price > 0:
            if lower_price <= current_price <= upper_price:
                in_range = "IN_RANGE"
            else:
                in_range = "OUT_OF_RANGE"

        # Extract token amounts
        base_token_amount = Decimal(str(result.get("baseTokenAmount", 0)))
        quote_token_amount = Decimal(str(result.get("quoteTokenAmount", 0)))

        # Check if position has been closed (zero liquidity)
        if base_token_amount == 0 and quote_token_amount == 0:
            logger.info(f"Position {position.position_address} has zero liquidity, marking as CLOSED")
            await clmm_repo.close_position(position.position_address)
            return

        # Update liquidity amounts and in_range status
        await clmm_repo.update_position_liquidity(
            position_address=position.position_address,
            base_token_amount=base_token_amount,
            quote_token_amount=quote_token_amount,
            in_range=in_range
        )

        # Update pending fees if available
        base_fee_pending = Decimal(str(result.get("baseFeeAmount", 0)))
        quote_fee_pending = Decimal(str(result.get("quoteFeeAmount", 0)))

        if base_fee_pending or quote_fee_pending:
            await clmm_repo.update_position_fees(
                position_address=position.position_address,
                base_fee_pending=base_fee_pending,
                quote_fee_pending=quote_fee_pending
            )

        logger.debug(f"Refreshed position {position.position_address}: in_range={in_range}, "
                    f"base={base_token_amount}, quote={quote_token_amount}")

    except Exception as e:
        logger.error(f"Error refreshing position {position.position_address}: {e}", exc_info=True)
        raise


@router.get("/clmm/pool-info", response_model=CLMMPoolInfoResponse, response_model_by_alias=False)
async def get_clmm_pool_info(
    connector: str,
    network: str,
    pool_address: str,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get detailed information about a CLMM pool by pool address.

    Args:
        connector: CLMM connector (e.g., 'meteora', 'raydium')
        network: Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')
        pool_address: Pool contract address

    Example:
        GET /gateway/clmm/pool-info?connector=meteora&network=solana-mainnet-beta&pool_address=2sf5NYcY4zUPXUSmG6f66mskb24t5F8S11pC1Nz5nQT3

    Returns:
        Pool information including liquidity, price, bins (for Meteora), etc.
        All field names are returned in snake_case format.

    Note:
        For Raydium connector, uses Raydium API directly instead of Gateway.
    """
    try:
        # Special handling for Raydium - use Raydium API directly (not Gateway)
        if connector.lower() == "raydium":
            logger.info(f"Using Raydium API directly for pool info: {pool_address}")

            # Fetch from Raydium API
            raydium_data = await fetch_raydium_pool_info(pool_address)
            if raydium_data is None:
                raise HTTPException(status_code=503, detail="Failed to get pool info from Raydium API")

            # Transform to Gateway-compatible format
            result = transform_raydium_to_clmm_response(raydium_data, pool_address)

            # Parse into response model
            return CLMMPoolInfoResponse(**result)

        # Default behavior for other connectors: use Gateway
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network_name = accounts_service.gateway_client.parse_network_id(network)

        # Get pool info from Gateway using the CLMM-specific endpoint
        result = await accounts_service.gateway_client.clmm_pool_info(
            connector=connector,
            network=network_name,
            pool_address=pool_address
        )

        if result is None:
            raise HTTPException(status_code=503, detail="Failed to get pool info from Gateway")

        # Parse the camelCase Gateway response into snake_case Pydantic model
        # The model's aliases will handle the conversion
        return CLMMPoolInfoResponse(**result)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting CLMM pool info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting CLMM pool info: {str(e)}")


@router.get("/clmm/pools", response_model=CLMMPoolListResponse)
async def get_clmm_pools(
    connector: str,
    page: int = Query(0, ge=0, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Results per page (max 100)"),
    search_term: Optional[str] = Query(None, description="Search term to filter pools"),
    sort_key: Optional[str] = Query("volume", description="Sort key (volume, tvl, etc.)"),
    order_by: Optional[str] = Query("desc", description="Sort order (asc, desc)"),
    include_unknown: bool = Query(True, description="Include pools with unverified tokens")
):
    """
    Get list of available CLMM pools for a connector.

    Currently supports: meteora

    Args:
        connector: CLMM connector (e.g., 'meteora')
        page: Page number (default: 0)
        limit: Results per page (default: 50, max: 100)
        search_term: Search term to filter pools (optional)
        sort_key: Sort by field (volume, tvl, feetvlratio, etc.)
        order_by: Sort order (asc, desc)
        include_unknown: Include pools with unverified tokens

    Example:
        GET /gateway/clmm/pools?connector=meteora&search_term=SOL&limit=20

    Returns:
        List of available pools with trading pairs, addresses, liquidity, volume, APR, etc.
    """
    try:
        # Only support Meteora for now
        if connector.lower() != "meteora":
            raise HTTPException(
                status_code=400,
                detail=f"Pool listing not supported for connector '{connector}'. Currently only 'meteora' is supported."
            )

        # Fetch pools from Meteora API
        logger.info(f"Fetching pools from Meteora API (page={page}, limit={limit}, search={search_term})")
        meteora_data = await fetch_meteora_pools(
            page=page,
            limit=limit,
            search_term=search_term,
            sort_key=sort_key,
            order_by=order_by,
            include_unknown=include_unknown
        )

        if meteora_data is None:
            raise HTTPException(status_code=503, detail="Failed to fetch pools from Meteora API")

        # Transform Meteora response to our format
        pools = []
        groups = meteora_data.get("groups", [])

        for group in groups:
            pairs = group.get("pairs", [])
            for pair in pairs:
                # Extract trading pair from name or construct from mints
                name = pair.get("name", "")
                trading_pair = name if name else f"{pair.get('mint_x', '')[:8]}-{pair.get('mint_y', '')[:8]}"

                pools.append(CLMMPoolListItem(
                    address=pair.get("address", ""),
                    name=name,
                    trading_pair=trading_pair,
                    mint_x=pair.get("mint_x", ""),
                    mint_y=pair.get("mint_y", ""),
                    bin_step=pair.get("bin_step", 0),
                    current_price=Decimal(str(pair.get("current_price", 0))),
                    liquidity=pair.get("liquidity", "0"),
                    reserve_x=pair.get("reserve_x", "0"),
                    reserve_y=pair.get("reserve_y", "0"),
                    apr=Decimal(str(pair.get("apr", 0))) if pair.get("apr") else None,
                    apy=Decimal(str(pair.get("apy", 0))) if pair.get("apy") else None,
                    volume_24h=Decimal(str(pair.get("trade_volume_24h", 0))) if pair.get("trade_volume_24h") else None,
                    fees_24h=Decimal(str(pair.get("fees_24h", 0))) if pair.get("fees_24h") else None,
                    is_verified=pair.get("is_verified", False)
                ))

        total = meteora_data.get("total", len(pools))

        return CLMMPoolListResponse(
            pools=pools,
            total=total,
            page=page,
            limit=limit
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting CLMM pools: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting CLMM pools: {str(e)}")


@router.post("/clmm/open", response_model=CLMMOpenPositionResponse)
async def open_clmm_position(
    request: CLMMOpenPositionRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Open a NEW CLMM position with initial liquidity.

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        pool_address: '2sf5NYcY4zUPXUSmG6f66mskb24t5F8S11pC1Nz5nQT3'
        lower_price: 150
        upper_price: 250
        base_token_amount: 0.01
        quote_token_amount: 2
        slippage_pct: 1
        wallet_address: (optional)
        extra_params: {"strategyType": 0}  # Meteora-specific

    Returns:
        Transaction hash and position address
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

        # Get wallet address
        wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
            chain=chain,
            wallet_address=request.wallet_address
        )

        # Get pool info to extract trading pair for database
        pool_info = await accounts_service.gateway_client.clmm_pool_info(
            connector=request.connector,
            network=network,
            pool_address=request.pool_address
        )

        # Extract tokens from pool info
        base_token_address = pool_info.get("baseTokenAddress", "")
        quote_token_address = pool_info.get("quoteTokenAddress", "")

        # Store full token addresses in the database
        base = base_token_address if base_token_address else "UNKNOWN"
        quote = quote_token_address if quote_token_address else "UNKNOWN"
        trading_pair = f"{base}-{quote}"

        # Open position
        result = await accounts_service.gateway_client.clmm_open_position(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            pool_address=request.pool_address,
            lower_price=float(request.lower_price),
            upper_price=float(request.upper_price),
            base_token_amount=float(request.base_token_amount) if request.base_token_amount else None,
            quote_token_amount=float(request.quote_token_amount) if request.quote_token_amount else None,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct else 1.0,
            extra_params=request.extra_params
        )

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")

        # Position address can be at root level or nested in data object
        data = result.get("data", {})
        position_address = result.get("positionAddress") or result.get("position") or data.get("positionAddress") or data.get("position")

        # Extract position rent (SOL locked for position NFT)
        position_rent = data.get("positionRent")
        if position_rent:
            logger.info(f"Position rent: {position_rent} SOL")

        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")
        if not position_address:
            raise HTTPException(status_code=500, detail="No position address returned from Gateway")

        # Calculate percentage: (upper_price - lower_price) / lower_price
        percentage = None
        if request.lower_price and request.upper_price and request.lower_price > 0:
            percentage = float((request.upper_price - request.lower_price) / request.lower_price)
            logger.info(f"Position price range percentage: {percentage:.4f} ({percentage*100:.2f}%)")

        # Get transaction status from Gateway response
        tx_status = get_transaction_status_from_response(result)

        # Extract gas fee from Gateway response
        gas_fee = data.get("fee")
        gas_token = get_native_gas_token(chain)

        # Store position and event in database
        try:
            async with db_manager.get_session_context() as session:
                clmm_repo = GatewayCLMMRepository(session)

                # Create position record
                position_data = {
                    "position_address": position_address,
                    "pool_address": request.pool_address,
                    "network": request.network,
                    "connector": request.connector,
                    "wallet_address": wallet_address,
                    "trading_pair": trading_pair,
                    "base_token": base,
                    "quote_token": quote,
                    "status": "OPEN",
                    "lower_price": float(request.lower_price),
                    "upper_price": float(request.upper_price),
                    "percentage": percentage,
                    "initial_base_token_amount": float(request.base_token_amount) if request.base_token_amount else 0,
                    "initial_quote_token_amount": float(request.quote_token_amount) if request.quote_token_amount else 0,
                    "position_rent": float(position_rent) if position_rent else None,
                    "base_token_amount": float(request.base_token_amount) if request.base_token_amount else 0,
                    "quote_token_amount": float(request.quote_token_amount) if request.quote_token_amount else 0,
                    "in_range": "UNKNOWN"  # Will be updated by poller
                }

                position = await clmm_repo.create_position(position_data)
                logger.info(f"Recorded CLMM position in database: {position_address}")

                # Create OPEN event with polled status
                event_data = {
                    "position_id": position.id,
                    "transaction_hash": transaction_hash,
                    "event_type": "OPEN",
                    "base_token_amount": float(request.base_token_amount) if request.base_token_amount else None,
                    "quote_token_amount": float(request.quote_token_amount) if request.quote_token_amount else None,
                    "gas_fee": float(gas_fee) if gas_fee else None,
                    "gas_token": gas_token,
                    "status": tx_status
                }

                await clmm_repo.create_event(event_data)
                logger.info(f"Recorded CLMM OPEN event in database: {transaction_hash} (status: {tx_status}, gas: {gas_fee} {gas_token})")
        except Exception as db_error:
            # Log but don't fail the operation - it was submitted successfully
            logger.error(f"Error recording CLMM position in database: {db_error}", exc_info=True)

        return CLMMOpenPositionResponse(
            transaction_hash=transaction_hash,
            position_address=position_address,
            trading_pair=trading_pair,
            pool_address=request.pool_address,
            lower_price=request.lower_price,
            upper_price=request.upper_price,
            status="submitted"
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error opening CLMM position: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error opening CLMM position: {str(e)}")


# @router.post("/clmm/add")
# async def add_liquidity_to_clmm_position(
#     request: CLMMAddLiquidityRequest,
#     accounts_service: AccountsService = Depends(get_accounts_service),
#     db_manager: AsyncDatabaseManager = Depends(get_database_manager)
# ):
#     """
#     Add MORE liquidity to an EXISTING CLMM position.
#
#     Example:
#         connector: 'meteora'
#         network: 'solana-mainnet-beta'
#         position_address: '...'
#         base_token_amount: 0.5
#         quote_token_amount: 50.0
#         slippage_pct: 1
#         wallet_address: (optional)
#
#     Returns:
#         Transaction hash
#     """
#     try:
#         if not await accounts_service.gateway_client.ping():
#             raise HTTPException(status_code=503, detail="Gateway service is not available")
#
#         # Parse network_id
#         chain, network = accounts_service.gateway_client.parse_network_id(request.network)
#
#         # Get wallet address
#         wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
#             chain=chain,
#             wallet_address=request.wallet_address
#         )
#
#         # Add liquidity to existing position
#         result = await accounts_service.gateway_client.clmm_add_liquidity(
#             connector=request.connector,
#             network=network,
#             wallet_address=wallet_address,
#             position_address=request.position_address,
#             base_token_amount=float(request.base_token_amount) if request.base_token_amount else None,
#             quote_token_amount=float(request.quote_token_amount) if request.quote_token_amount else None,
#             slippage_pct=float(request.slippage_pct) if request.slippage_pct else 1.0
#         )
#
#         transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
#         if not transaction_hash:
#             raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")
#
#         # Get transaction status from Gateway response
#         tx_status = get_transaction_status_from_response(result)
#
#         # Extract gas fee from Gateway response
#         data = result.get("data", {})
#         gas_fee = data.get("fee")
#         gas_token = "SOL" if chain == "solana" else "ETH" if chain == "ethereum" else None
#
#         # Store ADD_LIQUIDITY event in database
#         try:
#             async with db_manager.get_session_context() as session:
#                 clmm_repo = GatewayCLMMRepository(session)
#
#                 # Get position to link event
#                 position = await clmm_repo.get_position_by_address(request.position_address)
#                 if position:
#                     event_data = {
#                         "position_id": position.id,
#                         "transaction_hash": transaction_hash,
#                         "event_type": "ADD_LIQUIDITY",
#                         "base_token_amount": float(request.base_token_amount) if request.base_token_amount else None,
#                         "quote_token_amount": float(request.quote_token_amount) if request.quote_token_amount else None,
#                         "gas_fee": float(gas_fee) if gas_fee else None,
#                         "gas_token": gas_token,
#                         "status": tx_status
#                     }
#                     await clmm_repo.create_event(event_data)
#                     logger.info(f"Recorded CLMM ADD_LIQUIDITY event: {transaction_hash} (status: {tx_status}, gas: {gas_fee} {gas_token})")
#         except Exception as db_error:
#             logger.error(f"Error recording ADD_LIQUIDITY event: {db_error}", exc_info=True)
#
#         return {
#             "transaction_hash": transaction_hash,
#             "position_address": request.position_address,
#             "status": "submitted"
#         }
#
#     except HTTPException:
#         raise
#     except ValueError as e:
#         raise HTTPException(status_code=400, detail=str(e))
#     except Exception as e:
#         logger.error(f"Error adding liquidity to CLMM position: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=f"Error adding liquidity to CLMM position: {str(e)}")
#
#
# @router.post("/clmm/remove")
# async def remove_liquidity_from_clmm_position(
#     request: CLMMRemoveLiquidityRequest,
#     accounts_service: AccountsService = Depends(get_accounts_service),
#     db_manager: AsyncDatabaseManager = Depends(get_database_manager)
# ):
#     """
#     Remove SOME liquidity from a CLMM position (partial removal).
#
#     Example:
#         connector: 'meteora'
#         network: 'solana-mainnet-beta'
#         position_address: '...'
#         percentage: 50
#         wallet_address: (optional)
#
#     Returns:
#         Transaction hash
#     """
#     try:
#         if not await accounts_service.gateway_client.ping():
#             raise HTTPException(status_code=503, detail="Gateway service is not available")
#
#         # Parse network_id
#         chain, network = accounts_service.gateway_client.parse_network_id(request.network)
#
#         # Get wallet address
#         wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
#             chain=chain,
#             wallet_address=request.wallet_address
#         )
#
#         # Remove liquidity
#         result = await accounts_service.gateway_client.clmm_remove_liquidity(
#             connector=request.connector,
#             network=network,
#             wallet_address=wallet_address,
#             position_address=request.position_address,
#             percentage=float(request.percentage)
#         )
#
#         transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
#         if not transaction_hash:
#             raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")
#
#         # Get transaction status from Gateway response
#         tx_status = get_transaction_status_from_response(result)
#
#         # Extract gas fee from Gateway response
#         data = result.get("data", {})
#         gas_fee = data.get("fee")
#         gas_token = "SOL" if chain == "solana" else "ETH" if chain == "ethereum" else None
#
#         # Store REMOVE_LIQUIDITY event in database
#         try:
#             async with db_manager.get_session_context() as session:
#                 clmm_repo = GatewayCLMMRepository(session)
#
#                 # Get position to link event
#                 position = await clmm_repo.get_position_by_address(request.position_address)
#                 if position:
#                     event_data = {
#                         "position_id": position.id,
#                         "transaction_hash": transaction_hash,
#                         "event_type": "REMOVE_LIQUIDITY",
#                         "percentage": float(request.percentage),
#                         "gas_fee": float(gas_fee) if gas_fee else None,
#                         "gas_token": gas_token,
#                         "status": tx_status
#                     }
#                     await clmm_repo.create_event(event_data)
#                     logger.info(f"Recorded CLMM REMOVE_LIQUIDITY event: {transaction_hash} (status: {tx_status}, gas: {gas_fee} {gas_token})")
#         except Exception as db_error:
#             logger.error(f"Error recording REMOVE_LIQUIDITY event: {db_error}", exc_info=True)
#
#         return {
#             "transaction_hash": transaction_hash,
#             "position_address": request.position_address,
#             "percentage": float(request.percentage),
#             "status": "submitted"
#         }
#
#     except HTTPException:
#         raise
#     except ValueError as e:
#         raise HTTPException(status_code=400, detail=str(e))
#     except Exception as e:
#         logger.error(f"Error removing liquidity from CLMM position: {e}", exc_info=True)
#         raise HTTPException(status_code=500, detail=f"Error removing liquidity from CLMM position: {str(e)}")
#

@router.post("/clmm/close")
async def close_clmm_position(
    request: CLMMClosePositionRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    CLOSE a CLMM position completely (removes all liquidity).

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        position_address: '...'
        wallet_address: (optional)

    Returns:
        Transaction hash
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

        # Get wallet address
        wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
            chain=chain,
            wallet_address=request.wallet_address
        )

        # Close position
        result = await accounts_service.gateway_client.clmm_close_position(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            position_address=request.position_address
        )

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")

        # Get transaction status from Gateway response
        tx_status = get_transaction_status_from_response(result)

        # Extract gas fee from Gateway response
        data = result.get("data", {})
        gas_fee = data.get("fee")
        gas_token = "SOL" if chain == "solana" else "ETH" if chain == "ethereum" else None

        # Store CLOSE event in database
        try:
            async with db_manager.get_session_context() as session:
                clmm_repo = GatewayCLMMRepository(session)

                # Get position to link event
                position = await clmm_repo.get_position_by_address(request.position_address)
                if position:
                    event_data = {
                        "position_id": position.id,
                        "transaction_hash": transaction_hash,
                        "event_type": "CLOSE",
                        "gas_fee": float(gas_fee) if gas_fee else None,
                        "gas_token": gas_token,
                        "status": tx_status
                    }
                    await clmm_repo.create_event(event_data)
                    logger.info(f"Recorded CLMM CLOSE event: {transaction_hash} (status: {tx_status}, gas: {gas_fee} {gas_token})")
        except Exception as db_error:
            logger.error(f"Error recording CLOSE event: {db_error}", exc_info=True)

        return {
            "transaction_hash": transaction_hash,
            "position_address": request.position_address,
            "status": "submitted"
        }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error closing CLMM position: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error closing CLMM position: {str(e)}")


@router.post("/clmm/collect-fees", response_model=CLMMCollectFeesResponse)
async def collect_fees_from_clmm_position(
    request: CLMMCollectFeesRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Collect accumulated fees from a CLMM liquidity position.

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        position_address: '...'
        wallet_address: (optional)

    Returns:
        Transaction hash and collected fee amounts
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

        # Get pool_address and wallet_address from database
        pool_address = None
        wallet_address = None

        async with db_manager.get_session_context() as session:
            clmm_repo = GatewayCLMMRepository(session)
            db_position = await clmm_repo.get_position_by_address(request.position_address)
            if db_position:
                pool_address = db_position.pool_address
                wallet_address = db_position.wallet_address

        # If not in database, use default wallet
        if not wallet_address:
            wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
                chain=chain,
                wallet_address=request.wallet_address
            )

        # If no pool_address from database, we can't query Gateway
        if not pool_address:
            raise HTTPException(
                status_code=404,
                detail=f"Position {request.position_address} not found in database. Pool address is required."
            )

        # Fetch pending fees BEFORE collecting (Gateway doesn't always return collected amounts in response)
        base_fee_to_collect = Decimal("0")
        quote_fee_to_collect = Decimal("0")

        try:
            positions_list = await accounts_service.gateway_client.clmm_positions_owned(
                connector=request.connector,
                network=network,
                wallet_address=wallet_address,
                pool_address=pool_address
            )

            # Find our specific position and get pending fees
            if positions_list and isinstance(positions_list, list):
                for pos in positions_list:
                    if pos and pos.get("address") == request.position_address:
                        base_fee_to_collect = Decimal(str(pos.get("baseFeeAmount", 0)))
                        quote_fee_to_collect = Decimal(str(pos.get("quoteFeeAmount", 0)))
                        logger.info(f"Pending fees before collection: base={base_fee_to_collect}, quote={quote_fee_to_collect}")
                        break
            else:
                logger.warning(f"Could not find position {request.position_address} in positions_owned response")
        except Exception as e:
            logger.warning(f"Could not fetch pending fees before collection: {e}", exc_info=True)

        # Collect fees
        result = await accounts_service.gateway_client.clmm_collect_fees(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            position_address=request.position_address
        )

        if not result:
            raise HTTPException(status_code=500, detail="No response from Gateway collect-fees endpoint")

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")

        # Get transaction status from Gateway response
        tx_status = get_transaction_status_from_response(result)

        # Try to extract collected amounts from Gateway response, fallback to pre-fetched amounts
        data = result.get("data", {})
        base_fee_from_response = data.get("baseFeeAmountCollected")
        quote_fee_from_response = data.get("quoteFeeAmountCollected")

        # Use response values if available, otherwise use pre-fetched values
        base_fee_collected = Decimal(str(base_fee_from_response)) if base_fee_from_response is not None else base_fee_to_collect
        quote_fee_collected = Decimal(str(quote_fee_from_response)) if quote_fee_from_response is not None else quote_fee_to_collect

        # Extract gas fee from Gateway response
        gas_fee = data.get("fee")
        gas_token = get_native_gas_token(chain)

        logger.info(f"Collected fees: base={base_fee_collected}, quote={quote_fee_collected}")

        # Store COLLECT_FEES event in database and update position
        try:
            async with db_manager.get_session_context() as session:
                clmm_repo = GatewayCLMMRepository(session)

                # Get position to link event
                position = await clmm_repo.get_position_by_address(request.position_address)
                if position:
                    # Create event record
                    event_data = {
                        "position_id": position.id,
                        "transaction_hash": transaction_hash,
                        "event_type": "COLLECT_FEES",
                        "base_fee_collected": float(base_fee_collected) if base_fee_collected else None,
                        "quote_fee_collected": float(quote_fee_collected) if quote_fee_collected else None,
                        "gas_fee": float(gas_fee) if gas_fee else None,
                        "gas_token": gas_token,
                        "status": tx_status
                    }
                    await clmm_repo.create_event(event_data)
                    logger.info(f"Recorded CLMM COLLECT_FEES event: {transaction_hash} (status: {tx_status}, gas: {gas_fee} {gas_token})")

                    # Update position: add to collected, reset pending to 0
                    new_base_collected = Decimal(str(position.base_fee_collected)) + base_fee_collected
                    new_quote_collected = Decimal(str(position.quote_fee_collected)) + quote_fee_collected

                    await clmm_repo.update_position_fees(
                        position_address=request.position_address,
                        base_fee_collected=new_base_collected,
                        quote_fee_collected=new_quote_collected,
                        base_fee_pending=Decimal("0"),
                        quote_fee_pending=Decimal("0")
                    )
                    logger.info(f"Updated position {request.position_address}: collected fees updated, pending fees reset to 0")
        except Exception as db_error:
            logger.error(f"Error recording COLLECT_FEES event: {db_error}", exc_info=True)

        return CLMMCollectFeesResponse(
            transaction_hash=transaction_hash,
            position_address=request.position_address,
            base_fee_collected=Decimal(str(base_fee_collected)) if base_fee_collected else None,
            quote_fee_collected=Decimal(str(quote_fee_collected)) if quote_fee_collected else None,
            status="submitted"
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error collecting fees: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error collecting fees: {str(e)}")


@router.post("/clmm/positions_owned", response_model=List[CLMMPositionInfo])
async def get_clmm_positions_owned(
    request: CLMMPositionsOwnedRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get all CLMM liquidity positions owned by a wallet for a specific pool.

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        pool_address: '2sf5NYcY4zUPXUSmG6f66mskb24t5F8S11pC1Nz5nQT3'
        wallet_address: (optional, uses default if not provided)

    Returns:
        List of CLMM position information for the specified pool
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

        # Get wallet address
        wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
            chain=chain,
            wallet_address=request.wallet_address
        )

        # Get positions for the specified pool
        result = await accounts_service.gateway_client.clmm_positions_owned(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            pool_address=request.pool_address
        )

        if result is None:
            raise HTTPException(status_code=500, detail="Failed to get positions from Gateway")

        # Gateway returns a list directly
        positions_data = result if isinstance(result, list) else []
        positions = []

        for pos in positions_data:
            # Extract token addresses (Gateway returns addresses, not symbols)
            base_token_address = pos.get("baseTokenAddress", "")
            quote_token_address = pos.get("quoteTokenAddress", "")

            # Use short addresses as symbols for now
            base_token = base_token_address[-8:] if base_token_address else ""
            quote_token = quote_token_address[-8:] if quote_token_address else ""
            trading_pair = f"{base_token}-{quote_token}" if base_token and quote_token else ""

            current_price = Decimal(str(pos.get("price", 0)))
            lower_price = Decimal(str(pos.get("lowerPrice", 0))) if pos.get("lowerPrice") else Decimal("0")
            upper_price = Decimal(str(pos.get("upperPrice", 0))) if pos.get("upperPrice") else Decimal("0")

            # Determine if position is in range
            in_range = False
            if current_price > 0 and lower_price > 0 and upper_price > 0:
                in_range = lower_price <= current_price <= upper_price

            positions.append(CLMMPositionInfo(
                position_address=pos.get("address", ""),
                pool_address=pos.get("poolAddress", ""),
                trading_pair=trading_pair,
                base_token=base_token,
                quote_token=quote_token,
                base_token_amount=Decimal(str(pos.get("baseTokenAmount", 0))),
                quote_token_amount=Decimal(str(pos.get("quoteTokenAmount", 0))),
                current_price=current_price,
                lower_price=lower_price,
                upper_price=upper_price,
                base_fee_amount=Decimal(str(pos.get("baseFeeAmount", 0))) if pos.get("baseFeeAmount") else None,
                quote_fee_amount=Decimal(str(pos.get("quoteFeeAmount", 0))) if pos.get("quoteFeeAmount") else None,
                lower_bin_id=pos.get("lowerBinId"),
                upper_bin_id=pos.get("upperBinId"),
                in_range=in_range
            ))

        return positions

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting CLMM positions owned: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting CLMM positions owned: {str(e)}")


@router.get("/clmm/positions/{position_address}/events")
async def get_clmm_position_events(
    position_address: str,
    event_type: Optional[str] = None,
    limit: int = 100,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Get event history for a CLMM position.

    Args:
        position_address: Position NFT address
        event_type: Filter by event type (OPEN, ADD_LIQUIDITY, REMOVE_LIQUIDITY, COLLECT_FEES, CLOSE)
        limit: Max events to return

    Returns:
        List of position events
    """
    try:
        async with db_manager.get_session_context() as session:
            clmm_repo = GatewayCLMMRepository(session)
            events = await clmm_repo.get_position_events(
                position_address=position_address,
                event_type=event_type,
                limit=limit
            )

            return {
                "data": [clmm_repo.event_to_dict(event) for event in events],
                "total_count": len(events)
            }

    except Exception as e:
        logger.error(f"Error getting position events: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting position events: {str(e)}")


@router.post("/clmm/positions/search")
async def search_clmm_positions(
    network: Optional[str] = None,
    connector: Optional[str] = None,
    wallet_address: Optional[str] = None,
    trading_pair: Optional[str] = None,
    status: Optional[str] = None,
    position_addresses: Optional[List[str]] = Query(None),
    limit: int = 50,
    offset: int = 0,
    refresh: bool = False,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager),
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Search CLMM positions with filters.

    Args:
        network: Filter by network (e.g., 'solana-mainnet-beta')
        connector: Filter by connector (e.g., 'meteora')
        wallet_address: Filter by wallet address
        trading_pair: Filter by trading pair (e.g., 'SOL-USDC')
        status: Filter by status (OPEN, CLOSED)
        position_addresses: Filter by specific position addresses (list of addresses)
        limit: Max results (default 50, max 1000)
        offset: Pagination offset
        refresh: If True, refresh position data from Gateway before returning (default False)

    Returns:
        Paginated list of positions
    """
    try:
        # Validate limit
        if limit > 1000:
            limit = 1000

        # Optionally refresh position data from Gateway first
        if refresh and await accounts_service.gateway_client.ping():
            # Get positions to refresh
            async with db_manager.get_session_context() as session:
                clmm_repo = GatewayCLMMRepository(session)
                positions_to_refresh = await clmm_repo.get_positions(
                    network=network,
                    connector=connector,
                    wallet_address=wallet_address,
                    trading_pair=trading_pair,
                    status=status,
                    position_addresses=position_addresses,
                    limit=limit,
                    offset=offset
                )

                # Extract position addresses and details before closing session
                position_details = [
                    {
                        "position_address": pos.position_address,
                        "pool_address": pos.pool_address,
                        "connector": pos.connector,
                        "network": pos.network,
                        "wallet_address": pos.wallet_address
                    }
                    for pos in positions_to_refresh
                ]

            # Refresh each position in a separate session
            logger.info(f"Refreshing {len(position_details)} positions from Gateway")
            for pos_detail in position_details:
                try:
                    async with db_manager.get_session_context() as session:
                        clmm_repo = GatewayCLMMRepository(session)
                        # Get position again in this session
                        position = await clmm_repo.get_position_by_address(pos_detail["position_address"])
                        if position:
                            await _refresh_position_data(position, accounts_service, clmm_repo)
                except Exception as e:
                    logger.warning(f"Failed to refresh position {pos_detail['position_address']}: {e}")
                    # Continue with other positions even if one fails

        # Get final results after refresh
        async with db_manager.get_session_context() as session:
            clmm_repo = GatewayCLMMRepository(session)
            positions = await clmm_repo.get_positions(
                network=network,
                connector=connector,
                wallet_address=wallet_address,
                trading_pair=trading_pair,
                status=status,
                position_addresses=position_addresses,
                limit=limit,
                offset=offset
            )

            # Get total count for pagination
            has_more = len(positions) == limit

            return {
                "data": [clmm_repo.position_to_dict(pos) for pos in positions],
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "has_more": has_more,
                    "total_count": len(positions) + offset if not has_more else None
                }
            }

    except Exception as e:
        logger.error(f"Error searching CLMM positions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error searching CLMM positions: {str(e)}")


