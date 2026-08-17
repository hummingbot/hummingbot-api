"""
Models for Gateway DEX trading operations.
Supports swaps via routers (Jupiter, 0x) and CLMM liquidity positions (Meteora, Raydium, Uniswap V3).

Note: AMM support has been removed. Use Router for simple swaps, CLMM for liquidity provision.
"""
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

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
    amount_in: Optional[Decimal] = Field(
        default=None, description="Actual input amount (BUY: quote to spend, SELL: base to sell)"
    )
    amount_out: Optional[Decimal] = Field(
        default=None, description="Actual output amount (BUY: base to receive, SELL: quote to receive)"
    )
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
    pool_address: str = Field(description="Pool contract address")

    # Position range
    lower_price: Decimal = Field(description="Lower price for position range")
    upper_price: Decimal = Field(description="Upper price for position range")

    # Initial liquidity
    base_token_amount: Optional[Decimal] = Field(default=None, description="Amount of base token to add")
    quote_token_amount: Optional[Decimal] = Field(default=None, description="Amount of quote token to add")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")

    # Connector-specific parameters (e.g., strategyType for Meteora)
    extra_params: Optional[Dict[str, Any]] = Field(default=None, description="Additional connector-specific parameters")


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
    pool_address: Optional[str] = Field(
        default=None,
        description="Pool the position belongs to. Only needed for positions this API never recorded "
                    "(e.g. opened by an lp_executor straight against Gateway); otherwise read from the database"
    )
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMCollectFeesRequest(BaseModel):
    """Request to collect fees from a CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to collect fees from")
    pool_address: Optional[str] = Field(
        default=None,
        description="Pool the position belongs to. Only needed for positions this API never recorded "
                    "(e.g. opened by an lp_executor straight against Gateway); otherwise read from the database"
    )
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMCollectFeesResponse(BaseModel):
    """Response after collecting fees"""
    transaction_hash: str = Field(description="Transaction hash")
    position_address: str = Field(description="Position address")
    base_fee_collected: Optional[Decimal] = Field(default=None, description="Base token fees collected")
    quote_fee_collected: Optional[Decimal] = Field(default=None, description="Quote token fees collected")
    status: str = Field(default="submitted", description="Transaction status")


class CLMMPositionsOwnedRequest(BaseModel):
    """Request to get all CLMM positions owned by a wallet for a specific pool"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address to filter positions")
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


