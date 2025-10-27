"""
Gateway CLMM Router - Handles DEX CLMM liquidity operations via Hummingbot Gateway.
Supports CLMM connectors (Meteora, Raydium, Uniswap V3) for concentrated liquidity positions.
"""
import logging
from typing import List, Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

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
    CLMMGetPositionInfoRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway CLMM"], prefix="/gateway")


@router.get("/clmm/pool-info")
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
    """
    try:
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

        # Return Gateway response directly
        return result

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting CLMM pool info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting CLMM pool info: {str(e)}")


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

        # Try to get token symbols from pool info (Gateway should return these)
        # For now, we'll use addresses if symbols aren't available
        base = base_token_address.split("/")[-1][:8] if base_token_address else "UNKNOWN"
        quote = quote_token_address.split("/")[-1][:8] if quote_token_address else "UNKNOWN"
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
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

        # Get wallet address
        wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
            chain=chain,
            wallet_address=request.wallet_address
        )

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
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

        # Get wallet address
        wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
            chain=chain,
            wallet_address=request.wallet_address
        )

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
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

        # Get wallet address
        wallet_address = await accounts_service.gateway_client.get_wallet_address_or_default(
            chain=chain,
            wallet_address=request.wallet_address
        )

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
        chain, network = accounts_service.gateway_client.parse_network_id(request.network)

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
