from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional, Dict, List
import re

from models import GatewayConfig, GatewayStatus, AddPoolRequest, AddTokenRequest
from services.gateway_service import GatewayService
from services.accounts_service import AccountsService
from deps import get_gateway_service, get_accounts_service

router = APIRouter(tags=["Gateway"], prefix="/gateway")


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case"""
    name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()


def snake_to_camel(name: str) -> str:
    """
    Convert snake_case to camelCase, handling common acronyms.

    Special cases:
    - url -> URL
    - cu -> CU (compute units)
    - id -> ID
    - api -> API
    - rpc -> RPC
    """
    # Map of acronyms that should be uppercase
    acronyms = {'url', 'cu', 'id', 'api', 'rpc', 'uri'}

    components = name.split('_')

    # Process each component
    result_parts = [components[0]]  # First component stays lowercase

    for component in components[1:]:
        if component.lower() in acronyms:
            # Uppercase acronyms
            result_parts.append(component.upper())
        else:
            # Title case for normal words
            result_parts.append(component.title())

    return ''.join(result_parts)


def normalize_gateway_response(data: Dict) -> Dict:
    """
    Normalize Gateway response data to Python conventions.
    - Converts camelCase to snake_case
    - Maps baseSymbol -> base, quoteSymbol -> quote
    - Creates trading_pair field
    """
    if isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            # Handle special mappings
            if key == "baseSymbol":
                normalized["base"] = value
            elif key == "quoteSymbol":
                normalized["quote"] = value
            else:
                # Convert to snake_case
                new_key = camel_to_snake(key)
                # Recursively normalize nested dicts/lists
                if isinstance(value, dict):
                    normalized[new_key] = normalize_gateway_response(value)
                elif isinstance(value, list):
                    normalized[new_key] = [normalize_gateway_response(item) if isinstance(item, dict) else item for item in value]
                else:
                    normalized[new_key] = value

        # Create trading_pair if we have base and quote
        if "base" in normalized and "quote" in normalized:
            normalized["trading_pair"] = f"{normalized['base']}-{normalized['quote']}"

        return normalized
    return data


# ============================================
# Container Management
# ============================================

@router.get("/status", response_model=GatewayStatus)
async def get_gateway_status(gateway_service: GatewayService = Depends(get_gateway_service)):
    """Get Gateway container status."""
    return gateway_service.get_status()


@router.post("/start")
async def start_gateway(
    config: GatewayConfig,
    gateway_service: GatewayService = Depends(get_gateway_service)
):
    """Start Gateway container."""
    result = gateway_service.start(config)
    if not result["success"]:
        if "already running" in result["message"]:
            raise HTTPException(status_code=400, detail=result["message"])
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@router.post("/stop")
async def stop_gateway(gateway_service: GatewayService = Depends(get_gateway_service)):
    """Stop Gateway container."""
    result = gateway_service.stop()
    if not result["success"]:
        if "not found" in result["message"]:
            raise HTTPException(status_code=404, detail=result["message"])
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@router.post("/restart")
async def restart_gateway(
    config: Optional[GatewayConfig] = None,
    gateway_service: GatewayService = Depends(get_gateway_service)
):
    """
    Restart Gateway container.

    If config is provided, the container will be removed and recreated with new configuration.
    If no config is provided, the container will be stopped and started with existing configuration.
    """
    result = gateway_service.restart(config)
    if not result["success"]:
        if "not found" in result["message"]:
            raise HTTPException(status_code=404, detail=result["message"])
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@router.get("/logs")
async def get_gateway_logs(
    tail: int = Query(default=100, ge=1, le=10000),
    gateway_service: GatewayService = Depends(get_gateway_service)
):
    """Get Gateway container logs."""
    result = gateway_service.get_logs(tail)
    if not result["success"]:
        if "not found" in result["message"]:
            raise HTTPException(status_code=404, detail=result["message"])
        raise HTTPException(status_code=500, detail=result["message"])
    return result


# ============================================
# Connectors
# ============================================

@router.get("/connectors")
async def list_connectors(accounts_service: AccountsService = Depends(get_accounts_service)) -> Dict:
    """
    List all available DEX connectors with their configurations.

    Returns connector details including name, trading types, chain, and networks.
    All fields normalized to snake_case.
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client._request("GET", "config/connectors")
        return normalize_gateway_response(result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing connectors: {str(e)}")


@router.get("/connectors/{connector_name}")
async def get_connector_config(
    connector_name: str,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Get configuration for a specific DEX connector.

    Args:
        connector_name: Connector name (e.g., 'meteora', 'raydium')
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.get_config(connector_name)
        return normalize_gateway_response(result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting connector config: {str(e)}")


@router.post("/connectors/{connector_name}")
async def update_connector_config(
    connector_name: str,
    config_updates: Dict,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Update configuration for a DEX connector.

    Args:
        connector_name: Connector name (e.g., 'meteora', 'raydium')
        config_updates: Dict with path-value pairs to update.
                       Keys can be in snake_case (e.g., {"slippage_pct": 0.5})
                       or camelCase (e.g., {"slippagePct": 0.5})
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        results = []
        for path, value in config_updates.items():
            # Convert snake_case to camelCase if needed
            camel_path = snake_to_camel(path) if '_' in path else path
            result = await accounts_service.gateway_client.update_config(connector_name, camel_path, value)
            results.append(result)

        return {
            "success": True,
            "message": f"Updated {len(results)} config parameter(s) for {connector_name}. Restart Gateway for changes to take effect.",
            "restart_required": True,
            "restart_endpoint": "POST /gateway/restart",
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating connector config: {str(e)}")


# ============================================
# Chains (Networks) and Tokens
# ============================================

@router.get("/chains")
async def list_chains(accounts_service: AccountsService = Depends(get_accounts_service)) -> Dict:
    """
    List all available blockchain chains and their networks.

    This also serves as the networks list endpoint.
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.get_chains()
        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing chains: {str(e)}")


# ============================================
# Pools
# ============================================

@router.get("/pools")
async def list_pools(
    connector_name: str = Query(description="DEX connector (e.g., 'meteora', 'raydium')"),
    network: str = Query(description="Network (e.g., 'mainnet-beta')"),
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> List[Dict]:
    """
    List all liquidity pools for a connector and network.

    Returns normalized data with snake_case fields and trading_pair.
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        pools = await accounts_service.gateway_client.get_pools(connector_name, network)

        if not pools:
            raise HTTPException(status_code=400, detail=f"No pools found for {connector_name}/{network}")

        # Normalize each pool
        normalized_pools = [normalize_gateway_response(pool) for pool in pools]
        return normalized_pools

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting pools: {str(e)}")


@router.post("/pools")
async def add_pool(
    pool_request: AddPoolRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Add a custom liquidity pool.

    Args:
        pool_request: Pool details (connector, type, network, base, quote, address)
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.add_pool(
            connector=pool_request.connector_name,
            pool_type=pool_request.type,
            network=pool_request.network,
            base_symbol=pool_request.base,
            quote_symbol=pool_request.quote,
            address=pool_request.address
        )

        if result is None:
            raise HTTPException(status_code=502, detail="Failed to add pool: Gateway returned no response")

        if "error" in result:
            status = result.get("status", 400)
            raise HTTPException(status_code=status, detail=f"Failed to add pool: {result.get('error')}")

        trading_pair = f"{pool_request.base}-{pool_request.quote}"
        return {
            "message": f"Pool {trading_pair} added to {pool_request.connector_name}/{pool_request.network}",
            "trading_pair": trading_pair
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding pool: {str(e)}")


@router.delete("/pools/{address}")
async def delete_pool(
    address: str,
    connector_name: str = Query(description="DEX connector (e.g., 'meteora', 'raydium', 'uniswap')"),
    network: str = Query(description="Network name (e.g., 'mainnet-beta', 'mainnet')"),
    pool_type: str = Query(description="Pool type (e.g., 'clmm', 'amm')"),
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Delete a liquidity pool from Gateway's pool list.

    Args:
        address: Pool contract address to remove
        connector_name: DEX connector (e.g., 'meteora', 'raydium', 'uniswap')
        network: Network name (e.g., 'mainnet-beta', 'mainnet')
        pool_type: Pool type (e.g., 'clmm', 'amm')

    Example: DELETE /gateway/pools/2sf5NYcY...?connector_name=meteora&network=mainnet-beta&pool_type=clmm
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.delete_pool(
            connector=connector_name,
            network=network,
            pool_type=pool_type,
            address=address
        )

        if result is None:
            raise HTTPException(status_code=400, detail="Failed to delete pool - no response from Gateway")

        if "error" in result:
            raise HTTPException(status_code=400, detail=f"Failed to delete pool: {result.get('error')}")

        return {
            "success": True,
            "message": f"Pool {address} deleted from {connector_name}/{network}",
            "pool_address": address,
            "connector": connector_name,
            "network": network
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting pool: {str(e)}")


# ============================================
# Networks (Primary Endpoints)
# ============================================

@router.get("/networks")
async def list_networks(accounts_service: AccountsService = Depends(get_accounts_service)) -> Dict:
    """
    List all available networks across all chains.

    Returns a flattened list of network IDs in the format 'chain-network'.
    This is the primary interface for network discovery.
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        chains_result = await accounts_service.gateway_client.get_chains()

        # Flatten chain-network combinations into network IDs
        networks = []
        if "chains" in chains_result and isinstance(chains_result["chains"], list):
            for chain_item in chains_result["chains"]:
                chain = chain_item.get("chain")
                chain_networks = chain_item.get("networks", [])
                for network in chain_networks:
                    network_id = f"{chain}-{network}"
                    networks.append({
                        "network_id": network_id,
                        "chain": chain,
                        "network": network
                    })

        return {
            "networks": networks,
            "count": len(networks)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing networks: {str(e)}")


@router.get("/networks/{network_id}")
async def get_network_config(
    network_id: str,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Get configuration for a specific network.

    Args:
        network_id: Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')

    Example: GET /gateway/networks/solana-mainnet-beta
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        result = await accounts_service.gateway_client.get_config(network_id)
        return normalize_gateway_response(result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting network config: {str(e)}")


@router.post("/networks/{network_id}")
async def update_network_config(
    network_id: str,
    config_updates: Dict,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Update configuration for a specific network.

    Args:
        network_id: Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta')
        config_updates: Dict with path-value pairs to update.
                       Keys can be in snake_case (e.g., {"node_url": "https://..."})
                       or camelCase (e.g., {"nodeURL": "https://..."})

    Example: POST /gateway/networks/solana-mainnet-beta
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        results = []
        for path, value in config_updates.items():
            # Convert snake_case to camelCase if needed
            camel_path = snake_to_camel(path) if '_' in path else path
            result = await accounts_service.gateway_client.update_config(network_id, camel_path, value)
            results.append(result)

        return {
            "success": True,
            "message": f"Updated {len(results)} config parameter(s) for {network_id}. Restart Gateway for changes to take effect.",
            "restart_required": True,
            "restart_endpoint": "POST /gateway/restart",
            "results": results
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating network config: {str(e)}")


@router.get("/networks/{network_id}/tokens")
async def get_network_tokens(
    network_id: str,
    search: Optional[str] = Query(default=None),
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Get available tokens for a network.

    Args:
        network_id: Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta')
        search: Filter tokens by symbol or name

    Example: GET /gateway/networks/solana-mainnet-beta/tokens?search=USDC
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id into chain and network
        parts = network_id.split('-', 1)
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail=f"Invalid network_id format. Expected 'chain-network', got '{network_id}'")

        chain, network = parts
        result = await accounts_service.gateway_client.get_tokens(chain, network)

        # Apply search filter
        if search and "tokens" in result:
            search_lower = search.lower()
            result["tokens"] = [
                token for token in result["tokens"]
                if search_lower in token.get("symbol", "").lower() or
                   search_lower in token.get("name", "").lower()
            ]

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting network tokens: {str(e)}")


@router.post("/networks/{network_id}/tokens")
async def add_network_token(
    network_id: str,
    token_request: AddTokenRequest,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Add a custom token to Gateway's token list for a specific network.

    Args:
        network_id: Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')
        token_request: Token details (address, symbol, name, decimals)

    Example: POST /gateway/networks/ethereum-mainnet/tokens
    {
        "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "symbol": "USDC",
        "name": "USD Coin",
        "decimals": 6
    }

    Note: After adding a token, restart Gateway for changes to take effect.
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id into chain and network
        parts = network_id.split('-', 1)
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail=f"Invalid network_id format. Expected 'chain-network', got '{network_id}'")

        chain, network = parts

        # Use symbol as name if name is not provided
        token_name = token_request.name if token_request.name else token_request.symbol

        result = await accounts_service.gateway_client.add_token(
            chain=chain,
            network=network,
            address=token_request.address,
            symbol=token_request.symbol,
            name=token_name,
            decimals=token_request.decimals
        )

        if "error" in result:
            raise HTTPException(status_code=400, detail=f"Failed to add token: {result.get('error')}")

        return {
            "success": True,
            "message": f"Token {token_request.symbol} added to {network_id}. Restart Gateway for changes to take effect.",
            "restart_required": True,
            "restart_endpoint": "POST /gateway/restart",
            "token": {
                "symbol": token_request.symbol,
                "address": token_request.address,
                "network_id": network_id
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding token: {str(e)}")


@router.delete("/networks/{network_id}/tokens/{token_address}")
async def delete_network_token(
    network_id: str,
    token_address: str,
    accounts_service: AccountsService = Depends(get_accounts_service)
) -> Dict:
    """
    Delete a custom token from Gateway's token list for a specific network.

    Args:
        network_id: Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')
        token_address: Token contract address to delete

    Example: DELETE /gateway/networks/solana-mainnet-beta/tokens/9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump

    Note: After deleting a token, restart Gateway for changes to take effect.
    """
    try:
        if not await accounts_service.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

        # Parse network_id into chain and network
        parts = network_id.split('-', 1)
        if len(parts) != 2:
            raise HTTPException(status_code=400, detail=f"Invalid network_id format. Expected 'chain-network', got '{network_id}'")

        chain, network = parts

        result = await accounts_service.gateway_client.delete_token(
            chain=chain,
            network=network,
            token_address=token_address
        )

        if "error" in result:
            raise HTTPException(status_code=400, detail=f"Failed to delete token: {result.get('error')}")

        return {
            "success": True,
            "message": f"Token {token_address} deleted from {network_id}. Restart Gateway for changes to take effect.",
            "restart_required": True,
            "restart_endpoint": "POST /gateway/restart",
            "token_address": token_address,
            "network_id": network_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting token: {str(e)}")