class CLMMPoolInfoRequest(BaseModel):
    """Request to get CLMM pool information by pool address"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")


class CLMMPoolBin(BaseModel):
    """Individual bin in a CLMM pool (e.g., Meteora)"""
    bin_id: int = Field(alias="binId", description="Bin identifier")
    price: Decimal = Field(description="Price at this bin")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Base token amount in bin")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Quote token amount in bin")

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "bin_id": -374,
                "price": 0.47366592950616504,
                "base_token_amount": 19656.740028,
                "quote_token_amount": 18197.718539
            }
        }
    }


class CLMMPoolInfoResponse(BaseModel):
    """Response with detailed CLMM pool information"""
    address: str = Field(description="Pool address")
    base_token_address: str = Field(alias="baseTokenAddress", description="Base token contract address")
    quote_token_address: str = Field(alias="quoteTokenAddress", description="Quote token contract address")
    bin_step: Optional[int] = Field(None, alias="binStep", description="Bin step (Meteora DLMM only)")
    fee_pct: Decimal = Field(alias="feePct", description="Pool fee percentage")
    price: Decimal = Field(description="Current pool price")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Total base token liquidity")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Total quote token liquidity")
    active_bin_id: Optional[int] = Field(None, alias="activeBinId", description="Currently active bin ID (Meteora DLMM only)")
    dynamic_fee_pct: Optional[Decimal] = Field(None, alias="dynamicFeePct", description="Dynamic fee percentage")
    min_bin_id: Optional[int] = Field(None, alias="minBinId", description="Minimum bin ID (Meteora-specific)")
    max_bin_id: Optional[int] = Field(None, alias="maxBinId", description="Maximum bin ID (Meteora-specific)")
    bins: List[CLMMPoolBin] = Field(default_factory=list, description="List of bins with liquidity")

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "address": "5hbf9JP8k5zdrZp9pokPypFQoBse5mGCmW6nqodurGcd",
                "base_token_address": "METvsvVRapdj9cFLzq4Tr43xK4tAjQfwX76z3n6mWQL",
                "quote_token_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "bin_step": 20,
                "fee_pct": 0.2,
                "price": 0.47366592950616504,
                "base_token_amount": 8645709.142366,
                "quote_token_amount": 1095942.335132,
                "active_bin_id": -374,
                "dynamic_fee_pct": 0.2,
                "min_bin_id": -21835,
                "max_bin_id": 21835,
                "bins": []
            }
        }
    }


# ============================================
# AMM Liquidity Models (Meteora DAMM v2, Raydium CPMM, Uniswap/Pancakeswap V2)
# ============================================
# Re-added deliberately as a separate surface from CLMM. Unlike classic fungible-LP AMMs,
# Meteora DAMM v2 positions are NFTs (a wallet may hold several per pool), so the AMM routes
# are position-addressed: remove requires position_address (meteora), add takes it optionally
# (omit = new position), position-info returns a positions[] breakdown, and positions-owned
# lists all of a wallet's positions. Fungible-LP AMMs ignore position_address.

class AMMPoolInfoResponse(BaseModel):
    """Response with AMM pool information (constant-product / DAMM v2)."""
    address: str = Field(description="Pool address")
    base_token_address: str = Field(alias="baseTokenAddress", description="Base token contract address")
    quote_token_address: str = Field(alias="quoteTokenAddress", description="Quote token contract address")
    fee_pct: Decimal = Field(alias="feePct", description="Pool base fee percentage")
    price: Decimal = Field(description="Current pool price (quote per base)")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Total base token liquidity")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Total quote token liquidity")

    model_config = {"populate_by_name": True}


class AMMPositionDetail(BaseModel):
    """Per-position breakdown entry (one NFT position). Non-fungible-LP AMMs only."""
    position_address: str = Field(alias="positionAddress", description="Individual position (NFT) address")
    lp_token_amount: Decimal = Field(alias="lpTokenAmount", description="Liquidity held by this position (LP units)")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Base token amount in this position")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Quote token amount in this position")

    model_config = {"populate_by_name": True}


class AMMPositionInfoResponse(BaseModel):
    """Wallet's aggregate liquidity in an AMM pool, plus a per-position breakdown (DAMM v2)."""
    pool_address: str = Field(alias="poolAddress", description="Pool address")
    wallet_address: str = Field(alias="walletAddress", description="Wallet address")
    base_token_address: str = Field(alias="baseTokenAddress", description="Base token contract address")
    quote_token_address: str = Field(alias="quoteTokenAddress", description="Quote token contract address")
    lp_token_amount: Decimal = Field(alias="lpTokenAmount", description="Aggregate LP units across positions")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Aggregate base token amount")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Aggregate quote token amount")
    price: Decimal = Field(description="Current pool price (quote per base)")
    # Per-position breakdown; populated by Meteora DAMM v2, omitted by fungible-LP AMMs.
    positions: Optional[List[AMMPositionDetail]] = Field(default=None, description="Per-NFT position breakdown")

    model_config = {"populate_by_name": True}


class AMMQuoteSwapRequest(BaseModel):
    """Request to quote a swap against a specific AMM pool."""
    connector: str = Field(description="AMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")
    base_token: str = Field(description="Token that defines the swap direction (symbol or address)")
    side: str = Field(description="Trade direction: BUY or SELL")
    amount: Decimal = Field(description="Amount to swap (of base for SELL, of base to receive for BUY)")
    slippage_pct: Optional[Decimal] = Field(default=None, description="Maximum slippage percentage")


