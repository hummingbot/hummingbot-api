"""
Gateway Trading Router - Handles DEX trading operations via Hummingbot Gateway.
Supports Router swaps (Jupiter, 0x) and CLMM liquidity (Meteora, Raydium, Uniswap V3).

Note: AMM support removed. Use Router connectors for simple swaps, CLMM for liquidity provision.
"""
import logging
from typing import Dict, List, Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from deps import get_accounts_service, get_database_manager
from services.accounts_service import AccountsService
from database import AsyncDatabaseManager
from database.repositories import GatewaySwapRepository, GatewayCLMMRepository
from models import (
    SwapQuoteRequest,
    SwapQuoteResponse,
    SwapExecuteRequest,
    SwapExecuteResponse,
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
    GetPoolInfoRequest,
    PoolInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway Trading"], prefix="/gateway")


# Helper function to parse network_id into chain and network
def parse_network_id(network_id: str) -> tuple[str, str]:
    """
    Parse network_id in format 'chain-network' into (chain, network).

    Examples:
        'solana-mainnet-beta' -> ('solana', 'mainnet-beta')
        'ethereum-mainnet' -> ('ethereum', 'mainnet')
    """
    parts = network_id.split('-', 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid network_id format. Expected 'chain-network', got '{network_id}'")
    return parts[0], parts[1]


# Helper to get wallet address (use provided or default)
async def get_wallet_address(
    network_id: str,
    wallet_address: str | None,
    accounts_service: AccountsService
) -> str:
    """Get wallet address - use provided or get default for chain"""
    if wallet_address:
        return wallet_address

    chain, _ = parse_network_id(network_id)
    default_wallet = await accounts_service.gateway_client.get_default_wallet_address(chain)
    if not default_wallet:
        raise HTTPException(status_code=400, detail=f"No wallet configured for chain '{chain}'")
    return default_wallet


# ============================================
# Swap Operations (Router: Jupiter, 0x)
# ============================================

@router.post("/swap/quote", response_model=SwapQuoteResponse)
async def get_swap_quote(
    request: SwapQuoteRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get a price quote for a swap via router (Jupiter, 0x).

    Example:
        connector: 'jupiter'
        network: 'solana-mainnet-beta'
        trading_pair: 'SOL-USDC'
        side: 'BUY'
        amount: 1
        slippage_pct: 1

    Returns:
        Quote with price, expected output amount, and gas estimate
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = parse_network_id(request.network)

        # Parse trading pair
        base, quote = request.trading_pair.split("-")

        # Get quote from Gateway
        result = await accounts_service.gateway_client.quote_swap(
            connector=request.connector,
            network=network,
            base_asset=base,
            quote_asset=quote,
            amount=float(request.amount),
            side=request.side,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct else 1.0,
            pool_address=None
        )

        # Extract amounts from Gateway response (snake_case for consistency)
        amount_in_raw = result.get("amountIn") or result.get("amount_in")
        amount_out_raw = result.get("amountOut") or result.get("amount_out")

        amount_in = Decimal(str(amount_in_raw)) if amount_in_raw else None
        amount_out = Decimal(str(amount_out_raw)) if amount_out_raw else None

        # Extract gas estimate (try both camelCase and snake_case)
        gas_estimate = result.get("gasEstimate") or result.get("gas_estimate")
        gas_estimate_value = Decimal(str(gas_estimate)) if gas_estimate else None

        return SwapQuoteResponse(
            base=base,
            quote=quote,
            price=Decimal(str(result.get("price", 0))),
            amount=request.amount,
            amount_in=amount_in,
            amount_out=amount_out,
            expected_amount=amount_out,  # Deprecated, kept for backward compatibility
            slippage_pct=request.slippage_pct or Decimal("1.0"),
            gas_estimate=gas_estimate_value
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting swap quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting swap quote: {str(e)}")


@router.post("/swap/execute", response_model=SwapExecuteResponse)
async def execute_swap(
    request: SwapExecuteRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Execute a swap transaction via router (Jupiter, 0x).

    Example:
        connector: 'jupiter'
        network: 'solana-mainnet-beta'
        trading_pair: 'SOL-USDC'
        side: 'BUY'
        amount: 1
        slippage_pct: 1
        wallet_address: (optional, uses default if not provided)

    Returns:
        Transaction hash and swap details
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = parse_network_id(request.network)

        # Get wallet address
        wallet_address = await get_wallet_address(request.network, request.wallet_address, accounts_service)

        # Parse trading pair
        base, quote = request.trading_pair.split("-")

        # Execute swap
        result = await accounts_service.gateway_client.execute_swap(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            base_asset=base,
            quote_asset=quote,
            amount=float(request.amount),
            side=request.side,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct else 1.0
        )

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")

        # Extract swap data from Gateway response
        # Gateway returns amounts nested under 'data' object
        data = result.get("data", {})
        amount_in_raw = data.get("amountIn")
        amount_out_raw = data.get("amountOut")

        # Use amounts from Gateway response, fallback to request amount if not available
        input_amount = Decimal(str(amount_in_raw)) if amount_in_raw is not None else request.amount
        output_amount = Decimal(str(amount_out_raw)) if amount_out_raw is not None else Decimal("0")

        # Calculate price from actual swap amounts
        # Price = output / input (how much quote you get/pay per base)
        price = output_amount / input_amount if input_amount > 0 else Decimal("0")

        # Store swap in database
        try:
            async with db_manager.get_session_context() as session:
                swap_repo = GatewaySwapRepository(session)

                swap_data = {
                    "transaction_hash": transaction_hash,
                    "network": request.network,
                    "connector": request.connector,
                    "wallet_address": wallet_address,
                    "trading_pair": request.trading_pair,
                    "base_token": base,
                    "quote_token": quote,
                    "side": request.side,
                    "input_amount": float(input_amount),
                    "output_amount": float(output_amount),
                    "price": float(price),
                    "slippage_pct": float(request.slippage_pct) if request.slippage_pct else 1.0,
                    "status": "SUBMITTED",
                    "pool_address": result.get("poolAddress") or result.get("pool_address")
                }

                await swap_repo.create_swap(swap_data)
                logger.info(f"Recorded swap in database: {transaction_hash}")
        except Exception as db_error:
            # Log but don't fail the swap - it was submitted successfully
            logger.error(f"Error recording swap in database: {db_error}", exc_info=True)

        return SwapExecuteResponse(
            transaction_hash=transaction_hash,
            trading_pair=request.trading_pair,
            side=request.side,
            amount=request.amount,
            status="submitted"
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error executing swap: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error executing swap: {str(e)}")

@router.get("/swaps/{transaction_hash}/status")
async def get_swap_status(
    transaction_hash: str,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Get status of a specific swap by transaction hash.

    Args:
        transaction_hash: Transaction hash of the swap

    Returns:
        Swap details including current status
    """
    try:
        async with db_manager.get_session_context() as session:
            swap_repo = GatewaySwapRepository(session)
            swap = await swap_repo.get_swap_by_tx_hash(transaction_hash)

            if not swap:
                raise HTTPException(status_code=404, detail=f"Swap not found: {transaction_hash}")

            return swap_repo.to_dict(swap)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting swap status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting swap status: {str(e)}")


@router.post("/swaps/search")
async def search_swaps(
    network: Optional[str] = None,
    connector: Optional[str] = None,
    wallet_address: Optional[str] = None,
    trading_pair: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Search swap history with filters.

    Args:
        network: Filter by network (e.g., 'solana-mainnet-beta')
        connector: Filter by connector (e.g., 'jupiter')
        wallet_address: Filter by wallet address
        trading_pair: Filter by trading pair (e.g., 'SOL-USDC')
        status: Filter by status (SUBMITTED, CONFIRMED, FAILED)
        start_time: Start timestamp (unix seconds)
        end_time: End timestamp (unix seconds)
        limit: Max results (default 50, max 1000)
        offset: Pagination offset

    Returns:
        Paginated list of swaps
    """
    try:
        # Validate limit
        if limit > 1000:
            limit = 1000

        async with db_manager.get_session_context() as session:
            swap_repo = GatewaySwapRepository(session)
            swaps = await swap_repo.get_swaps(
                network=network,
                connector=connector,
                wallet_address=wallet_address,
                trading_pair=trading_pair,
                status=status,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                offset=offset
            )

            # Get total count for pagination (simplified - actual count would need separate query)
            has_more = len(swaps) == limit

            return {
                "data": [swap_repo.to_dict(swap) for swap in swaps],
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "has_more": has_more,
                    "total_count": len(swaps) + offset if not has_more else None
                }
            }

    except Exception as e:
        logger.error(f"Error searching swaps: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error searching swaps: {str(e)}")


@router.get("/swaps/summary")
async def get_swaps_summary(
    network: Optional[str] = None,
    wallet_address: Optional[str] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Get swap summary statistics.

    Args:
        network: Filter by network
        wallet_address: Filter by wallet address
        start_time: Start timestamp (unix seconds)
        end_time: End timestamp (unix seconds)

    Returns:
        Summary statistics including volume, fees, success rate
    """
    try:
        async with db_manager.get_session_context() as session:
            swap_repo = GatewaySwapRepository(session)
            summary = await swap_repo.get_swaps_summary(
                network=network,
                wallet_address=wallet_address,
                start_time=start_time,
                end_time=end_time
            )
            return summary

    except Exception as e:
        logger.error(f"Error getting swaps summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting swaps summary: {str(e)}")


# ============================================
# Pool Information
# ============================================

@router.post("/pools/info", response_model=PoolInfo)
async def get_pool_info(
    request: GetPoolInfoRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get information about a liquidity pool.

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        trading_pair: 'SOL-USDC'

    Returns:
        Pool details including type, address, liquidity, price, and fees
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = parse_network_id(request.network)

        # Get pool address from trading pair
        pools = await accounts_service.gateway_client.get_pools(request.connector, network)

        # Parse trading pair
        base, quote = request.trading_pair.split("-")

        # Find matching pool
        pool_data = None
        for pool in pools:
            if (pool.get("baseSymbol") == base and pool.get("quoteSymbol") == quote) or \
               (pool.get("base") == base and pool.get("quote") == quote):
                pool_data = pool
                break

        if not pool_data:
            raise HTTPException(status_code=404, detail=f"Pool not found for {request.trading_pair}")

        pool_address = pool_data.get("address")
        if not pool_address:
            raise HTTPException(status_code=404, detail="Pool address not found")

        # Get detailed pool info
        result = await accounts_service.gateway_client.pool_info(
            connector=request.connector,
            network=network,
            pool_address=pool_address
        )

        # Determine pool type (CLMM has binStep, Router doesn't)
        pool_type = "clmm" if "binStep" in result or "bin_step" in result else "router"

        return PoolInfo(
            type=pool_type,
            address=pool_address,
            trading_pair=request.trading_pair,
            base_token=base,
            quote_token=quote,
            current_price=Decimal(str(result.get("price", 0))),
            base_token_amount=Decimal(str(result.get("baseTokenAmount", 0))),
            quote_token_amount=Decimal(str(result.get("quoteTokenAmount", 0))),
            fee_pct=Decimal(str(result.get("feePct", 0))),
            bin_step=result.get("binStep") or result.get("bin_step"),
            active_bin_id=result.get("activeBinId") or result.get("active_bin_id")
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting pool info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting pool info: {str(e)}")


# ============================================
# CLMM Liquidity Operations
# ============================================

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
        trading_pair: 'SOL-USDC'
        lower_price: 95.0
        upper_price: 105.0
        base_token_amount: 1.0
        quote_token_amount: 100.0
        slippage_pct: 1
        wallet_address: (optional)

    Returns:
        Transaction hash and position address
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = parse_network_id(request.network)

        # Get wallet address
        wallet_address = await get_wallet_address(request.network, request.wallet_address, accounts_service)

        # Get pool address
        pools = await accounts_service.gateway_client.get_pools(request.connector, network)
        base, quote = request.trading_pair.split("-")

        pool_address = None
        for pool in pools:
            if (pool.get("baseSymbol") == base and pool.get("quoteSymbol") == quote) or \
               (pool.get("base") == base and pool.get("quote") == quote):
                pool_address = pool.get("address")
                break

        if not pool_address:
            raise HTTPException(status_code=404, detail=f"Pool not found for {request.trading_pair}")

        # Calculate price range
        if request.lower_price is None or request.upper_price is None:
            if request.price is None or request.lower_width_pct is None or request.upper_width_pct is None:
                raise HTTPException(
                    status_code=400,
                    detail="Must provide either (lower_price + upper_price) or (price + lower_width_pct + upper_width_pct)"
                )
            lower_price = float(request.price) * (1 - float(request.lower_width_pct) / 100)
            upper_price = float(request.price) * (1 + float(request.upper_width_pct) / 100)
        else:
            lower_price = float(request.lower_price)
            upper_price = float(request.upper_price)

        # Open position
        result = await accounts_service.gateway_client.clmm_open_position(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            pool_address=pool_address,
            lower_price=lower_price,
            upper_price=upper_price,
            base_token_amount=float(request.base_token_amount) if request.base_token_amount else None,
            quote_token_amount=float(request.quote_token_amount) if request.quote_token_amount else None,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct else 1.0
        )

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
        position_address = result.get("positionAddress") or result.get("position")

        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")
        if not position_address:
            raise HTTPException(status_code=500, detail="No position address returned from Gateway")

        # Store position and event in database
        try:
            async with db_manager.get_session_context() as session:
                clmm_repo = GatewayCLMMRepository(session)

                # Create position record
                position_data = {
                    "position_address": position_address,
                    "pool_address": pool_address,
                    "network": request.network,
                    "connector": request.connector,
                    "wallet_address": wallet_address,
                    "trading_pair": request.trading_pair,
                    "base_token": base,
                    "quote_token": quote,
                    "status": "OPEN",
                    "lower_price": float(lower_price),
                    "upper_price": float(upper_price),
                    "base_token_amount": float(request.base_token_amount) if request.base_token_amount else 0,
                    "quote_token_amount": float(request.quote_token_amount) if request.quote_token_amount else 0,
                    "in_range": "UNKNOWN"  # Will be updated by poller
                }

                position = await clmm_repo.create_position(position_data)
                logger.info(f"Recorded CLMM position in database: {position_address}")

                # Create OPEN event
                event_data = {
                    "position_id": position.id,
                    "transaction_hash": transaction_hash,
                    "event_type": "OPEN",
                    "base_token_amount": float(request.base_token_amount) if request.base_token_amount else None,
                    "quote_token_amount": float(request.quote_token_amount) if request.quote_token_amount else None,
                    "status": "SUBMITTED"
                }

                await clmm_repo.create_event(event_data)
                logger.info(f"Recorded CLMM OPEN event in database: {transaction_hash}")
        except Exception as db_error:
            # Log but don't fail the operation - it was submitted successfully
            logger.error(f"Error recording CLMM position in database: {db_error}", exc_info=True)

        return CLMMOpenPositionResponse(
            transaction_hash=transaction_hash,
            position_address=position_address,
            trading_pair=request.trading_pair,
            pool_address=pool_address,
            lower_price=Decimal(str(lower_price)),
            upper_price=Decimal(str(upper_price)),
            status="submitted"
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error opening CLMM position: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error opening CLMM position: {str(e)}")


@router.post("/clmm/add")
async def add_liquidity_to_clmm_position(
    request: CLMMAddLiquidityRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Add MORE liquidity to an EXISTING CLMM position.

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        position_address: '...'
        base_token_amount: 0.5
        quote_token_amount: 50.0
        slippage_pct: 1
        wallet_address: (optional)

    Returns:
        Transaction hash
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = parse_network_id(request.network)

        # Get wallet address
        wallet_address = await get_wallet_address(request.network, request.wallet_address, accounts_service)

        # Add liquidity to existing position
        result = await accounts_service.gateway_client.clmm_add_liquidity(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            position_address=request.position_address,
            base_token_amount=float(request.base_token_amount) if request.base_token_amount else None,
            quote_token_amount=float(request.quote_token_amount) if request.quote_token_amount else None,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct else 1.0
        )

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")

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
        logger.error(f"Error adding liquidity to CLMM position: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error adding liquidity to CLMM position: {str(e)}")


@router.post("/clmm/remove")
async def remove_liquidity_from_clmm_position(
    request: CLMMRemoveLiquidityRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Remove SOME liquidity from a CLMM position (partial removal).

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        position_address: '...'
        percentage: 50
        wallet_address: (optional)

    Returns:
        Transaction hash
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = parse_network_id(request.network)

        # Get wallet address
        wallet_address = await get_wallet_address(request.network, request.wallet_address, accounts_service)

        # Remove liquidity
        result = await accounts_service.gateway_client.clmm_remove_liquidity(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            position_address=request.position_address,
            percentage=float(request.percentage)
        )

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")

        return {
            "transaction_hash": transaction_hash,
            "position_address": request.position_address,
            "percentage": float(request.percentage),
            "status": "submitted"
        }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error removing liquidity from CLMM position: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error removing liquidity from CLMM position: {str(e)}")


@router.post("/clmm/close")
async def close_clmm_position(
    request: CLMMClosePositionRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
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
        chain, network = parse_network_id(request.network)

        # Get wallet address
        wallet_address = await get_wallet_address(request.network, request.wallet_address, accounts_service)

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
    accounts_service: AccountsService = Depends(get_accounts_service)
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
        chain, network = parse_network_id(request.network)

        # Get wallet address
        wallet_address = await get_wallet_address(request.network, request.wallet_address, accounts_service)

        # Get position info to check fees before collecting
        position_info = await accounts_service.gateway_client.clmm_position_info(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            position_address=request.position_address
        )

        base_fee = position_info.get("baseFeeAmount", 0)
        quote_fee = position_info.get("quoteFeeAmount", 0)

        # Collect fees
        result = await accounts_service.gateway_client.clmm_collect_fees(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            position_address=request.position_address
        )

        transaction_hash = result.get("signature") or result.get("txHash") or result.get("hash")
        if not transaction_hash:
            raise HTTPException(status_code=500, detail="No transaction hash returned from Gateway")

        return CLMMCollectFeesResponse(
            transaction_hash=transaction_hash,
            position_address=request.position_address,
            base_fee_collected=Decimal(str(base_fee)) if base_fee else None,
            quote_fee_collected=Decimal(str(quote_fee)) if quote_fee else None,
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
    Get all CLMM liquidity positions owned by a wallet.

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        wallet_address: (optional, uses default if not provided)

    Returns:
        List of CLMM position information
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = parse_network_id(request.network)

        # Get wallet address
        wallet_address = await get_wallet_address(request.network, request.wallet_address, accounts_service)

        # Get positions
        result = await accounts_service.gateway_client.clmm_positions_owned(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            pool_address=None  # Get all positions
        )

        positions_data = result if isinstance(result, list) else result.get("positions", [])
        positions = []

        for pos in positions_data:
            base_token = pos.get("baseToken", "")
            quote_token = pos.get("quoteToken", "")
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


@router.get("/clmm/positions/{position_address}")
async def get_clmm_position(
    position_address: str,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Get details of a specific CLMM position by address.

    Args:
        position_address: Position NFT address

    Returns:
        Position details
    """
    try:
        async with db_manager.get_session_context() as session:
            clmm_repo = GatewayCLMMRepository(session)
            position = await clmm_repo.get_position_by_address(position_address)

            if not position:
                raise HTTPException(status_code=404, detail=f"Position not found: {position_address}")

            return clmm_repo.position_to_dict(position)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting CLMM position: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting CLMM position: {str(e)}")


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
    limit: int = 50,
    offset: int = 0,
    db_manager: AsyncDatabaseManager = Depends(get_database_manager)
):
    """
    Search CLMM positions with filters.

    Args:
        network: Filter by network (e.g., 'solana-mainnet-beta')
        connector: Filter by connector (e.g., 'meteora')
        wallet_address: Filter by wallet address
        trading_pair: Filter by trading pair (e.g., 'SOL-USDC')
        status: Filter by status (OPEN, CLOSED)
        limit: Max results (default 50, max 1000)
        offset: Pagination offset

    Returns:
        Paginated list of positions
    """
    try:
        # Validate limit
        if limit > 1000:
            limit = 1000

        async with db_manager.get_session_context() as session:
            clmm_repo = GatewayCLMMRepository(session)
            positions = await clmm_repo.get_positions(
                network=network,
                connector=connector,
                wallet_address=wallet_address,
                trading_pair=trading_pair,
                status=status,
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


@router.post("/clmm/position_info", response_model=CLMMPositionInfo)
async def get_clmm_position_info(
    request: CLMMGetPositionInfoRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get detailed information about a specific CLMM position.

    Example:
        connector: 'meteora'
        network: 'solana-mainnet-beta'
        position_address: '...'

    Returns:
        CLMM position information
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id
        chain, network = parse_network_id(request.network)

        # Get default wallet address for position info call
        wallet_address = await accounts_service.gateway_client.get_default_wallet_address(chain)
        if not wallet_address:
            raise HTTPException(status_code=400, detail=f"No wallet configured for chain '{chain}'")

        # Get position info
        result = await accounts_service.gateway_client.clmm_position_info(
            connector=request.connector,
            network=network,
            wallet_address=wallet_address,
            position_address=request.position_address
        )

        base_token = result.get("baseToken", "")
        quote_token = result.get("quoteToken", "")
        trading_pair = f"{base_token}-{quote_token}" if base_token and quote_token else ""

        current_price = Decimal(str(result.get("price", 0)))
        lower_price = Decimal(str(result.get("lowerPrice", 0))) if result.get("lowerPrice") else Decimal("0")
        upper_price = Decimal(str(result.get("upperPrice", 0))) if result.get("upperPrice") else Decimal("0")

        # Determine if position is in range
        in_range = False
        if current_price > 0 and lower_price > 0 and upper_price > 0:
            in_range = lower_price <= current_price <= upper_price

        return CLMMPositionInfo(
            position_address=request.position_address,
            pool_address=result.get("poolAddress", ""),
            trading_pair=trading_pair,
            base_token=base_token,
            quote_token=quote_token,
            base_token_amount=Decimal(str(result.get("baseTokenAmount", 0))),
            quote_token_amount=Decimal(str(result.get("quoteTokenAmount", 0))),
            current_price=current_price,
            lower_price=lower_price,
            upper_price=upper_price,
            base_fee_amount=Decimal(str(result.get("baseFeeAmount", 0))) if result.get("baseFeeAmount") else None,
            quote_fee_amount=Decimal(str(result.get("quoteFeeAmount", 0))) if result.get("quoteFeeAmount") else None,
            lower_bin_id=result.get("lowerBinId"),
            upper_bin_id=result.get("upperBinId"),
            in_range=in_range
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting CLMM position info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting CLMM position info: {str(e)}")
