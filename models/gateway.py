from pydantic import BaseModel, Field
from typing import Optional, List


# ============================================
# Container Management Models
# ============================================

class GatewayConfig(BaseModel):
    """Configuration for Gateway container deployment"""
    passphrase: str = Field(description="Gateway passphrase for configuration encryption")
    image: str = Field(default="hummingbot/gateway:latest", description="Docker image for Gateway")
    port: int = Field(default=15888, description="Port for Gateway API")
    dev_mode: bool = Field(default=True, description="Enable development mode")


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
    """Credentials for connecting a Gateway wallet"""
    chain: str = Field(description="Blockchain chain (e.g., 'solana', 'ethereum')")
    private_key: str = Field(description="Wallet private key")
    network: Optional[str] = Field(default=None, description="Network to use (defaults to chain's default)")


class GatewayWalletInfo(BaseModel):
    """Information about a connected Gateway wallet"""
    chain: str = Field(description="Blockchain chain")
    address: str = Field(description="Wallet address")
    network: str = Field(description="Network the wallet is configured for")


# ============================================
# Pool and Token Management Models
# ============================================

class AddPoolRequest(BaseModel):
    """Request to add a liquidity pool"""
    connector_name: str = Field(description="DEX connector name (e.g., 'raydium', 'meteora')")
    type: str = Field(description="Pool type ('clmm' for concentrated liquidity)")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')")
    base: str = Field(description="Base token symbol")
    quote: str = Field(description="Quote token symbol")
    address: str = Field(description="Pool contract address")


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
