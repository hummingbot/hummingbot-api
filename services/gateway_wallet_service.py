import asyncio
import logging
from decimal import Decimal
from typing import Dict, List, Optional

from fastapi import HTTPException

from services.gateway_client import GatewayClient
from services.gecko_price_source import GeckoPriceSource

# Create module-specific logger
logger = logging.getLogger(__name__)


def balance_entry(token: str, units: Decimal, price: Optional[Decimal],
                  available_units: Optional[Decimal] = None) -> Dict:
    """Build the standard token balance entry dict shared across balance endpoints.

    Args:
        token: Token symbol
        units: Token balance
        price: Token price (None means unknown -> price/value reported as 0.0)
        available_units: Available balance (defaults to units when not provided)
    """
    if available_units is None:
        available_units = units
    return {
        "token": token,
        "units": float(units),
        "price": float(price) if price is not None else 0.0,
        "value": float(price * units) if price is not None else 0.0,
        "available_units": float(available_units),
    }


class GatewayWalletService:
    """
    Gateway wallet management: wallet CRUD plus balance and price retrieval through the Gateway service.
    Gateway manages its own encrypted wallets; this service only talks to it over HTTP via GatewayClient.
    """

    def __init__(self, gateway_client: GatewayClient, market_data_service=None):
        """
        Initialize the GatewayWalletService.

        Args:
            gateway_client: Client used for all Gateway HTTP interactions.
            market_data_service: MarketDataService used to publish on-chain DEX prices into the
                shared price pool so portfolio quoting can resolve blockchain token values.
        """
        self.gateway_client = gateway_client
        self._market_data_service = market_data_service
        # Batched DEX price lookups via GeckoTerminal, tried before per-token Gateway quotes.
        self._gecko_source = GeckoPriceSource(gateway_client)

    async def close(self) -> None:
        """Release resources held by this service (the GeckoTerminal HTTP client)."""
        await self._gecko_source.close()

    async def _require_gateway(self) -> None:
        """Raise a 503 HTTPException if the Gateway service is not reachable."""
        if not await self.gateway_client.ping():
            raise HTTPException(status_code=503, detail="Gateway service is not available")

    async def get_gateway_wallets(self) -> List[Dict]:
        """
        Get all wallets from Gateway. Gateway manages its own encrypted wallets.

        Returns:
            List of wallet information from Gateway, with default_address included for each chain
        """
        await self._require_gateway()

        try:
            wallets = await self.gateway_client.get_wallets()

            # Enrich with default wallet info for each chain
            for wallet_group in wallets:
                chain = wallet_group.get("chain")
                if chain:
                    default_wallet = await self.gateway_client.get_default_wallet_address(chain)
                    wallet_group["default_address"] = default_wallet or ""

            return wallets
        except Exception as e:
            logger.error(f"Error getting Gateway wallets: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get wallets: {str(e)}")

    async def add_gateway_wallet(self, chain: str, private_key: str, set_default: bool = True) -> Dict:
        """
        Add a wallet to Gateway. Gateway handles encryption internally.

        Args:
            chain: Blockchain chain (e.g., 'solana', 'ethereum')
            private_key: Wallet private key
            set_default: Set as default wallet for this chain (default: True)

        Returns:
            Dictionary with wallet information from Gateway
        """
        await self._require_gateway()

        try:
            result = await self.gateway_client.add_wallet(chain, private_key, set_default=set_default)

            if "error" in result:
                raise HTTPException(status_code=400, detail=f"Gateway error: {result['error']}")

            logger.info(f"Added {chain} wallet {result.get('address')} to Gateway")
            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding Gateway wallet: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to add wallet: {str(e)}")

    async def remove_gateway_wallet(self, chain: str, address: str) -> Dict:
        """
        Remove a wallet from Gateway.

        Args:
            chain: Blockchain chain
            address: Wallet address to remove

        Returns:
            Success message
        """
        await self._require_gateway()

        try:
            result = await self.gateway_client.remove_wallet(chain, address)

            if "error" in result:
                raise HTTPException(status_code=400, detail=f"Gateway error: {result['error']}")

            logger.info(f"Removed {chain} wallet {address} from Gateway")
            return {"success": True, "message": f"Successfully removed {chain} wallet"}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error removing Gateway wallet: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to remove wallet: {str(e)}")

    async def get_gateway_balances(self, chain: str, address: str, network: Optional[str] = None,
                                   tokens: Optional[List[str]] = None) -> List[Dict]:
        """
        Get Gateway wallet balances with pricing from rate sources.

        Args:
            chain: Blockchain chain
            address: Wallet address
            network: Optional network name (if not provided, uses default network for chain)
            tokens: Optional list of token symbols to query

        Returns:
            List of token balance dictionaries with prices from rate sources
        """
        await self._require_gateway()

        try:
            # Get default network for chain if not provided
            if not network:
                network = await self.gateway_client.get_default_network(chain)
            if not network:
                raise HTTPException(status_code=400, detail=f"Could not determine network for chain '{chain}'")

            # Get balances from Gateway
            balances_response = await self.gateway_client.get_balances(chain, network, address, tokens=tokens)

            if "error" in balances_response:
                raise HTTPException(status_code=400, detail=f"Gateway error: {balances_response['error']}")

            # Format balances list
            balances = balances_response.get("balances", {})
            balances_list = []

            for token, balance in balances.items():
                if balance and float(balance) > 0:
                    balances_list.append({
                        "token": token,
                        "units": Decimal(str(balance))
                    })

            # Get prices for tokens
            unique_tokens = [b["token"] for b in balances_list]
            all_prices = {}

            # Fetch prices for Gateway tokens
            if unique_tokens:
                try:
                    fetched_prices = await self._fetch_gateway_prices_immediate(
                        chain, network, unique_tokens
                    )
                    for token, price in fetched_prices.items():
                        if price > 0:
                            all_prices[token] = price
                except Exception as e:
                    logger.warning(f"Error fetching gateway prices: {e}")

            # Format final result with prices
            formatted_balances = []
            for balance in balances_list:
                token = balance["token"]
                if "USD" in token:
                    price = Decimal("1")
                else:
                    # all_prices is now keyed by token name directly
                    price = Decimal(str(all_prices.get(token, 0)))

                formatted_balances.append(balance_entry(token, balance["units"], price))

            return formatted_balances

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting Gateway balances: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to get balances: {str(e)}")

    async def _fetch_gateway_prices_immediate(self, chain: str, network: str,
                                              tokens: List[str]) -> Dict[str, Decimal]:
        """
        Fetch prices immediately from Gateway for the given tokens.
        This is used to get prices right away instead of waiting for the background update task.

        Args:
            chain: Blockchain chain (e.g., 'solana', 'ethereum')
            network: Network name (e.g., 'mainnet-beta', 'mainnet')
            tokens: List of token symbols to get prices for

        Returns:
            Dictionary mapping token symbol to price in USDC
        """
        from hummingbot.core.data_type.common import TradeType
        from hummingbot.core.gateway.gateway_http_client import GatewayHttpClient

        gateway_client = GatewayHttpClient.get_instance()
        prices = {}

        def publish_price(trading_pair: str, price: Decimal):
            """Push an on-chain price into the shared pool so quoting can resolve it later."""
            if self._market_data_service is not None:
                self._market_data_service.set_price(trading_pair, price)

        # Construct full network name (e.g., "solana-mainnet-beta")
        full_network = f"{chain}-{network}"

        # Create tasks for all tokens in parallel
        tasks = []
        task_tokens = []
        quote_asset = "USDC"

        # On ethereum networks, use WETH price for ETH to avoid duplicate calls
        eth_needs_weth_price = False
        if chain == "ethereum":
            has_eth = any(t.upper() == "ETH" for t in tokens)
            has_weth = any(t.upper() == "WETH" for t in tokens)
            if has_eth and not has_weth:
                # Replace ETH with WETH for fetching
                tokens = [t if t.upper() != "ETH" else "WETH" for t in tokens]
                eth_needs_weth_price = True
                logger.debug("Replacing ETH with WETH for price fetch on ethereum")
            elif has_eth and has_weth:
                # Remove ETH, will copy WETH price later
                tokens = [t for t in tokens if t.upper() != "ETH"]
                eth_needs_weth_price = True
                logger.debug("Removing duplicate ETH, will use WETH price on ethereum")

        # GeckoTerminal first: batch-price the tokens by contract address in a single call set.
        # Whatever it covers is published here and skipped in the per-token Gateway loop below;
        # anything it misses (unknown token, no pool, failure) falls back to Gateway pricing.
        try:
            gecko_prices = await self._gecko_source.fetch_prices(chain, network, tokens)
            for token, price in gecko_prices.items():
                prices[token] = price
                publish_price(f"{token}-{quote_asset}", price)
                logger.debug(f"Fetched GeckoTerminal price for {token}: {price} {quote_asset}")
        except Exception as e:
            logger.warning(f"GeckoTerminal pricing failed for {full_network}: {e}")

        for token in tokens:
            token_upper = token.upper()

            # Skip same-token quotes (e.g., USDC/USDC) - price is always 1
            if token_upper == quote_asset.upper():
                prices[token] = Decimal("1")
                publish_price(f"{token}-{quote_asset}", Decimal("1"))
                logger.debug(f"Skipping same-token quote for {token}, price=1")
                continue

            # Already priced by GeckoTerminal above - no Gateway quote needed.
            if token in prices:
                continue

            try:
                # get_price will auto-fetch dex/trading_type from network's swap provider
                task = gateway_client.get_price(
                    network=full_network,
                    base_asset=token,
                    quote_asset=quote_asset,
                    amount=Decimal("1"),
                    side=TradeType.SELL
                )
                tasks.append(task)
                task_tokens.append(token)
            except Exception as e:
                logger.warning(f"Error preparing price request for {token}: {e}")
                continue

        if tasks:
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for token, result in zip(task_tokens, results):
                    if isinstance(result, Exception):
                        logger.warning(f"Error fetching price for {token}: {result}")
                    elif result and "price" in result:
                        price = Decimal(str(result["price"]))
                        prices[token] = price
                        # Also publish to the shared pool so future lookups can find it
                        trading_pair = f"{token}-USDC"
                        publish_price(trading_pair, price)
                        logger.debug(f"Fetched immediate price for {token}: {price} USDC")
            except Exception as e:
                logger.error(f"Error fetching gateway prices: {e}", exc_info=True)

        # Copy WETH price to ETH on ethereum networks
        if eth_needs_weth_price and "WETH" in prices:
            prices["ETH"] = prices["WETH"]
            publish_price("ETH-USDC", prices["WETH"])
            logger.debug(f"Copied WETH price to ETH: {prices['WETH']} USDC")

        return prices
