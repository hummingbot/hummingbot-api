import logging
from typing import Dict, List, Optional
import aiohttp
from decimal import Decimal

logger = logging.getLogger(__name__)


class GatewayClient:
    """
    Simplified Gateway HTTP client for API integration.
    Provides essential functionality for wallet management and balance queries.
    """

    def __init__(self, base_url: str = "http://localhost:15888"):
        self.base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None

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
        return default_wallet

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close the aiohttp session"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, params: Dict = None, json: Dict = None) -> Optional[Dict]:
        """Make HTTP request to Gateway"""
        session = await self._get_session()
        url = f"{self.base_url}/{path}"

        try:
            if method == "GET":
                async with session.get(url, params=params) as response:
                    response.raise_for_status()
                    return await response.json()
            elif method == "POST":
                async with session.post(url, json=json) as response:
                    response.raise_for_status()
                    return await response.json()
            elif method == "DELETE":
                async with session.delete(url, params=params, json=json) as response:
                    response.raise_for_status()
                    return await response.json()
        except aiohttp.ClientResponseError as e:
            logger.warning(f"Gateway request failed with status {e.status}: {method} {url} - {e.message}")
            return None
        except aiohttp.ClientError as e:
            logger.debug(f"Gateway request error: {method} {url} - {e}")
            return None
        except Exception as e:
            logger.debug(f"Gateway request failed: {method} {url} - {e}")
            raise

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
        """Get default wallet address for a chain"""
        try:
            wallets = await self.get_wallets()
            for wallet in wallets:
                if wallet.get("chain") == chain:
                    addresses = wallet.get("walletAddresses", [])
                    return addresses[0] if addresses else None
            return None
        except Exception as e:
            logger.error(f"Error getting default wallet for chain {chain}: {e}")
            return None

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

    async def get_config(self, namespace: str) -> Dict:
        """Get configuration for a specific namespace (connector or chain-network)"""
        return await self._request("GET", "config", params={"namespace": namespace})

    async def update_config(self, namespace: str, path: str, value: any) -> Dict:
        """Update a configuration value for a namespace"""
        return await self._request("POST", "config/update", json={
            "namespace": namespace,
            "path": path,
            "value": value
        })

    async def get_pools(self, connector: str, network: str) -> List[Dict]:
        """Get pools for a connector and network"""
        return await self._request("GET", "pools", params={
            "connector": connector,
            "network": network
        })

    async def add_pool(self, connector: str, pool_type: str, network: str, base_symbol: str, quote_symbol: str, address: str) -> Dict:
        """Add a new pool"""
        return await self._request("POST", "pools", json={
            "connector": connector,
            "type": pool_type,
            "network": network,
            "baseSymbol": base_symbol,
            "quoteSymbol": quote_symbol,
            "address": address
        })

    async def pool_info(self, connector: str, network: str, pool_address: str) -> Dict:
        """Get detailed information about a specific pool"""
        return await self._request("POST", "clmm/liquidity/pool", json={
            "connector": connector,
            "network": network,
            "poolAddress": pool_address
        })

    # ============================================
    # Swap Operations
    # ============================================

    async def quote_swap(
        self,
        connector: str,
        network: str,
        base_asset: str,
        quote_asset: str,
        amount: float,
        side: str,
        slippage_pct: Optional[float] = None,
        pool_address: Optional[str] = None
    ) -> Dict:
        """Get a quote for a swap"""
        payload = {
            "network": network,
            "baseToken": base_asset,
            "quoteToken": quote_asset,
            "amount": str(amount),
            "side": side.upper()
        }
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct
        if pool_address:
            payload["poolAddress"] = pool_address

        return await self._request("GET", f"connectors/{connector}/router/quote-swap", params=payload)

    async def execute_swap(
        self,
        connector: str,
        network: str,
        wallet_address: str,
        base_asset: str,
        quote_asset: str,
        amount: float,
        side: str,
        slippage_pct: Optional[float] = None
    ) -> Dict:
        """Execute a swap"""
        payload = {
            "network": network,
            "walletAddress": wallet_address,
            "baseToken": base_asset,
            "quoteToken": quote_asset,
            "amount": str(amount),
            "side": side.upper()
        }
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct

        return await self._request("POST", f"connectors/{connector}/router/execute-swap", json=payload)

    async def execute_quote(
        self,
        connector: str,
        network: str,
        wallet_address: str,
        quote_id: str
    ) -> Dict:
        """Execute a previously obtained quote"""
        return await self._request("POST", f"connectors/{connector}/router/execute-quote", json={
            "network": network,
            "address": wallet_address,
            "quoteId": quote_id
        })

    # ============================================
    # Liquidity Operations - CLMM (Concentrated Liquidity)
    # ============================================

    async def clmm_open_position(
        self,
        connector: str,
        network: str,
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
            "network": network,
            "walletAddress": wallet_address,
            "poolAddress": pool_address,
            "lowerPrice": lower_price,
            "upperPrice": upper_price
        }
        if base_token_amount is not None:
            payload["baseTokenAmount"] = str(base_token_amount)
        if quote_token_amount is not None:
            payload["quoteTokenAmount"] = str(quote_token_amount)
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct

        # Add any connector-specific parameters
        if extra_params:
            payload.update(extra_params)

        return await self._request("POST", f"connectors/{connector}/clmm/open-position", json=payload)

    async def clmm_add_liquidity(
        self,
        connector: str,
        network: str,
        wallet_address: str,
        position_address: str,
        base_token_amount: Optional[float] = None,
        quote_token_amount: Optional[float] = None,
        slippage_pct: Optional[float] = None
    ) -> Dict:
        """Add more liquidity to an existing CLMM position"""
        payload = {
            "connector": connector,
            "network": network,
            "address": wallet_address,
            "positionAddress": position_address
        }
        if base_token_amount is not None:
            payload["baseTokenAmount"] = str(base_token_amount)
        if quote_token_amount is not None:
            payload["quoteTokenAmount"] = str(quote_token_amount)
        if slippage_pct is not None:
            payload["slippagePct"] = slippage_pct

        return await self._request("POST", "clmm/liquidity/add", json=payload)

    async def clmm_close_position(
        self,
        connector: str,
        network: str,
        wallet_address: str,
        position_address: str
    ) -> Dict:
        """Close a CLMM position completely"""
        return await self._request("POST", f"connectors/{connector}/clmm/close-position", json={
            "network": network,
            "walletAddress": wallet_address,
            "positionAddress": position_address
        })

    async def clmm_remove_liquidity(
        self,
        connector: str,
        network: str,
        wallet_address: str,
        position_address: str,
        percentage: float
    ) -> Dict:
        """Remove liquidity from a CLMM position (partial)"""
        return await self._request("POST", "clmm/liquidity/remove", json={
            "connector": connector,
            "network": network,
            "address": wallet_address,
            "positionAddress": position_address,
            "percentage": percentage
        })

    async def clmm_position_info(
        self,
        connector: str,
        network: str,
        wallet_address: str,
        position_address: str
    ) -> Dict:
        """Get CLMM position information including pending fees"""
        params = {
            "network": network,
            "walletAddress": wallet_address,
            "positionAddress": position_address
        }
        return await self._request("GET", f"connectors/{connector}/clmm/position-info", params=params)

    async def clmm_positions_owned(
        self,
        connector: str,
        network: str,
        wallet_address: str,
        pool_address: str
    ) -> Dict:
        """Get all CLMM positions owned by wallet for a specific pool"""
        params = {
            "network": network,
            "walletAddress": wallet_address,
            "poolAddress": pool_address
        }

        return await self._request("GET", f"connectors/{connector}/clmm/positions-owned", params=params)

    async def clmm_collect_fees(
        self,
        connector: str,
        network: str,
        wallet_address: str,
        position_address: str
    ) -> Dict:
        """Collect accumulated fees from a CLMM position"""
        return await self._request("POST", f"connectors/{connector}/clmm/collect-fees", json={
            "network": network,
            "address": wallet_address,
            "positionAddress": position_address
        })

    async def clmm_pool_info(
        self,
        connector: str,
        network: str,
        pool_address: str
    ) -> Dict:
        """Get detailed CLMM pool information by pool address"""
        return await self._request("GET", f"connectors/{connector}/clmm/pool-info", params={
            "network": network,
            "poolAddress": pool_address
        })

    # ============================================
    # Transaction Polling
    # ============================================

    async def poll_transaction(
        self,
        network_id: str,
        tx_hash: str,
        wallet_address: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Poll transaction status on blockchain.

        Args:
            network_id: Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')
            tx_hash: Transaction hash/signature
            wallet_address: Optional wallet address for verification

        Returns:
            Transaction status dict with fields:
            - txStatus: 1 for confirmed, 0 for failed/pending
            - fee: Transaction fee amount
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
            if wallet_address:
                payload["walletAddress"] = wallet_address

            return await self._request("POST", f"chains/{chain}/poll", json=payload)
        except Exception as e:
            logger.error(f"Error polling transaction {tx_hash}: {e}")
            return None

