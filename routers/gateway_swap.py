"""
Gateway Swap Router - Handles DEX swap operations via Hummingbot Gateway.
Uses Gateway's unified /trading/swap endpoints, so any swap provider works:
router connectors (jupiter, 0x, uniswap, pancakeswap) and amm/clmm connectors
(raydium/amm, meteora/clmm, ...).
"""
import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from database import AsyncDatabaseManager
from database.repositories import GatewaySwapRepository
from deps import get_accounts_service, get_database_manager
from models import SwapExecuteRequest, SwapExecuteResponse, SwapQuoteRequest, SwapQuoteResponse
from services.accounts_service import AccountsService
from services.gateway_client import GatewayError, check_gateway_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway Swaps"], prefix="/gateway")


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


@router.post("/swap/quote", response_model=SwapQuoteResponse)
async def get_swap_quote(
    request: SwapQuoteRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Get a price quote for a swap.

    Example:
        connector: 'jupiter' (or typed: 'jupiter/router', 'meteora/clmm', 'raydium/amm')
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

        # Parse trading pair
        base, quote = request.trading_pair.split("-")

        # Get quote from Gateway
        result = check_gateway_error(await accounts_service.gateway_client.quote_swap(
            connector=request.connector,
            chain_network=request.network,
            base_asset=base,
            quote_asset=quote,
            amount=float(request.amount),
            side=request.side,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else 1.0
        ))

        # Re-frame Gateway's token-flow response (tokenIn/tokenOut) into pair terms,
        # passing its execution-safety fields through in snake_case.
        def _dec(key):
            value = result.get(key)
            return Decimal(str(value)) if value is not None else None

        return SwapQuoteResponse(
            base=base,
            quote=quote,
            price=Decimal(str(result.get("price", 0))),
            amount=request.amount,
            amount_in=_dec("amountIn"),
            amount_out=_dec("amountOut"),
            min_amount_out=_dec("minAmountOut"),
            max_amount_in=_dec("maxAmountIn"),
            price_impact_pct=_dec("priceImpactPct"),
            pool_address=result.get("poolAddress"),
            route_path=result.get("routePath"),
            slippage_pct=(_dec("slippagePct")
                          or (request.slippage_pct if request.slippage_pct is not None else Decimal("1.0"))),
        )

    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error getting swap quote: {e}")
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

        # Parse network_id (chain needed for wallet lookup)
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

        # Get wallet address
        wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
            chain=chain,
            wallet_address=request.wallet_address
        )

        # Parse trading pair
        base, quote = request.trading_pair.split("-")

        # Execute swap
        result = check_gateway_error(await accounts_service.gateway_client.execute_swap(
            connector=request.connector,
            chain_network=request.network,
            wallet_address=wallet_address,
            base_asset=base,
            quote_asset=quote,
            amount=float(request.amount),
            side=request.side,
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else 1.0
        ))
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

        # Get transaction status from Gateway response
        tx_status = get_transaction_status_from_response(result)

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
                    "slippage_pct": float(request.slippage_pct) if request.slippage_pct is not None else 1.0,
                    "status": tx_status,
                    "pool_address": result.get("poolAddress") or result.get("pool_address")
                }

                await swap_repo.create_swap(swap_data)
                logger.info(f"Recorded swap in database: {transaction_hash} (status: {tx_status})")
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
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error executing swap: {e}")
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
