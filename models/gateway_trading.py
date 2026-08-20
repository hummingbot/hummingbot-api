"""
Models for Gateway DEX trading operations, mirroring Gateway's unified /trading routes:
swaps (routers like Jupiter and pool-scoped AMM swaps), CLMM liquidity positions
(Meteora, Raydium, Orca, Uniswap V3, PancakeSwap), and AMM liquidity/pool creation
(Meteora DAMM v2, Raydium CPMM, Uniswap V2).
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
    amount: Decimal = Field(
        description="Amount denominated in the BASE token (SELL: base to sell; BUY: base to receive — "
        "Gateway quotes BUY as ExactOut)")
    slippage_pct: Optional[Decimal] = Field(
        default=None, description="Maximum slippage percentage; omit to use the connector's configured slippagePct")
    extra_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Connector-specific params passed through to Gateway under its own names: "
        "approximateIfNoExactOut (Solana routers). Unknown keys are rejected.")


class SwapQuoteResponse(BaseModel):
    """Swap quote, re-framed from Gateway's token-flow response into trading-pair terms.

    Gateway's quote-swap routes speak tokenIn/tokenOut; this keeps the base/quote +
    side framing bots use and passes Gateway's execution-safety fields through in
    snake_case. No gas estimate: Gateway's quote does not return one.
    """
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
    min_amount_out: Optional[Decimal] = Field(
        default=None, description="Minimum output the transaction will accept after slippage")
    max_amount_in: Optional[Decimal] = Field(
        default=None, description="Maximum input the transaction will spend after slippage")
    price_impact_pct: Optional[Decimal] = Field(
        default=None, description="Price impact of this trade size on the route")
    pool_address: Optional[str] = Field(default=None, description="Pool the quote was priced against")
    route_path: Optional[str] = Field(default=None, description="Route taken (router connectors)")
    slippage_pct: Optional[Decimal] = Field(
        default=None,
        description="Slippage percentage Gateway applied to the quote (the request value when Gateway omits it)")


class SwapExecuteRequest(BaseModel):
    """Request to execute a swap"""
    connector: str = Field(description="DEX router connector (e.g., 'jupiter', '0x')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    trading_pair: str = Field(description="Trading pair (e.g., 'SOL-USDC')")
    side: str = Field(description="Trade side: 'BUY' or 'SELL'")
    amount: Decimal = Field(
        description="Amount denominated in the BASE token (SELL: base to sell; BUY: base to receive)")
    slippage_pct: Optional[Decimal] = Field(
        default=None, description="Maximum slippage percentage; omit to use the connector's configured slippagePct")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")
    extra_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Connector-specific params passed through to Gateway under its own names: "
        "approximateIfNoExactOut (Solana routers). Unknown keys are rejected.")


class SwapExecuteResponse(BaseModel):
    """Response after executing swap.

    `amount` is what was asked for; the three fill fields are what happened. They were
    missing entirely, so a caller reconciling a position against this response was
    reconciling against its own intent: a BUY of 1000 tokens that delivered 951.68
    answered `amount: 1000` under the description "Amount swapped". Every one of these
    numbers was already in hand — the same call writes them to the swap history — so the
    only way to learn what a swap did was to execute it, discard the answer, and search
    the history by transaction hash.
    """
    transaction_hash: str = Field(description="Transaction hash")
    trading_pair: str = Field(description="Trading pair")
    side: str = Field(description="Trade side")
    amount: Decimal = Field(
        description="Amount REQUESTED, denominated in the base token (SELL: base sold; BUY: base "
                    "wanted). This is the request echoed back, not the fill — see input_amount / "
                    "output_amount for what actually moved.")
    # None until the transaction confirms: a submitted swap has no fill yet, and echoing
    # the request into these would reintroduce the defect they exist to fix.
    input_amount: Optional[Decimal] = Field(
        default=None,
        description="Amount actually spent, denominated in the input token (quote for BUY, base "
                    "for SELL). None until the transaction confirms.")
    output_amount: Optional[Decimal] = Field(
        default=None,
        description="Amount actually received, denominated in the output token (base for BUY, "
                    "quote for SELL). None until the transaction confirms.")
    price: Optional[Decimal] = Field(
        default=None,
        description="Executed price in quote per base, computed from the amounts that moved. "
                    "None until the transaction confirms.")
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
    slippage_pct: Optional[Decimal] = Field(
        default=None, description="Maximum slippage percentage; omit to use the connector's configured slippagePct")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")

    # Connector-specific parameters (e.g., strategyType for Meteora)
    extra_params: Optional[Dict[str, Any]] = Field(default=None, description="Additional connector-specific parameters")


class CLMMOpenPositionResponse(BaseModel):
    """Response after opening a new CLMM position"""
    transaction_hash: str = Field(description="Transaction hash")
    position_address: Optional[str] = Field(
        default=None,
        description="Address of the newly created position. None when the transaction was "
        "submitted but not yet confirmed (Gateway only knows the address once the tx lands) — "
        "poll the transaction; the poller records the position once it appears on-chain")
    trading_pair: str = Field(description="Trading pair")
    pool_address: str = Field(description="Pool address")
    lower_price: Decimal = Field(description="Lower price bound")
    upper_price: Decimal = Field(description="Upper price bound")
    base_token_amount_added: Optional[Decimal] = Field(
        default=None,
        description="Base amount actually added on-chain (confirmed txs only; the requested amount otherwise)")
    quote_token_amount_added: Optional[Decimal] = Field(
        default=None,
        description="Quote amount actually added on-chain (confirmed txs only; the requested amount otherwise)")
    position_rent: Optional[Decimal] = Field(
        default=None, description="Native token locked as rent for the position account (refunded on close)")
    status: str = Field(default="submitted", description="Transaction status")


class CLMMAddLiquidityRequest(BaseModel):
    """Request to add MORE liquidity to an EXISTING CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Existing position address to add liquidity to")
    base_token_amount: Optional[Decimal] = Field(default=None, description="Amount of base token to add")
    quote_token_amount: Optional[Decimal] = Field(default=None, description="Amount of quote token to add")
    slippage_pct: Optional[Decimal] = Field(
        default=None, description="Maximum slippage percentage; omit to use the connector's configured slippagePct")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")

    # Connector-specific parameters (e.g., strategyType for Meteora)
    extra_params: Optional[Dict[str, Any]] = Field(default=None, description="Additional connector-specific parameters")


