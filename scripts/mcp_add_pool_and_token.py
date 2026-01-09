"""Discover token/pair metadata (optional) and add token + pool to Gateway via MCP.

This script is intended to be run locally by a developer against a running
Gateway/MCP instance (for example, http://localhost:15888). It will:
 - Optionally query on-chain token/pair metadata using web3 (if installed)
 - Call the Gateway client's `add_token` and `add_pool` endpoints

Safety features:
 - --dry-run to only show the payloads (no network calls)
 - --yes to skip interactive confirmation
 - Graceful fallback if web3 is not installed or RPC cannot be reached

Usage examples:
  # Dry run (no changes):
  python scripts/mcp_add_pool_and_token.py --token 0x... --pool 0x... --dry-run

  # Real run against local Gateway/MCP (default gateway URL shown):
  python scripts/mcp_add_pool_and_token.py --token 0x... --pool 0x... --gateway http://localhost:15888 --rpc https://bsc-dataseed.binance.org/ --yes

Note: This script uses the repo's `GatewayClient` to call MCP endpoints. Run it
from the repository root so imports resolve correctly.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional, Tuple

try:
    # web3 is optional; if not available we'll skip on-chain discovery
    from web3 import Web3
except Exception:  # pragma: no cover - optional dependency
    Web3 = None  # type: ignore

from services.gateway_client import GatewayClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Minimal ERC20 ABI for name/symbol/decimals
ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
]

# Minimal pair ABI to read token0/token1 (UniswapV2/PancakePair style)
PAIR_ABI = [
    {"constant": True, "inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "type": "function"},
]


def fetch_token_metadata(w3: "Web3", token_address: str) -> Tuple[str, str, int]:
    """Return (name, symbol, decimals) for ERC-20 token.

    Falls back to empty values if contract calls fail.
    """
    token = w3.eth.contract(Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    name = ""
    symbol = ""
    decimals = 18
    try:
        name = token.functions.name().call()
    except Exception:
        logger.debug("Failed to fetch token name for %s", token_address)
    try:
        symbol = token.functions.symbol().call()
    except Exception:
        logger.debug("Failed to fetch token symbol for %s", token_address)
    try:
        decimals = int(token.functions.decimals().call())
    except Exception:
        logger.debug("Failed to fetch token decimals for %s", token_address)
    return name or "", symbol or "", int(decimals)


def fetch_pair_tokens(w3: "Web3", pair_address: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (token0, token1) or (None, None) on failure."""
    pair = w3.eth.contract(Web3.to_checksum_address(pair_address), abi=PAIR_ABI)
    try:
        token0 = pair.functions.token0().call()
        token1 = pair.functions.token1().call()
        return Web3.to_checksum_address(token0), Web3.to_checksum_address(token1)
    except Exception:
        logger.debug("Failed to fetch pair tokens for %s", pair_address)
        return None, None


