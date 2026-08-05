"""
Gateway AMM Router - Handles DEX AMM liquidity operations via Hummingbot Gateway.

Supports AMM connectors (Meteora DAMM v2, Raydium CPMM, Uniswap/Pancakeswap V2). This is a
deliberately separate surface from CLMM: AMM was previously removed from hummingbot-api and is
re-added here to expose Gateway's standardized /trading/amm/* routes.

Stateless by design (no position persistence) — the caller/agent holds position state. Meteora
DAMM v2 positions are NFTs, so the routes are position-addressed: remove requires position_address,
add takes it optionally (omit = new position), position-info returns a positions[] breakdown, and
positions-owned lists all of a wallet's positions. Fungible-LP AMMs ignore position_address and
Gateway rejects positions-owned for them with a 400, surfaced here as-is.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from deps import get_accounts_service
from models import (
    AMMAddLiquidityRequest,
    AMMCreatePoolRequest,
    AMMCreatePoolResponse,
    AMMExecuteSwapRequest,
    AMMPoolInfoResponse,
    AMMPositionInfoResponse,
    AMMPositionsOwnedRequest,
    AMMQuoteLiquidityRequest,
    AMMQuoteLiquidityResponse,
    AMMQuoteSwapRequest,
    AMMQuoteSwapResponse,
    AMMRemoveLiquidityRequest,
    AMMTransactionResponse,
)
from services.accounts_service import AccountsService
from services.gateway_client import GatewayError, check_gateway_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway AMM"], prefix="/gateway")


async def _require_gateway(accounts_service: AccountsService) -> None:
    if not await accounts_service.gateway_client.ping():
        raise HTTPException(status_code=503, detail="Gateway service is not available")


async def _resolve_wallet(accounts_service: AccountsService, network: str, wallet_address) -> str:
    chain, _ = accounts_service.gateway_client.parse_network_id(network)
    return await accounts_service.gateway_client.get_wallet_address_or_default(
        chain=chain, wallet_address=wallet_address
    )


# ----------------------------- Reads -----------------------------

@router.get("/amm/pool-info", response_model=AMMPoolInfoResponse, response_model_by_alias=False)
async def get_amm_pool_info(
    connector: str,
    network: str,
    pool_address: str,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """Get AMM pool information (reserves, price, base fee) by pool address."""
    try:
        await _require_gateway(accounts_service)
        result = check_gateway_error(await accounts_service.gateway_client.amm_pool_info(
            connector=connector, chain_network=network, pool_address=pool_address,
        ))
        return AMMPoolInfoResponse(**result)
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error getting AMM pool info: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting AMM pool info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting AMM pool info: {str(e)}")


@router.get("/amm/position-info", response_model=AMMPositionInfoResponse, response_model_by_alias=False)
async def get_amm_position_info(
    connector: str,
    network: str,
    pool_address: str,
    wallet_address: Optional[str] = None,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """Get a wallet's aggregate liquidity in an AMM pool plus a per-position breakdown (DAMM v2)."""
    try:
        await _require_gateway(accounts_service)
        wallet_address = await _resolve_wallet(accounts_service, network, wallet_address)
        result = check_gateway_error(await accounts_service.gateway_client.amm_position_info(
            connector=connector, chain_network=network, pool_address=pool_address,
            wallet_address=wallet_address,
        ))
        return AMMPositionInfoResponse(**result)
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error getting AMM position info: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting AMM position info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting AMM position info: {str(e)}")


