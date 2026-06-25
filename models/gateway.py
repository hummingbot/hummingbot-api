from typing import List, Optional

from pydantic import BaseModel, Field

# ============================================
# Container Management Models
# ============================================


class GatewayConfig(BaseModel):
    """Configuration for Gateway container deployment.

    The Gateway always runs secured (TLS + mTLS); there is intentionally no ``dev_mode`` and no
    ``passphrase`` field (SEC-048):
    - A Gateway that holds wallet keys must never be served over plain HTTP, so the API does not
      support a dev/insecure mode.
    - The Gateway (v2.x) uses a single ``GATEWAY_PASSPHRASE`` for *both* TLS cert-key decryption
      and wallet encryption, and the shared mTLS cert set must be decryptable by this API's
      clients (which use ``CONFIG_PASSWORD``). The passphrase is therefore always
      ``CONFIG_PASSWORD``; a separate value would only break the API<->Gateway mTLS chain.
    """
    image: str = Field(default="hummingbot/gateway:latest", description="Docker image for Gateway")
    port: int = Field(default=15888, description="Port for Gateway API")


class GatewayStatus(BaseModel):
    """Status information for Gateway instance"""
    running: bool = Field(description="Whether Gateway container is running")
    container_id: Optional[str] = Field(default=None, description="Container ID if running")
    image: Optional[str] = Field(default=None, description="Image used for the container")
    created_at: Optional[str] = Field(default=None, description="Container creation timestamp")
    port: Optional[int] = Field(default=None, description="Port Gateway is running on")


# ============================================
# Wallet Management Models
# ============================================

class GatewayWalletCredential(BaseModel):
    """Credentials for adding an existing wallet to Gateway"""
    chain: str = Field(description="Blockchain chain (e.g., 'solana', 'ethereum')")
    private_key: str = Field(description="Wallet private key")
    set_default: bool = Field(default=True, description="Set as default wallet for this chain")


class GatewayWalletInfo(BaseModel):
    """Information about a connected Gateway wallet"""
    chain: str = Field(description="Blockchain chain")
    address: str = Field(description="Wallet address")
    network: str = Field(description="Network the wallet is configured for")


class SetDefaultWalletRequest(BaseModel):
    """Request to set the default wallet for a chain"""
    chain: str = Field(description="Blockchain chain (e.g., 'solana', 'ethereum')")
    address: str = Field(description="Wallet address to set as default")


# ============================================
# Pool and Token Management Models
# ============================================

class AddPoolRequest(BaseModel):
    """Request to add a liquidity pool"""
    connector_name: str = Field(description="DEX connector name (e.g., 'raydium', 'meteora')")
    type: str = Field(description="Pool type ('clmm' or 'amm')")
    network: Optional[str] = Field(
        default=None,
        description="Network name (e.g., 'mainnet-beta') - optional for /networks/{network_id}/pools"
    )
    address: str = Field(description="Pool contract address")
    base: str = Field(description="Base token symbol")
    quote: str = Field(description="Quote token symbol")
    base_address: str = Field(description="Base token contract address")
    quote_address: str = Field(description="Quote token contract address")
    fee_pct: Optional[float] = Field(default=None, description="Pool fee percentage (e.g., 0.25)")


class AddTokenRequest(BaseModel):
    """Request to add a custom token to Gateway"""
    address: str = Field(description="Token contract address")
    symbol: str = Field(description="Token symbol")
    name: Optional[str] = Field(default=None, description="Token name (defaults to symbol)")
    decimals: int = Field(description="Number of decimals for the token")


# ============================================
# Balance Query Models
# ============================================

class GatewayBalanceRequest(BaseModel):
    """Request for Gateway wallet balances"""
    account_name: str = Field(description="Account name")
    chain: str = Field(description="Blockchain chain")
    tokens: Optional[List[str]] = Field(default=None, description="List of token symbols to query (optional)")


# ============================================
# API Keys Management Models
# ============================================

class UpdateApiKeysRequest(BaseModel):
    """Request to update Gateway API keys"""
    api_keys: dict = Field(
        description="Dict mapping provider name to API key value (e.g., {'helius': 'abc123', 'infura': 'xyz789'})"
    )