class AMMQuoteSwapResponse(BaseModel):
    """Response with an AMM swap quote."""
    pool_address: str = Field(alias="poolAddress", description="Pool address")
    token_in: str = Field(alias="tokenIn", description="Input token address")
    token_out: str = Field(alias="tokenOut", description="Output token address")
    amount_in: Decimal = Field(alias="amountIn", description="Input amount")
    amount_out: Decimal = Field(alias="amountOut", description="Output amount")
    price: Decimal = Field(description="Execution price")
    min_amount_out: Decimal = Field(alias="minAmountOut", description="Minimum output after slippage")
    max_amount_in: Decimal = Field(alias="maxAmountIn", description="Maximum input after slippage")
    price_impact_pct: Decimal = Field(alias="priceImpactPct", description="Price impact percentage")
    slippage_pct: Optional[Decimal] = Field(default=None, alias="slippagePct", description="Slippage percentage used")

    model_config = {"populate_by_name": True}


class AMMExecuteSwapRequest(BaseModel):
    """Request to execute a swap against a specific AMM pool."""
    connector: str = Field(description="AMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")
    base_token: str = Field(description="Token that defines the swap direction (symbol or address)")
    side: str = Field(description="Trade direction: BUY or SELL")
    amount: Decimal = Field(description="Amount to swap")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default)")


class AMMTransactionResponse(BaseModel):
    """Chain-neutral write response. `signature` holds the tx signature (Solana) or tx hash (EVM)."""
    signature: str = Field(description="Transaction signature (Solana) or transaction hash (EVM)")
    status: int = Field(description="TransactionStatus enum value from Gateway")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Connector-specific confirmed-tx details")

    model_config = {"populate_by_name": True}


class AMMQuoteLiquidityRequest(BaseModel):
    """Request to quote a two-sided liquidity deposit."""
    connector: str = Field(description="AMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")
    base_token_amount: Decimal = Field(description="Amount of base token to deposit")
    quote_token_amount: Decimal = Field(description="Amount of quote token to deposit")
    slippage_pct: Optional[Decimal] = Field(default=None, description="Maximum slippage percentage")


class AMMQuoteLiquidityResponse(BaseModel):
    """Response with a two-sided deposit quote."""
    base_limited: bool = Field(alias="baseLimited", description="Whether the base side is the limiting side")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Base token amount to deposit")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Quote token amount to deposit")
    base_token_amount_max: Decimal = Field(alias="baseTokenAmountMax", description="Max base token amount")
    quote_token_amount_max: Decimal = Field(alias="quoteTokenAmountMax", description="Max quote token amount")

    model_config = {"populate_by_name": True}


class AMMAddLiquidityRequest(BaseModel):
    """Request to add two-sided liquidity to an AMM pool."""
    connector: str = Field(description="AMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")
    base_token_amount: Decimal = Field(description="Amount of base token to add")
    quote_token_amount: Decimal = Field(description="Amount of quote token to add")
    slippage_pct: Optional[Decimal] = Field(default=1.0, description="Maximum slippage percentage (default: 1.0)")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default)")
    # Meteora DAMM v2: add to this specific NFT position; omit to open a NEW position. Ignored by fungible-LP AMMs.
    position_address: Optional[str] = Field(default=None, description="Meteora position to add to (omit = new position)")


class AMMRemoveLiquidityRequest(BaseModel):
    """Request to remove liquidity from an AMM pool."""
    connector: str = Field(description="AMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")
    percentage_to_remove: Decimal = Field(description="Percentage of liquidity to remove (0-100)")
    slippage_pct: Optional[Decimal] = Field(default=None, description="Maximum slippage percentage")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default)")
    # Required for meteora (DAMM v2 positions are NFTs). Ignored by fungible-LP AMMs.
    position_address: Optional[str] = Field(default=None, description="Meteora position to remove from (required for meteora)")