@router.post("/amm/positions-owned", response_model=List[AMMPositionInfoResponse], response_model_by_alias=False)
async def get_amm_positions_owned(
    request: AMMPositionsOwnedRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    List all of a wallet's AMM positions across pools (Meteora DAMM v2 only).

    Fungible-LP AMMs (raydium, uniswap, pancakeswap) have no enumerable positions; Gateway rejects
    them with a 400, surfaced here unchanged. Use position-info with a specific pool address instead.
    """
    try:
        await _require_gateway(accounts_service)
        wallet_address = await _resolve_wallet(accounts_service, request.network, request.wallet_address)
        result = check_gateway_error(await accounts_service.gateway_client.amm_positions_owned(
            connector=request.connector, chain_network=request.network, wallet_address=wallet_address,
        ))
        positions = result if isinstance(result, list) else []
        return [AMMPositionInfoResponse(**pos) for pos in positions]
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error getting AMM positions owned: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting AMM positions owned: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting AMM positions owned: {str(e)}")


@router.post("/amm/quote-swap", response_model=AMMQuoteSwapResponse, response_model_by_alias=False)
async def quote_amm_swap(
    request: AMMQuoteSwapRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """Quote a swap against a specific AMM pool (pool-scoped, not router)."""
    try:
        await _require_gateway(accounts_service)
        result = check_gateway_error(await accounts_service.gateway_client.amm_quote_swap(
            connector=request.connector, chain_network=request.network, pool_address=request.pool_address,
            base_token=request.base_token, side=request.side, amount=float(request.amount),
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else None,
        ))
        return AMMQuoteSwapResponse(**result)
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error quoting AMM swap: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error quoting AMM swap: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error quoting AMM swap: {str(e)}")


@router.post("/amm/quote-liquidity", response_model=AMMQuoteLiquidityResponse, response_model_by_alias=False)
async def quote_amm_liquidity(
    request: AMMQuoteLiquidityRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """Quote a two-sided liquidity deposit."""
    try:
        await _require_gateway(accounts_service)
        result = check_gateway_error(await accounts_service.gateway_client.amm_quote_liquidity(
            connector=request.connector, chain_network=request.network, pool_address=request.pool_address,
            base_token_amount=float(request.base_token_amount), quote_token_amount=float(request.quote_token_amount),
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else None,
        ))
        return AMMQuoteLiquidityResponse(**result)
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error quoting AMM liquidity: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error quoting AMM liquidity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error quoting AMM liquidity: {str(e)}")


# ----------------------------- Writes -----------------------------

@router.post("/amm/execute-swap", response_model=AMMTransactionResponse)
async def execute_amm_swap(
    request: AMMExecuteSwapRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """Execute a swap against a specific AMM pool."""
    try:
        await _require_gateway(accounts_service)
        wallet_address = await _resolve_wallet(accounts_service, request.network, request.wallet_address)
        result = check_gateway_error(await accounts_service.gateway_client.amm_execute_swap(
            connector=request.connector, chain_network=request.network, wallet_address=wallet_address,
            pool_address=request.pool_address, base_token=request.base_token, side=request.side,
            amount=float(request.amount),
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else None,
        ))
        return AMMTransactionResponse(**result)
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error executing AMM swap: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error executing AMM swap: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error executing AMM swap: {str(e)}")


@router.post("/amm/add-liquidity", response_model=AMMTransactionResponse)
async def add_amm_liquidity(
    request: AMMAddLiquidityRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Add two-sided liquidity to an AMM pool.

    Meteora DAMM v2: pass position_address to add to that NFT position; omit it to open a new one.
    Fungible-LP AMMs ignore position_address.
    """
    try:
        await _require_gateway(accounts_service)
        wallet_address = await _resolve_wallet(accounts_service, request.network, request.wallet_address)
        result = check_gateway_error(await accounts_service.gateway_client.amm_add_liquidity(
            connector=request.connector, chain_network=request.network, wallet_address=wallet_address,
            pool_address=request.pool_address, base_token_amount=float(request.base_token_amount),
            quote_token_amount=float(request.quote_token_amount),
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else None,
            position_address=request.position_address,
        ))
        return AMMTransactionResponse(**result)
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error adding AMM liquidity: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error adding AMM liquidity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error adding AMM liquidity: {str(e)}")


@router.post("/amm/remove-liquidity", response_model=AMMTransactionResponse)
async def remove_amm_liquidity(
    request: AMMRemoveLiquidityRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Remove liquidity from an AMM pool.

    Meteora DAMM v2 requires position_address (positions are NFTs); Gateway rejects a missing one
    with a 400, surfaced here unchanged, so "remove 100%" is a true exit of the named position.
    """
    try:
        await _require_gateway(accounts_service)
        wallet_address = await _resolve_wallet(accounts_service, request.network, request.wallet_address)
        result = check_gateway_error(await accounts_service.gateway_client.amm_remove_liquidity(
            connector=request.connector, chain_network=request.network, wallet_address=wallet_address,
            pool_address=request.pool_address, percentage_to_remove=float(request.percentage_to_remove),
            slippage_pct=float(request.slippage_pct) if request.slippage_pct is not None else None,
            position_address=request.position_address,
        ))
        return AMMTransactionResponse(**result)
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error removing AMM liquidity: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error removing AMM liquidity: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error removing AMM liquidity: {str(e)}")


@router.post("/amm/create-pool", response_model=AMMCreatePoolResponse, response_model_by_alias=False)
async def create_amm_pool(
    request: AMMCreatePoolRequest,
    accounts_service: AccountsService = Depends(get_accounts_service),
):
    """
    Create and seed a new AMM pool.

    Seed price priority: initial_price → quote_token_amount ratio → live market price (anti-snipe).
    Connector extras are sent only when provided (config_address for meteora, fee_config_index for
    raydium, gas_price/max_gas for uniswap).
    """
    try:
        await _require_gateway(accounts_service)
        wallet_address = await _resolve_wallet(accounts_service, request.network, request.wallet_address)
        result = check_gateway_error(await accounts_service.gateway_client.amm_create_pool(
            connector=request.connector, chain_network=request.network, wallet_address=wallet_address,
            base_token=request.base_token, quote_token=request.quote_token,
            base_token_amount=float(request.base_token_amount),
            quote_token_amount=float(request.quote_token_amount) if request.quote_token_amount is not None else None,
            initial_price=float(request.initial_price) if request.initial_price is not None else None,
            config_address=request.config_address,
            fee_config_index=request.fee_config_index,
            gas_price=float(request.gas_price) if request.gas_price is not None else None,
            max_gas=request.max_gas,
        ))
        return AMMCreatePoolResponse(**result)
    except HTTPException:
        raise
    except GatewayError as e:
        raise HTTPException(status_code=e.status, detail=f"Gateway error creating AMM pool: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating AMM pool: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error creating AMM pool: {str(e)}")
