import logging
import ssl
from typing import Any, Callable, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Connectors whose bare name maps to a router-type swap provider on Gateway.
# All other connectors default to their CLMM route (meteora, orca, raydium, pancakeswap-sol).
ROUTER_CONNECTORS = {"jupiter", "0x", "uniswap", "pancakeswap", "dflow", "okx", "titan"}

# The single source for chain -> native gas token. Every writer of gas_token
# columns (routers and the transaction poller) must use this — drifted local
# copies previously produced "MATIC", None, and "UNKNOWN" for the same chain.
_NATIVE_GAS_TOKENS = {
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


def get_native_gas_token(chain: str) -> str:
    """Native gas token symbol for a chain (e.g. 'solana' -> 'SOL')."""
    return _NATIVE_GAS_TOKENS.get(chain.lower(), "UNKNOWN")


class GatewayError(Exception):
    """A Gateway HTTP request that completed with a non-OK status."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def check_gateway_error(result: Optional[Any]) -> Any:
    """
    Validate a GatewayClient response, raising instead of letting error shapes flow onward.

    ``_request`` returns ``{"error": <str>, "status": <int>}`` on non-OK HTTP responses and
    ``None`` on connection errors. Callers that treat those as data silently corrupt results
    (e.g. rendering a 404 as price 0), so pass every response through this helper unless the
    caller explicitly branches on the error shape.
    """
    if result is None:
        raise GatewayError("No response from Gateway (connection error)", 503)
    if isinstance(result, dict) and set(result.keys()) == {"error", "status"}:
        raise GatewayError(str(result["error"]), int(result["status"]))
    return result


class GatewayClient:
    """
    Simplified Gateway HTTP client for API integration.
    Provides essential functionality for wallet management and balance queries.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:15888",
        ssl_context_factory: Optional[Callable[[], ssl.SSLContext]] = None,
    ):
        """
        Args:
            base_url: Gateway base URL. Use an ``https://`` scheme together with
                ``ssl_context_factory`` to talk to a secured (mTLS) Gateway (SEC-048).
            ssl_context_factory: Zero-arg callable returning a client SSLContext presenting the
                shared client cert. Called lazily (and cached) on the first ``https`` request, so
                certs generated *after* the API started — e.g. once the Gateway is started — are
                picked up without an API restart. Ignored for plain ``http://``.
        """
        self.base_url = base_url
        self._ssl_context_factory = ssl_context_factory
        self._ssl_context: Optional[ssl.SSLContext] = None
        self._is_https = base_url.lower().startswith("https://")
        self._session: Optional[aiohttp.ClientSession] = None
        # Guards the "certs unavailable" warning so background pollers don't spam it every cycle
        # while the Gateway is simply not started. Logged once on the transition, then suppressed
        # until certs become available again.
        self._certs_unavailable_warned = False

    @staticmethod
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

    async def get_wallet_address_or_default(self, chain: str, wallet_address: Optional[str] = None) -> str:
        """Get wallet address - use provided or get default for chain"""
        if wallet_address:
            return wallet_address

        default_wallet = await self.get_default_wallet_address(chain)
        if not default_wallet:
            raise ValueError(f"No wallet configured for chain '{chain}'")
        # Gateway's fresh config templates write "<solana-wallet-address>" /
        # "<ethereum-wallet-address>" as the placeholder — passing that literal
        # string on would surface as an opaque Gateway address-validation error.
        if default_wallet.startswith("<") and default_wallet.endswith(">"):
            raise ValueError(f"No valid wallet configured for chain '{chain}' (found placeholder: {default_wallet})")
        return default_wallet

    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Lazily build and cache the client SSLContext for https Gateways.

        Deferred so certs created after startup (once the Gateway is started) are picked up.
        Raises FileNotFoundError (from the factory) while the cert set is still absent.
        """
        if not self._is_https or self._ssl_context_factory is None:
            return None
        if self._ssl_context is None:
            self._ssl_context = self._ssl_context_factory()
            # Certs are now available; allow a fresh warning if they ever disappear again.
            self._certs_unavailable_warned = False
        return self._ssl_context

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            ssl_context = self._get_ssl_context()
            if ssl_context is not None:
                connector = aiohttp.TCPConnector(ssl=ssl_context)
                self._session = aiohttp.ClientSession(connector=connector)
            else:
                self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close the aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, params: Dict = None, json: Dict = None) -> Optional[Dict]:
        """Make HTTP request to Gateway"""
        url = f"{self.base_url}/{path}"

        try:
            session = await self._get_session()
        except FileNotFoundError as e:
            # https Gateway selected but the shared certs aren't available yet (Gateway not
            # started). Return a clean error instead of crashing the caller. Warn only once on
            # the transition so background pollers don't spam the log every cycle while the
            # Gateway stays unstarted (a normal, optional state).
            if not self._certs_unavailable_warned:
                logger.warning(f"Gateway mTLS certs unavailable, cannot reach {url}: {e}")
                self._certs_unavailable_warned = True
            else:
                logger.debug(f"Gateway mTLS certs still unavailable, cannot reach {url}: {e}")
            return {"error": "Gateway client certificates not available; start the Gateway first", "status": 503}

        try:
            if method == "GET":
                async with session.get(url, params=params) as response:
                    if not response.ok:
                        error_body = await self._get_error_body(response)
                        logger.warning(f"Gateway request failed: {method} {url} - {response.status} - {error_body}")
                        return {"error": error_body, "status": response.status}
                    return await response.json()
            elif method == "POST":
                async with session.post(url, params=params, json=json) as response:
                    if not response.ok:
                        error_body = await self._get_error_body(response)
                        logger.warning(f"Gateway request failed: {method} {url} - {response.status} - {error_body}")
                        return {"error": error_body, "status": response.status}
                    return await response.json()
            elif method == "DELETE":
                async with session.delete(url, params=params, json=json) as response:
                    if not response.ok:
                        error_body = await self._get_error_body(response)
                        logger.warning(f"Gateway request failed: {method} {url} - {response.status} - {error_body}")
                        return {"error": error_body, "status": response.status}
                    return await response.json()
        except aiohttp.ClientError as e:
            logger.debug(f"Gateway request error: {method} {url} - {e}")
            return None
        except Exception as e:
            logger.debug(f"Gateway request failed: {method} {url} - {e}")
            raise

    async def _get_error_body(self, response: aiohttp.ClientResponse) -> str:
        """Extract error message from response body"""
        try:
            data = await response.json()
            if isinstance(data, dict):
                return data.get("message") or data.get("error") or str(data)
            return str(data)
        except Exception:
            try:
                return await response.text()
            except Exception:
                return f"HTTP {response.status}"

    async def ping(self) -> bool:
        """Check if Gateway is online"""
        try:
            response = await self._request("GET", "")
            return response.get("status") == "ok"
        except Exception:
            return False

    async def get_wallets(self) -> List[Dict]:
        """Get all connected wallets"""
        return await self._request("GET", "wallet")

    async def get_default_wallet_address(self, chain: str) -> Optional[str]:
        """Get default wallet address for a chain from Gateway config.

        Raises GatewayError(503) when Gateway is unreachable — an unreachable
        Gateway must not masquerade as "no wallet configured" (a 400 that sends
        the operator chasing the wrong problem).
        """
        config = await self._request("GET", "config", params={"namespace": chain})
        if config is None:
            raise GatewayError("Gateway service is not available", status=503)
        if isinstance(config, dict) and set(config.keys()) == {"error", "status"}:
            raise GatewayError(str(config["error"]), status=config.get("status", 502))
        return config.get("defaultWallet")

    async def get_all_wallet_addresses(self, chain: Optional[str] = None) -> Dict[str, List[str]]:
        """
        Get all wallet addresses, optionally filtered by chain.

        Args:
            chain: Optional chain filter (e.g., 'solana', 'ethereum').
                   If not provided, returns wallets for all chains.

        Returns:
            Dict mapping chain name to list of wallet addresses.
            Example: {"solana": ["addr1", "addr2"], "ethereum": ["addr3"]}
        """
        try:
            wallets = await self.get_wallets()
            if wallets is None:
                return {}

            result = {}
            for wallet in wallets:
                wallet_chain = wallet.get("chain")
                if chain and wallet_chain != chain:
                    continue

                # Hardware (Ledger) wallets live in a separate list — without them
                # the discovery/balance sweeps never see hardware-held positions.
                addresses = list(wallet.get("walletAddresses", [])) + list(wallet.get("hardwareWalletAddresses", []))
                if addresses and wallet_chain:
                    result[wallet_chain] = addresses

            return result
        except Exception as e:
            logger.error(f"Error getting all wallet addresses: {e}")
            return {}

    async def add_wallet(self, chain: str, private_key: str, set_default: bool = True) -> Dict:
        """Add a wallet to Gateway"""
        return await self._request("POST", "wallet/add", json={
            "chain": chain,
            "privateKey": private_key,
            "setDefault": set_default
        })

    async def remove_wallet(self, chain: str, address: str) -> Dict:
        """Remove a wallet from Gateway"""
        return await self._request("DELETE", "wallet/remove", json={
            "chain": chain,
            "address": address
        })

    async def set_default_wallet(self, chain: str, address: str) -> Dict:
        """Set the default wallet for a chain in Gateway"""
        return await self._request("POST", "wallet/setDefault", json={
            "chain": chain,
            "address": address
        })

    async def get_balances(self, chain: str, network: str, address: str, tokens: Optional[List[str]] = None) -> Dict:
        """Get token balances for a wallet"""
        return await self._request("POST", f"chains/{chain}/balances", json={
            "network": network,
            "address": address,
            "tokens": tokens if tokens is not None else []
        })

    async def get_chains(self) -> Dict:
        """Get available chains"""
        return await self._request("GET", "config/chains")

    async def get_connectors(self) -> Dict:
        """Get available connectors with their trading types"""
        return await self._request("GET", "config/connectors")

    async def get_default_network(self, chain: str) -> Optional[str]:
        """Get default network for a chain"""
        try:
            config = await self._request("GET", "config", params={"namespace": chain})
            return config.get("defaultNetwork")
        except Exception:
            return None

    async def get_tokens(self, chain: str, network: str) -> Dict:
        """Get available tokens for a chain/network"""
        return await self._request("GET", "tokens", params={
            "chain": chain,
            "network": network
        })

    async def add_token(self, chain: str, network: str, address: str, symbol: str, name: str, decimals: int) -> Dict:
        """Add a custom token to Gateway's token list"""
        return await self._request("POST", "tokens", json={
            "chain": chain,
            "network": network,
            "token": {
                "address": address,
                "symbol": symbol,
                "name": name,
                "decimals": decimals
            }
        })

    async def delete_token(self, chain: str, network: str, token_address: str) -> Dict:
        """Delete a custom token from Gateway's token list"""
        return await self._request("DELETE", f"tokens/{token_address}", params={
            "chain": chain,
            "network": network
        })

    async def save_token(self, chain: str, network: str, token_address: str) -> Dict:
        """Save a token by address - auto-fetches info from GeckoTerminal"""
        chain_network = f"{chain}-{network}"
        return await self._request("POST", f"tokens/save/{token_address}", params={
            "chainNetwork": chain_network
        }, json={})

    async def get_config(self, namespace: str) -> Dict:
        """Get configuration for a specific namespace (connector or chain-network)"""
        return await self._request("GET", "config", params={"namespace": namespace})

    async def update_config(self, namespace: str, path: str, value: Any) -> Dict:
        """Update a configuration value for a namespace"""
        return await self._request("POST", "config/update", json={
            "namespace": namespace,
            "path": path,
            "value": value
        })

    async def get_api_keys(self) -> Dict:
        """Get all configured API keys from Gateway"""
        return await self._request("GET", "config", params={"namespace": "apiKeys"})

    async def update_api_keys(self, api_keys: Dict[str, str]) -> List[Dict]:
        """
        Update API keys in Gateway configuration.

        Args:
            api_keys: Dict mapping provider name to API key value
                     (e.g., {"helius": "abc123", "infura": "xyz789"})

        Returns:
            List of results for each API key update
        """
        results = []
        for provider, api_key in api_keys.items():
            result = await self._request("POST", "config/update", json={
                "namespace": "apiKeys",
                "path": provider,
                "value": api_key
            })
            results.append(result)
        return results

    async def get_pools(
        self,
        chain: str,
        network: str,
        connector: Optional[str] = None,
        pool_type: Optional[str] = None,
        search: Optional[str] = None
    ) -> List[Dict]:
        """Get pools for a chain and network with optional filtering"""
        params = {
            "chain": chain,
            "network": network
        }
        if connector:
            params["connector"] = connector
        if pool_type:
            params["type"] = pool_type.lower()
        if search:
            params["search"] = search
        return await self._request("GET", "pools", params=params)

    async def add_pool(
        self,
        chain: str,
        network: str,
        connector: str,
        pool_type: str,
        address: str,
        base_symbol: str,
        quote_symbol: str,
        base_token_address: str,
        quote_token_address: str,
        fee_pct: Optional[float] = None
    ) -> Dict:
        """Add a new pool"""
        payload = {
            "chain": chain,
            "connector": connector,
            "type": pool_type.lower(),  # Gateway expects lowercase (amm, clmm)
            "network": network,
            "address": address,
            "baseSymbol": base_symbol,
            "quoteSymbol": quote_symbol,
            "baseTokenAddress": base_token_address,
            "quoteTokenAddress": quote_token_address
        }
        if fee_pct is not None:
            payload["feePct"] = fee_pct
        return await self._request("POST", "pools", json=payload)

    async def save_pool(self, chain_network: str, address: str) -> Dict:
        """Save a pool by address using GeckoTerminal lookup"""
        return await self._request("POST", f"pools/save/{address}", params={
            "chainNetwork": chain_network
        }, json={})

    async def delete_pool(self, chain: str, network: str, address: str) -> Dict:
        """Delete a pool from Gateway's pool list"""
        return await self._request("DELETE", f"pools/{address}", params={
            "chain": chain,
            "network": network
        })

    # ============================================
    # Swap Operations (unified /trading/swap endpoints)
    # ============================================

    @staticmethod
    def normalize_swap_connector(connector: str) -> str:
        """
        Normalize a connector name to Gateway's 'name/type' swap-provider format.

        'jupiter' -> 'jupiter/router', 'meteora' -> 'meteora/clmm',
        'raydium/amm' -> 'raydium/amm' (already typed, passed through).
        """
        if "/" in connector:
            return connector
        connector_type = "router" if connector in ROUTER_CONNECTORS else "clmm"
        return f"{connector}/{connector_type}"

    async def quote_swap(
        self,
        connector: str,
        chain_network: str,
        base_asset: str,
        quote_asset: str,
        amount: float,
        side: str,
        slippage_pct: Optional[float] = None,
        extra_params: Optional[Dict] = None
    ) -> Dict:
        """
        Get a swap quote via Gateway's unified /trading/swap/quote endpoint.

        Args:
            connector: Swap provider, either bare ('jupiter', 'meteora') or typed
                ('jupiter/router', 'raydium/amm', 'meteora/clmm').
            chain_network: 'chain-network' format (e.g. 'solana-mainnet-beta').
                For amm/clmm providers Gateway resolves the pool from its pool list.
            extra_params: Connector-specific query params under Gateway's own names
                (e.g. approximateIfNoExactOut). The router validates keys first.
        """
        params = {
            "chainNetwork": chain_network,
            "connector": self.normalize_swap_connector(connector),
            "baseToken": base_asset,
            "quoteToken": quote_asset,
            "amount": str(amount),
            "side": side.upper()
        }
        if slippage_pct is not None:
            params["slippagePct"] = str(slippage_pct)
        if extra_params:
            # Query params must be strings for aiohttp; Gateway's schema coerces
            # "true"/"false" back to booleans.
            for key, value in extra_params.items():
                params[key] = str(value).lower() if isinstance(value, bool) else str(value)

        return await self._request("GET", "trading/swap/quote", params=params)

    async def execute_swap(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        base_asset: str,
        quote_asset: str,
        amount: float,
        side: str,
        slippage_pct: Optional[float] = None,
        extra_params: Optional[Dict] = None
    ) -> Dict:
        """Execute a swap via Gateway's unified /trading/swap/execute endpoint.

        extra_params carries connector-specific params under Gateway's own names
        (e.g. approximateIfNoExactOut). The router validates keys first.
        """
        payload = {
            "chainNetwork": chain_network,
            "connector": self.normalize_swap_connector(connector),
            "walletAddress": wallet_address,
            "baseToken": base_asset,
            "quoteToken": quote_asset,
            "amount": amount,
            "side": side.upper()
        }
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct
        if extra_params:
            payload.update(extra_params)

        return await self._request("POST", "trading/swap/execute", json=payload)

    # ============================================
    # Liquidity Operations - CLMM (unified /trading/clmm endpoints)
    # ============================================

    async def clmm_open_position(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        pool_address: str,
        lower_price: float,
        upper_price: float,
        base_token_amount: Optional[float] = None,
        quote_token_amount: Optional[float] = None,
        slippage_pct: Optional[float] = None,
        extra_params: Optional[Dict] = None
    ) -> Dict:
        """Open a NEW CLMM position with initial liquidity"""
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "poolAddress": pool_address,
            "lowerPrice": lower_price,
            "upperPrice": upper_price
        }
        if base_token_amount is not None:
            payload["baseTokenAmount"] = base_token_amount
        if quote_token_amount is not None:
            payload["quoteTokenAmount"] = quote_token_amount
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct

        # Connector-specific parameters (e.g. Meteora's strategyType)
        if extra_params:
            payload.update(extra_params)

        return await self._request("POST", "trading/clmm/open", json=payload)

    async def clmm_add_liquidity(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        position_address: str,
        base_token_amount: Optional[float] = None,
        quote_token_amount: Optional[float] = None,
        slippage_pct: Optional[float] = None,
        extra_params: Optional[Dict] = None
    ) -> Dict:
        """Add more liquidity to an existing CLMM position"""
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "positionAddress": position_address
        }
        if base_token_amount is not None:
            payload["baseTokenAmount"] = base_token_amount
        if quote_token_amount is not None:
            payload["quoteTokenAmount"] = quote_token_amount
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct

        # Connector-specific parameters (e.g. Meteora's strategyType)
        if extra_params:
            payload.update(extra_params)

        return await self._request("POST", "trading/clmm/add", json=payload)

    async def clmm_close_position(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        position_address: str
    ) -> Dict:
        """Close a CLMM position completely"""
        return await self._request("POST", "trading/clmm/close", json={
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "positionAddress": position_address
        })

    async def clmm_remove_liquidity(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        position_address: str,
        percentage_to_remove: float,
        slippage_pct: Optional[float] = None
    ) -> Dict:
        """Remove liquidity from a CLMM position (partial).

        slippage_pct is only honored by the Orca connector; others ignore it.
        """
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "positionAddress": position_address,
            "percentageToRemove": percentage_to_remove
        }
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct

        return await self._request("POST", "trading/clmm/remove", json=payload)

    async def clmm_position_info(
        self,
        connector: str,
        chain_network: str,
        position_address: str
    ) -> Dict:
        """
        Get CLMM position information including pending fees.

        Note: Gateway returns 500 instead of 404 when position doesn't exist (is closed).
        Callers should treat 500 errors as "position not found/closed".
        """
        # Validate required parameters
        if not connector:
            raise ValueError("connector is required for clmm_position_info")
        if not chain_network:
            raise ValueError("chain_network is required for clmm_position_info")
        if not position_address:
            raise ValueError("position_address is required for clmm_position_info")

        params = {
            "connector": connector,
            "chainNetwork": chain_network,
            "positionAddress": position_address
        }
        return await self._request("GET", "trading/clmm/position-info", params=params)

    async def clmm_positions_owned(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str
    ) -> List[Dict]:
        """
        Get ALL CLMM positions owned by a wallet on a connector.

        Gateway's /trading/clmm/positions-owned takes no pool filter (its handler
        reads only connector, chainNetwork and walletAddress); callers that care
        about one pool filter the returned rows by their poolAddress field.

        Args:
            connector: CLMM connector (e.g., 'meteora', 'raydium')
            chain_network: Chain and network in format 'chain-network' (e.g., 'solana-mainnet-beta')
            wallet_address: Wallet address to query

        Returns:
            List of position dictionaries with fields like:
            - address: Position NFT address
            - poolAddress: Pool address
            - baseTokenAddress, quoteTokenAddress
            - baseTokenAmount, quoteTokenAmount
            - baseFeeAmount, quoteFeeAmount
            - lowerBinId, upperBinId
            - lowerPrice, upperPrice, price
        """
        params = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
        }
        return await self._request("GET", "trading/clmm/positions-owned", params=params)

    async def clmm_quote_position(
        self,
        connector: str,
        chain_network: str,
        pool_address: str,
        lower_price: float,
        upper_price: float,
        base_token_amount: Optional[float] = None,
        quote_token_amount: Optional[float] = None,
        slippage_pct: Optional[float] = None,
    ) -> Dict:
        """Quote the base/quote split a candidate position would take, without signing anything."""
        params = {
            "connector": connector,
            "chainNetwork": chain_network,
            "poolAddress": pool_address,
            "lowerPrice": lower_price,
            "upperPrice": upper_price,
        }
        if base_token_amount is not None:
            params["baseTokenAmount"] = base_token_amount
        if quote_token_amount is not None:
            params["quoteTokenAmount"] = quote_token_amount
        if slippage_pct is not None:
            params["slippagePct"] = slippage_pct
        return await self._request("GET", "trading/clmm/quote-position", params=params)

    async def clmm_create_pool(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        base_token: str,
        quote_token: str,
        initial_price: Optional[float] = None,
        extra_params: Optional[Dict] = None,
    ) -> Dict:
        """Create a new (empty) CLMM pool.

        extra_params carries the connector-specific create params under Gateway's own
        names (binStep, feeBps, ammConfigIndex) and is spread into the payload — the
        same contract as clmm open's extra_params. The router validates keys before
        this is called.
        """
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "baseToken": base_token,
            "quoteToken": quote_token,
        }
        if initial_price is not None:
            payload["initialPrice"] = initial_price
        if extra_params:
            payload.update(extra_params)
        return await self._request("POST", "trading/clmm/create-pool", json=payload)

    async def clmm_collect_fees(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        position_address: str
    ) -> Dict:
        """Collect accumulated fees from a CLMM position"""
        return await self._request("POST", "trading/clmm/collect-fees", json={
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "positionAddress": position_address
        })

    async def clmm_pool_info(
        self,
        connector: str,
        chain_network: str,
        pool_address: str,
        bin_count: int = 0
    ) -> Dict:
        """Get detailed CLMM pool information by pool address.

        bin_count > 0 asks Gateway for the per-tick liquidity distribution
        (`bins`) around the active tick. Meteora always returns its bins and
        ignores the parameter; orca, raydium, uniswap and pancakeswap honour it.
        """
        params = {
            "connector": connector,
            "chainNetwork": chain_network,
            "poolAddress": pool_address
        }
        if bin_count:
            params["binCount"] = bin_count
        return await self._request("GET", "trading/clmm/pool-info", params=params)

    async def clmm_fetch_pools(
        self,
        connector: str,
        network: str,
        limit: int = 50,
        query: Optional[str] = None,
        sort_by: Optional[str] = None,
        page: Optional[int] = None,
        include_unverified: Optional[bool] = None,
        sort_direction: Optional[str] = None,
        verified_only: Optional[bool] = None
    ) -> Dict:
        """
        Discover CLMM pools from the connector's own listing API (meteora, orca).

        This is a per-connector Gateway route (no unified equivalent): it proxies the
        DEX's pool-discovery API rather than Gateway's saved pool list. The schemas
        differ per connector — meteora takes page/includeUnverified and a
        "field:direction" sortBy; orca takes sortDirection/verifiedOnly and does not
        paginate. Only keys the caller sets are sent; AJV strips unknown keys
        Gateway-side, so sending the wrong connector's knob would be a silent no-op.
        """
        params = {
            "network": network,
            "limit": limit,
        }
        if query:
            params["query"] = query
        if sort_by:
            params["sortBy"] = sort_by
        if page is not None and page > 0:
            params["page"] = page
        if include_unverified is not None:
            params["includeUnverified"] = "true" if include_unverified else "false"
        if sort_direction:
            params["sortDirection"] = sort_direction
        if verified_only is not None:
            params["verifiedOnly"] = "true" if verified_only else "false"

        return await self._request("GET", f"connectors/{connector}/clmm/fetch-pools", params=params)

    # ============================================
    # AMM Liquidity (Meteora DAMM v2, Raydium CPMM, Uniswap/Pancakeswap V2)
    # ============================================
    # All go through the unified trading/amm/* surface (camelCase body/query with connector +
    # chainNetwork). Meteora DAMM v2 positions are NFTs, so remove requires positionAddress and
    # add takes it optionally; fungible-LP AMMs ignore it. positions-owned is meteora-only.

    async def amm_pool_info(self, connector: str, chain_network: str, pool_address: str) -> Dict:
        """Get AMM pool information (reserves, price, base fee)."""
        return await self._request("GET", "trading/amm/pool-info", params={
            "connector": connector,
            "chainNetwork": chain_network,
            "poolAddress": pool_address,
        })

    async def amm_position_info(
        self, connector: str, chain_network: str, pool_address: str, wallet_address: str
    ) -> Dict:
        """Get a wallet's aggregate liquidity in an AMM pool plus a per-position breakdown (DAMM v2)."""
        return await self._request("GET", "trading/amm/position-info", params={
            "connector": connector,
            "chainNetwork": chain_network,
            "poolAddress": pool_address,
            "walletAddress": wallet_address,
        })

    async def amm_positions_owned(
        self, connector: str, chain_network: str, wallet_address: str
    ) -> List[Dict]:
        """List all of a wallet's AMM positions across pools (meteora only; fungible-LP → Gateway 400)."""
        return await self._request("GET", "trading/amm/positions-owned", params={
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
        })

    async def amm_quote_swap(
        self,
        connector: str,
        chain_network: str,
        pool_address: str,
        base_token: str,
        side: str,
        amount: float,
        slippage_pct: Optional[float] = None,
    ) -> Dict:
        """Quote a swap against a specific AMM pool."""
        params = {
            "connector": connector,
            "chainNetwork": chain_network,
            "poolAddress": pool_address,
            "baseToken": base_token,
            "side": side.upper(),
            "amount": amount,
        }
        if slippage_pct is not None:
            params["slippagePct"] = slippage_pct
        return await self._request("GET", "trading/amm/quote-swap", params=params)

    async def amm_execute_swap(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        pool_address: str,
        base_token: str,
        side: str,
        amount: float,
        slippage_pct: Optional[float] = None,
    ) -> Dict:
        """Execute a swap against a specific AMM pool."""
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "poolAddress": pool_address,
            "baseToken": base_token,
            "side": side.upper(),
            "amount": amount,
        }
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct
        return await self._request("POST", "trading/amm/execute-swap", json=payload)

    async def amm_quote_liquidity(
        self,
        connector: str,
        chain_network: str,
        pool_address: str,
        base_token_amount: float,
        quote_token_amount: float,
        slippage_pct: Optional[float] = None,
    ) -> Dict:
        """Quote a two-sided liquidity deposit."""
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "poolAddress": pool_address,
            "baseTokenAmount": base_token_amount,
            "quoteTokenAmount": quote_token_amount,
        }
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct
        return await self._request("GET", "trading/amm/quote-liquidity", params=payload)

    async def amm_add_liquidity(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        pool_address: str,
        base_token_amount: float,
        quote_token_amount: float,
        slippage_pct: Optional[float] = None,
        position_address: Optional[str] = None,
    ) -> Dict:
        """Add two-sided liquidity. For meteora, position_address adds to that NFT position (omit = new)."""
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "poolAddress": pool_address,
            "baseTokenAmount": base_token_amount,
            "quoteTokenAmount": quote_token_amount,
        }
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct
        if position_address is not None:
            payload["positionAddress"] = position_address
        return await self._request("POST", "trading/amm/add-liquidity", json=payload)

    async def amm_remove_liquidity(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        pool_address: str,
        percentage_to_remove: float,
        slippage_pct: Optional[float] = None,
        position_address: Optional[str] = None,
    ) -> Dict:
        """Remove liquidity. Gateway requires position_address for meteora (DAMM v2 NFT positions)."""
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "poolAddress": pool_address,
            "percentageToRemove": percentage_to_remove,
        }
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct
        if position_address is not None:
            payload["positionAddress"] = position_address
        return await self._request("POST", "trading/amm/remove-liquidity", json=payload)

    async def amm_create_pool(
        self,
        connector: str,
        chain_network: str,
        wallet_address: str,
        base_token: str,
        quote_token: str,
        base_token_amount: float,
        quote_token_amount: Optional[float] = None,
        initial_price: Optional[float] = None,
        slippage_pct: Optional[float] = None,
        extra_params: Optional[Dict] = None,
    ) -> Dict:
        """Create and seed a new AMM pool.

        slippage_pct is the seeding slippage (uniswap/pancakeswap only); omitted, the
        connector's configured slippagePct applies. extra_params carries the
        connector-specific create params under Gateway's own names (configAddress,
        ammConfigIndex) and is spread into the payload — the same contract as clmm
        open's extra_params. The router validates keys before this is called.
        """
        payload = {
            "connector": connector,
            "chainNetwork": chain_network,
            "walletAddress": wallet_address,
            "baseToken": base_token,
            "quoteToken": quote_token,
            "baseTokenAmount": base_token_amount,
        }
        # Seed price: at most one of quoteTokenAmount / initialPrice; Gateway falls back to market price.
        if quote_token_amount is not None:
            payload["quoteTokenAmount"] = quote_token_amount
        if initial_price is not None:
            payload["initialPrice"] = initial_price
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct
        if extra_params:
            payload.update(extra_params)
        return await self._request("POST", "trading/amm/create-pool", json=payload)

    # ============================================
    # Transaction Polling
    # ============================================

    async def poll_transaction(
        self,
        network_id: str,
        tx_hash: str,
    ) -> Optional[Dict]:
        """
        Poll transaction status on blockchain.

        Args:
            network_id: Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')
            tx_hash: Transaction hash/signature

        Returns:
            Transaction status dict with fields:
            - txStatus: 1 confirmed, 0 pending, -1 failed, -2 not found
              (-2 is terminal on Solana once the blockhash expires — treat it
              as dropped, not merely pending)
            - fee: Transaction fee amount
            - error: Parsed error message if transaction failed (e.g., "SLIPPAGE_EXCEEDED (0x1771): ...")
            - txData: Full transaction data including meta.err
            Returns None if Gateway is unavailable or request fails.
        """
        try:
            # Split network_id into chain and network
            parts = network_id.split('-', 1)
            if len(parts) != 2:
                logger.error(f"Invalid network_id format: {network_id}. Expected 'chain-network'")
                return None

            chain, network = parts

            payload = {
                "network": network,
                "signature": tx_hash
            }

            return await self._request("POST", f"chains/{chain}/poll", json=payload)
        except Exception as e:
            logger.error(f"Error polling transaction {tx_hash}: {e}")
            return None
