"""
Gateway Trading Router - Handles DEX trading operations via Hummingbot Gateway.
Supports Router swaps (Jupiter, 0x) and CLMM liquidity (Meteora, Raydium, Uniswap V3).

Note: AMM support removed. Use Router connectors for simple swaps, CLMM for liquidity provision.
"""
import logging
from typing import Dict, List
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from deps import get_accounts_service
from services.accounts_service import AccountsService
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
    accounts_service: AccountsService = Depends(get_accounts_service)
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
    accounts_service: AccountsService = Depends(get_accounts_service)
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