class AMMCreatePoolRequest(BaseModel):
    """Request to create and seed a new AMM pool."""
    connector: str = Field(description="AMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    base_token: str = Field(description="Base token symbol or address (becomes the pool base)")
    quote_token: str = Field(description="Quote token symbol or address (becomes the pool quote)")
    base_token_amount: Decimal = Field(description="Amount of base token to seed the pool with")
    quote_token_amount: Optional[Decimal] = Field(default=None, description="Amount of quote to seed (sets price if given)")
    initial_price: Optional[Decimal] = Field(default=None, description="Initial price (quote per base); overrides quote amount")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default)")
    # Connector-specific create-pool extras (only consumed by their owning connector):
    config_address: Optional[str] = Field(default=None, description="Meteora DAMM v2 config account (required for meteora)")
    fee_config_index: Optional[int] = Field(default=None, description="Raydium CPMM fee config index (optional)")
    gas_price: Optional[Decimal] = Field(default=None, description="Uniswap (EVM) gas price in gwei (optional)")
    max_gas: Optional[int] = Field(default=None, description="Uniswap (EVM) max gas limit (optional)")


class AMMCreatePoolResponse(BaseModel):
    """Response after creating an AMM pool."""
    signature: str = Field(description="Transaction signature (Solana) or transaction hash (EVM)")
    status: int = Field(description="TransactionStatus enum value from Gateway")
    pool_address: str = Field(alias="poolAddress", description="Address of the newly created pool")
    price: Optional[Decimal] = Field(default=None, description="Initial price the pool was seeded at (quote per base)")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Connector-specific confirmed-tx details")

    model_config = {"populate_by_name": True}


class AMMPositionsOwnedRequest(BaseModel):
    """Request to list all of a wallet's AMM positions across pools (Meteora only)."""
    connector: str = Field(description="AMM connector (meteora only; fungible-LP AMMs rejected)")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default)")


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


# ============================================
# CLMM Pool Listing Models
# ============================================

class TimeBasedMetrics(BaseModel):
    """Time-based metrics (volume, fees, fee-to-TVL ratio) for different time periods"""
    min_30: Optional[Decimal] = Field(default=None, description="30 minute metric")
    hour_1: Optional[Decimal] = Field(default=None, description="1 hour metric")
    hour_2: Optional[Decimal] = Field(default=None, description="2 hour metric")
    hour_4: Optional[Decimal] = Field(default=None, description="4 hour metric")
    hour_12: Optional[Decimal] = Field(default=None, description="12 hour metric")
    hour_24: Optional[Decimal] = Field(default=None, description="24 hour metric")


class CLMMPoolListItem(BaseModel):
    """Individual pool item in CLMM pool listing - matches Gateway fetch-pools response"""
    address: str = Field(description="Pool address")
    name: str = Field(description="Pool name (e.g., 'SOL-USDC')")
    trading_pair: str = Field(description="Trading pair derived from tokens")
    mint_x: str = Field(description="Base token mint address")
    mint_y: str = Field(description="Quote token mint address")
    bin_step: int = Field(description="Bin step / tick spacing")
    current_price: Decimal = Field(description="Current pool price")
    liquidity: str = Field(description="Total value locked (TVL) in USD")
    base_fee_percentage: Optional[str] = Field(default=None, description="Base fee percentage")
    apr: Optional[Decimal] = Field(default=None, description="Annual percentage rate")
    apy: Optional[Decimal] = Field(default=None, description="Annual percentage yield")
    volume_24h: Optional[Decimal] = Field(default=None, description="24h trading volume")
    fees_24h: Optional[Decimal] = Field(default=None, description="24h fees collected")


class CLMMPoolListResponse(BaseModel):
    """Response with list of available CLMM pools - matches Gateway fetch-pools response"""
    pools: List[CLMMPoolListItem] = Field(description="List of available pools")
    total: int = Field(description="Total number of matching pools")
    page: int = Field(description="Current page number")
    pageSize: int = Field(description="Number of pools per page")