class CLMMRemoveLiquidityRequest(BaseModel):
    """Request to remove SOME liquidity from a CLMM position (partial removal)"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to remove liquidity from")
    # Same name as the AMM remove model and Gateway's percentageToRemove — and distinct
    # from the position row's `percentage`, which means price-range width.
    percentage_to_remove: Decimal = Field(description="Percentage of liquidity to remove (0-100)")
    slippage_pct: Optional[Decimal] = Field(
        default=None,
        description="Maximum slippage percentage. Only honored by the Orca connector; "
        "omit to use the connector's configured slippagePct")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMClosePositionRequest(BaseModel):
    """Request to CLOSE a CLMM position completely (removes all liquidity and closes position)"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to close")
    slippage_pct: Optional[Decimal] = Field(
        default=None,
        description="Maximum acceptable slippage percentage for the withdrawal. Enforced by orca, "
                    "uniswap and pancakeswap; meteora, raydium and pancakeswap-sol close with no "
                    "minimum-amount check at all, so it changes nothing there. Omit to use the "
                    "connector's configured slippagePct. An executor widening this across retries "
                    "is what it exists for: a narrow in-range close can fail on slippage at the "
                    "configured value with no way to say \"accept more to get out\".")
    pool_address: Optional[str] = Field(
        default=None,
        description="Pool the position belongs to. Informational only — neither Gateway's call "
                    "nor the fee snapshot needs it, and unrecorded positions work without it"
    )
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMCollectFeesRequest(BaseModel):
    """Request to collect fees from a CLMM position"""
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    position_address: str = Field(description="Position address to collect fees from")
    pool_address: Optional[str] = Field(
        default=None,
        description="Pool the position belongs to. Informational only — neither Gateway's call "
                    "nor the fee snapshot needs it, and unrecorded positions work without it"
    )
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMCollectFeesResponse(BaseModel):
    """Response after collecting fees"""
    transaction_hash: str = Field(description="Transaction hash")
    position_address: str = Field(description="Position address")
    base_fee_collected: Optional[Decimal] = Field(default=None, description="Base token fees collected")
    quote_fee_collected: Optional[Decimal] = Field(default=None, description="Quote token fees collected")
    status: str = Field(default="submitted", description="Transaction status")


class CLMMClosePositionResponse(CLMMCollectFeesResponse):
    """Response after closing a position: fees collected plus what the close returned.

    The removed amounts and rent refund come from Gateway's confirmed transaction data,
    so they are None for submitted-not-confirmed transactions.
    """
    base_token_amount_removed: Optional[Decimal] = Field(
        default=None, description="Base liquidity actually withdrawn on-chain")
    quote_token_amount_removed: Optional[Decimal] = Field(
        default=None, description="Quote liquidity actually withdrawn on-chain")
    position_rent_refunded: Optional[Decimal] = Field(
        default=None, description="Native token rent refunded when the position account closed")


class CLMMQuotePositionRequest(BaseModel):
    """Request to quote a candidate CLMM position before opening or adding.

    Mirrors Gateway's GET /trading/clmm/quote-position: given the price range and
    one or both deposit amounts, returns the actual base/quote split the pool
    would take (and which side limits it) without signing anything.
    """
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'orca')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    pool_address: str = Field(description="Pool contract address")
    lower_price: Decimal = Field(description="Lower price bound")
    upper_price: Decimal = Field(description="Upper price bound")
    base_token_amount: Optional[Decimal] = Field(default=None, description="Base amount to deposit (one side may be omitted)")
    quote_token_amount: Optional[Decimal] = Field(default=None, description="Quote amount to deposit (one side may be omitted)")
    slippage_pct: Optional[Decimal] = Field(default=None, description="Max acceptable slippage percentage")


