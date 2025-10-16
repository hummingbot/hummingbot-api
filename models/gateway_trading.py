"""
Models for Gateway DEX trading operations.
Supports swaps via routers (Jupiter, 0x) and CLMM liquidity positions (Meteora, Raydium, Uniswap V3).

Note: AMM support has been removed. Use Router for simple swaps, CLMM for liquidity provision.
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from decimal import Decimal


# ============================================
# Swap Models (Router: Jupiter, 0x)
# ============================================

class SwapQuoteRequest(BaseModel):
    """Request for swap price quote"""
    connector: str = Field(description="DEX router connector (e.g., 'jupiter', '0x')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta', 'ethereum-mainnet')")
    trading_pair: str = Field(description="Trading pair in BASE-QUOTE format (e.g., 'SOL-USDC')")
    side: str = Field(description="Trade side: 'BUY' or 'SELL'")
    amount: Decimal = Field(description="Amount to swap (in base token for SELL, quote token for BUY)")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")


class SwapQuoteResponse(BaseModel):
    """Response with swap quote details"""
    base: str = Field(description="Base token symbol")
    quote: str = Field(description="Quote token symbol")
    price: Decimal = Field(description="Quoted price (base/quote)")
    amount: Decimal = Field(description="Amount specified in request (BUY: base amount to receive, SELL: base amount to sell)")
    amount_in: Optional[Decimal] = Field(default=None, description="Actual input amount (BUY: quote to spend, SELL: base to sell)")
    amount_out: Optional[Decimal] = Field(default=None, description="Actual output amount (BUY: base to receive, SELL: quote to receive)")
    expected_amount: Optional[Decimal] = Field(default=None, description="Deprecated: use amount_out instead")
    slippage_pct: Decimal = Field(description="Applied slippage percentage")
    gas_estimate: Optional[Decimal] = Field(default=None, description="Estimated gas cost")


class SwapExecuteRequest(BaseModel):
    """Request to execute a swap"""
    connector: str = Field(description="DEX router connector (e.g., 'jupiter', '0x')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    trading_pair: str = Field(description="Trading pair (e.g., 'SOL-USDC')")
    side: str = Field(description="Trade side: 'BUY' or 'SELL'")
    amount: Decimal = Field(description="Amount to swap")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class SwapExecuteResponse(BaseModel):
    """Response after executing swap"""
    transaction_hash: str = Field(description="Transaction hash")
    trading_pair: str = Field(description="Trading pair")
    side: str = Field(description="Trade side")
    amount: Decimal = Field(description="Amount swapped")
    status: str = Field(default="submitted", description="Transaction status")


# ============================================
# CLMM Liquidity Models (Meteora, Raydium, Uniswap V3)
# ============================================

class CLMMOpenPositionRequest(BaseModel):
    """Request to open a new CLMM position with initial liquidity"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    trading_pair: str = Field(description="Trading pair (e.g., 'SOL-USDC')")

    # Position range - either absolute prices or center + widths
    lower_price: Optional[Decimal] = Field(default=None, description="Lower price for position range")
    upper_price: Optional[Decimal] = Field(default=None, description="Upper price for position range")
    price: Optional[Decimal] = Field(default=None, description="Center price (alternative to absolute prices)")
    lower_width_pct: Optional[Decimal] = Field(default=None, description="Lower range width % from center")
    upper_width_pct: Optional[Decimal] = Field(default=None, description="Upper range width % from center")

    # Initial liquidity
    base_token_amount: Optional[Decimal] = Field(default=None, description="Amount of base token to add")
    quote_token_amount: Optional[Decimal] = Field(default=None, description="Amount of quote token to add")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMOpenPositionResponse(BaseModel):
    """Response after opening a new CLMM position"""
    transaction_hash: str = Field(description="Transaction hash")
    position_address: str = Field(description="Address of the newly created position")
    trading_pair: str = Field(description="Trading pair")
    pool_address: str = Field(description="Pool address")
    lower_price: Decimal = Field(description="Lower price bound")
    upper_price: Decimal = Field(description="Upper price bound")
    status: str = Field(default="submitted", description="Transaction status")