async def add_token_and_pool(
    gateway_url: str,
    chain: str,
    network: str,
    token_address: str,
    token_symbol: str,
    token_name: str,
    token_decimals: int,
    pool_address: str,
    connector: str,
    pool_type: str = "amm",
    base_symbol: Optional[str] = None,
    quote_symbol: Optional[str] = None,
    fee_pct: Optional[float] = None,
    dry_run: bool = False,
):
    client = GatewayClient(base_url=gateway_url)

    token_payload = {
        "chain": chain,
        "network": network,
        "address": token_address,
        "symbol": token_symbol,
        "name": token_name,
        "decimals": int(token_decimals),
    }

    pool_payload = {
        "connector": connector,
        "pool_type": pool_type,
        "network": network,
        "address": pool_address,
        "base_symbol": base_symbol or token_symbol,
        "quote_symbol": quote_symbol or "UNKNOWN",
        "base_token_address": token_address,
        "quote_token_address": "",
        "fee_pct": fee_pct,
    }

    logger.info("Token payload: %s", token_payload)
    logger.info("Pool payload: %s", pool_payload)

    if dry_run:
        logger.info("Dry run enabled — not sending requests to Gateway")
        return

    logger.info("Calling Gateway to add token...")
    try:
        resp = await client.add_token(
            chain,
            network,
            token_address,
            token_symbol,
            token_name,
            int(token_decimals),
        )
        logger.info("add_token response: %s", resp)
    except Exception as e:
        logger.error("add_token failed: %s", e)
        raise

    logger.info("Calling Gateway to add pool...")
    try:
        pool_resp = await client.add_pool(
            connector=connector,
            pool_type=pool_type,
            network=network,
            address=pool_address,
            base_symbol=pool_payload["base_symbol"],
            quote_symbol=pool_payload["quote_symbol"],
            base_token_address=pool_payload["base_token_address"],
            quote_token_address=pool_payload["quote_token_address"],
            fee_pct=fee_pct,
        )
        logger.info("add_pool response: %s", pool_resp)
    except Exception as e:
        logger.error("add_pool failed: %s", e)
        raise


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Discover token/pair metadata and add to Gateway via MCP")
    p.add_argument("--token", required=True, help="Token contract address (hex)")
    p.add_argument("--pool", required=True, help="Pool/pair contract address (hex)")
    p.add_argument("--rpc", required=False, default="https://bsc-dataseed.binance.org/", help="RPC URL for on-chain metadata (optional)")
    p.add_argument("--gateway", required=False, default="http://localhost:15888", help="Gateway base URL (default: http://localhost:15888)")
    p.add_argument("--connector", required=False, default="pancakeswap", help="Connector name (pancakeswap)")
    p.add_argument("--chain", required=False, default="bsc", help="Chain name for Gateway token add (e.g., bsc)")
    p.add_argument("--network", required=False, default="mainnet", help="Network name for Gateway token add (e.g., mainnet)")
    p.add_argument("--pool-type", required=False, default="amm", help="Pool type: amm or clmm")
    p.add_argument("--fee-pct", required=False, type=float, help="Optional pool fee percentage (e.g., 0.3)")
    p.add_argument("--dry-run", action="store_true", help="Show payloads but do not call Gateway")
    p.add_argument("--yes", action="store_true", help="Assume yes to prompts")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    token_addr = args.token
    pool_addr = args.pool

    name = ""
    symbol = ""
    decimals = 18
    other_symbol = None

    if Web3 is None:
        logger.warning("web3.py is not installed; skipping on-chain metadata discovery. Install with: pip install web3")
    else:
        w3 = Web3(Web3.HTTPProvider(args.rpc))
        if not w3.is_connected():
            logger.warning("Failed to connect to RPC %s; skipping on-chain metadata discovery", args.rpc)
        else:
            try:
                name, symbol, decimals = fetch_token_metadata(w3, token_addr)
                logger.info("Discovered token: symbol=%s, name=%s, decimals=%s", symbol, name, decimals)
            except Exception:
                logger.debug("Token metadata discovery failed", exc_info=True)

            try:
                token0, token1 = fetch_pair_tokens(w3, pool_addr)
                if token0 and token1:
                    other_addr = token1 if token_addr.lower() == token0.lower() else token0 if token_addr.lower() == token1.lower() else None
                    if other_addr:
                        oname, osym, odec = fetch_token_metadata(w3, other_addr)
                        other_symbol = osym
                        logger.info("Discovered other token: address=%s symbol=%s", other_addr, other_symbol)
            except Exception:
                logger.debug("Pair discovery failed or not applicable", exc_info=True)

    token_symbol = symbol or token_addr[:8]
    token_name = name or token_symbol

    logger.info("Summary:")
    logger.info("  Gateway: %s", args.gateway)
    logger.info("  RPC: %s", args.rpc)
    logger.info("  Token: %s -> symbol=%s, name=%s, decimals=%d", token_addr, token_symbol, token_name, decimals)
    logger.info("  Pool: %s (connector=%s, type=%s)", pool_addr, args.connector, args.pool_type)
    if other_symbol:
        logger.info("  Other token symbol: %s", other_symbol)

    if not args.yes and not args.dry_run:
        ans = input("Proceed to add token and pool to Gateway? (yes/no): ")
        if ans.strip().lower() not in ("y", "yes"):
            logger.info("Aborted by user")
            return 0

    try:
        asyncio.run(
            add_token_and_pool(
                gateway_url=args.gateway,
                chain=args.chain,
                network=args.network,
                token_address=token_addr,
                token_symbol=token_symbol,
                token_name=token_name,
                token_decimals=int(decimals),
                pool_address=pool_addr,
                connector=args.connector,
                pool_type=args.pool_type,
                base_symbol=token_symbol,
                quote_symbol=other_symbol,
                fee_pct=args.fee_pct,
                dry_run=args.dry_run,
            )
        )
    except Exception as e:
        logger.error("Operation failed: %s", e)
        return 2

    logger.info("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
THIS FILE WAS REMOVED. Per user request, the automatic MCP add script was reverted.
If you need this functionality again, re-create the script or ask me to add it back.
"""