class CLMMQuotePositionResponse(BaseModel):
    """Gateway's position quote: the deposit split the pool would actually take."""
    base_limited: bool = Field(alias="baseLimited", description="True when the base side limits the deposit")
    base_token_amount: Decimal = Field(alias="baseTokenAmount", description="Base amount the position would take")
    quote_token_amount: Decimal = Field(alias="quoteTokenAmount", description="Quote amount the position would take")
    base_token_amount_max: Decimal = Field(alias="baseTokenAmountMax", description="Base ceiling after slippage")
    quote_token_amount_max: Decimal = Field(alias="quoteTokenAmountMax", description="Quote ceiling after slippage")

    model_config = {"populate_by_name": True}


class CLMMCreatePoolRequest(BaseModel):
    """Request to create a new (empty) CLMM pool — liquidity is added by opening positions.

    Mirrors Gateway's POST /trading/clmm/create-pool. Connector extras are consumed
    only by their owning connector.
    """
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'orca', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    base_token: str = Field(description="Base token symbol or address")
    quote_token: str = Field(description="Quote token symbol or address")
    initial_price: Optional[Decimal] = Field(
        default=None, description="Initial price (quote per base); market price when omitted")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default)")
    extra_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Connector-specific create params, passed through to Gateway under its own "
        "names: binStep (meteora, orca), feeBps (meteora; required for uniswap/pancakeswap — the "
        "V3 fee tier in basis points), ammConfigIndex (raydium, pancakeswap-sol). "
        "Unknown keys are rejected.")


class CLMMPositionsOwnedRequest(BaseModel):
    """Request to get all CLMM positions owned by a wallet.

    Mirrors Gateway's /trading/clmm/positions-owned, which takes no pool filter —
    every CLMM position the wallet owns on the connector is returned, each row
    carrying its own pool_address.
    """
    connector: str = Field(description="CLMM connector (e.g., 'meteora', 'raydium', 'uniswap')")
    network: str = Field(description="Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta')")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default if not provided)")


class CLMMPositionInfo(BaseModel):
    """Information about a CLMM liquidity position.

    Note: in_range here is a bool (live Gateway read); the DB-backed
    /clmm/positions/search endpoint reports in_range as the string enum
    IN_RANGE / OUT_OF_RANGE / UNKNOWN (three states, so not collapsible to bool).
    """
    position_address: str = Field(description="Position address")
    pool_address: str = Field(description="Pool address")
    trading_pair: str = Field(description="Trading pair (address-derived identifiers, not symbols)")
    base_token: str = Field(description="Base token identifier (derived from the token address; not a symbol)")
    quote_token: str = Field(description="Quote token identifier (derived from the token address; not a symbol)")
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
    active_bin_id: Optional[int] = Field(None, alias="activeBinId", description="Currently active bin/tick ID")
    # No dynamicFeePct/minBinId/maxBinId: those are Meteora connector extensions that
    # Gateway's unified /trading/clmm/pool-info response schema strips before serialization,
    # so they can never arrive here — and nothing downstream consumes them.
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


class AMMTransactionResponse(BaseModel):
    """Chain-neutral write response. `signature` holds the tx signature (Solana) or tx hash (EVM)."""
    signature: str = Field(description="Transaction signature (Solana) or transaction hash (EVM)")
    status: str = Field(
        description="Transaction status: SUBMITTED, CONFIRMED or FAILED. Mapped from "
                    "Gateway's TransactionStatus enum by the same helper the swap and "
                    "CLMM surfaces use, so one vocabulary spans all three."
    )
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
    slippage_pct: Optional[Decimal] = Field(
        default=None, description="Maximum slippage percentage; omit to use the connector's configured slippagePct")
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
    slippage_pct: Optional[Decimal] = Field(
        default=None,
        description="Seeding slippage percentage (uniswap/pancakeswap only); "
        "omit to use the connector's configured slippagePct")
    wallet_address: Optional[str] = Field(default=None, description="Wallet address (optional, uses default)")
    extra_params: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Connector-specific create params, passed through to Gateway under its own "
        "names: configAddress (meteora DAMM v2, required there), ammConfigIndex (raydium CPMM). "
        "Unknown keys are rejected.")


class AMMCreatePoolResponse(BaseModel):
    """Response after creating an AMM pool."""
    signature: str = Field(description="Transaction signature (Solana) or transaction hash (EVM)")
    status: str = Field(
        description="Transaction status: SUBMITTED, CONFIRMED or FAILED. Mapped from "
                    "Gateway's TransactionStatus enum by the same helper the swap and "
                    "CLMM surfaces use, so one vocabulary spans all three."
    )
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
# CLMM Pool Listing Models
# ============================================


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
    page_size: int = Field(description="Number of pools per page")