class CLMMAddLiquidityRequest(BaseModel):
    """Request to add MORE liquidity to an EXISTING CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Existing position address to add liquidity to")
    base_token_amount: Optional[Decimal] = Field(default=None, description="Amount of base token to add")
    quote_token_amount: Optional[Decimal] = Field(default=None, description="Amount of quote token to add")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMRemoveLiquidityRequest(BaseModel):
    """Request to remove SOME liquidity from a CLMM position (partial removal)"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to remove liquidity from")
    percentage: Decimal = Field(description="Percentage of liquidity to remove (0-100)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMClosePositionRequest(BaseModel):
    """Request to CLOSE a CLMM position completely (removes all liquidity and closes position)"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to close")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMCollectFeesRequest(BaseModel):
    """Request to collect fees from a CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to collect fees from")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMCollectFeesResponse(BaseModel):
    """Response after collecting fees"""
    transaction_hash: str = Field(description="Transaction hash")
    position_address: str = Field(description="Position address")
    base_fee_collected: Optional[Decimal] = Field(default=None, description="Base token fees collected")
    quote_fee_collected: Optional[Decimal] = Field(default=None, description="Quote token fees collected")
    status: str = Field(default="submitted", description="Transaction status")


class CLMMPositionsOwnedRequest(BaseModel):
    """Request to get all CLMM positions owned by a wallet"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMPositionInfo(BaseModel):
    """Information about a CLMM liquidity position"""
    position_address: str = Field(description="Position address")
    pool_address: str = Field(description="Pool address")
    trading_pair: str = Field(description="Trading pair")
    base_token: str = Field(description="Base token symbol")
    quote_token: str = Field(description="Quote token symbol")
    base_token_amount: Decimal = Field(description="Base token amount in position")
    quote_token_amount: Decimal = Field(description="Quote token amount in position")
    current_price: Decimal = Field(description="Current pool price")
    lower_price: Decimal = Field(description="Lower price bound")
    upper_price: Decimal = Field(description="Upper price bound")
    base_fee_amount: Optional[Decimal] = Field(default=None, description="Base token uncollected fees")
    quote_fee_amount: Optional[Decimal] = Field(default=None, description="Quote token uncollected fees")
    lower_bin_id: Optional[int] = Field(default=None, description="Lower bin ID (Meteora)")
    upper_bin_id: Optional[int] = Field(default=None, description="Upper bin ID (Meteora)")
    in_range: bool = Field(description="Whether position is currently in range")


class CLMMGetPositionInfoRequest(BaseModel):
    """Request to get detailed info about a specific CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to query")


# ============================================
# Pool Information Models
# ============================================

class GetPoolInfoRequest(BaseModel):
    """Request to get pool information"""
    connector: str = Field(description="DEX connector (e.g., 'meteora', 'raydium', 'jupiter')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    trading_pair: str = Field(description="Trading pair (e.g., 'SOL-USDC')")


class PoolInfo(BaseModel):
    """Information about a liquidity pool"""
    type: str = Field(description="Pool type: 'clmm' or 'router'")
    address: str = Field(description="Pool address")
    trading_pair: str = Field(description="Trading pair")
    base_token: str = Field(description="Base token symbol")
    quote_token: str = Field(description="Quote token symbol")
    current_price: Decimal = Field(description="Current pool price")
    base_token_amount: Decimal = Field(description="Base token liquidity in pool")
    quote_token_amount: Decimal = Field(description="Quote token liquidity in pool")
    fee_pct: Decimal = Field(description="Pool fee percentage")

    # CLMM-specific
    bin_step: Optional[int] = Field(default=None, description="Bin step (CLMM)")
    active_bin_id: Optional[int] = Field(default=None, description="Active bin ID (CLMM)")
