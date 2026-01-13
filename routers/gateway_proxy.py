"""
Gateway Proxy Router

Catch-all router that forwards requests to Gateway server unchanged.
Dashboard calls /api/gateway-proxy/* and this router forwards to Gateway at localhost:15888/*.

This allows the dashboard to access all Gateway endpoints through the API without
needing each endpoint to be explicitly defined.

Examples:
    GET /api/gateway-proxy/wallet -> GET localhost:15888/wallet
    POST /api/gateway-proxy/wallet/add -> POST localhost:15888/wallet/add
    GET /api/gateway-proxy/config -> GET localhost:15888/config
    GET /api/gateway-proxy/trading/clmm/positions-owned -> GET localhost:15888/trading/clmm/positions-owned
"""

import json
import logging

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from deps import get_accounts_service
from services.accounts_service import AccountsService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway Proxy"], prefix="/gateway-proxy")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def forward_to_gateway(
    path: str,
    request: Request,
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Forward request to Gateway server unchanged.

    This catch-all route forwards any request to /api/gateway-proxy/* to the Gateway server.
    The request body, headers, and query parameters are passed through unchanged.
    The response from Gateway is returned unchanged.

    Examples:
        GET /api/gateway-proxy/wallet -> GET localhost:15888/wallet
        POST /api/gateway-proxy/wallet/add -> POST localhost:15888/wallet/add
        GET /api/gateway-proxy/config -> GET localhost:15888/config
    """
    gateway_client = accounts_service.gateway_client
    gateway_url = gateway_client.base_url

    # Build target URL
    target_url = f"{gateway_url}/{path}"

    # Get query parameters
    query_params = dict(request.query_params)

    # Get request body if present
    body = None
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.json()
        except Exception:
            # No JSON body or invalid JSON - that's OK for some requests
            body = None

    try:
        # Get or create aiohttp session
        session = await gateway_client._get_session()

        # Forward the request
        async with session.request(
            method=request.method,
            url=target_url,
            params=query_params if query_params else None,
            json=body if body else None,
        ) as response:
            # Read response body
            response_body = await response.read()

            # Try to parse as JSON, otherwise return as-is
            content_type = response.headers.get("Content-Type", "")

            if "application/json" in content_type:
                try:
                    json_body = json.loads(response_body)
                    return JSONResponse(
                        content=json_body,
                        status_code=response.status,
                    )
                except Exception:
                    pass

            # Return raw response
            return Response(
                content=response_body,
                status_code=response.status,
                media_type=content_type or "application/octet-stream",
            )

    except aiohttp.ClientError as e:
        logger.error(f"Gateway proxy error: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Gateway service unavailable: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Gateway proxy error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Gateway proxy error: {str(e)}"
        )


# Also expose the root endpoint for health checks
@router.get("")
async def gateway_root(
    accounts_service: AccountsService = Depends(get_accounts_service)
):
    """
    Gateway health check.
    Forwards to Gateway root endpoint to check if it's online.
    """
    gateway_client = accounts_service.gateway_client
    result = await gateway_client._request("GET", "")
    if result is None:
        raise HTTPException(status_code=503, detail="Gateway service unavailable")
    if "error" in result:
        raise HTTPException(status_code=result.get("status", 500), detail=result["error"])
    return result
