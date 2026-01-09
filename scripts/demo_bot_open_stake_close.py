"""Demo bot script: open a CLMM position, stake it, wait, then close it.

This is a lightweight demonstration script that uses the repository's
GatewayClient to exercise the Open -> Stake -> Wait -> Close flow.

Notes:
- Requires a running Gateway at the URL provided (default http://localhost:15888).
- The Gateway must have a wallet loaded (or you may pass wallet_address explicitly).
- By default the script performs a dry-run (prints payloads). Use --execute to actually call Gateway.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional
import os

# Delay importing GatewayClient until we actually need to execute (so dry-run works
# without installing all runtime dependencies). The client will be imported inside
# run_demo only when --execute is used.

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def run_demo(
    gateway_url: str,
    connector: str,
    chain: str,
    network: str,
    pool_address: str,
    lower_price: float,
    upper_price: float,
    base_amount: Optional[float],
    quote_amount: Optional[float],
    wallet_address: Optional[str],
    wait_seconds: int,
    execute: bool,
    supports_stake: bool,
):
    client = None

    # Resolve wallet (use provided or default). Only import/create GatewayClient
    # when execute=True; for dry-run we avoid importing heavy dependencies.

    if execute:
            # Import GatewayClient by file path to avoid importing the top-level
            # `services` package which pulls heavy dependencies (hummingbot, fastapi)
            # that are not necessary for the demo client. This makes the demo more
            # resilient in developer environments.
            try:
                import importlib.util
                from pathlib import Path

                gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
                spec = importlib.util.spec_from_file_location("gateway_client", str(gw_path))
                gw_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(gw_mod)  # type: ignore
                GatewayClient = getattr(gw_mod, "GatewayClient")
            except Exception as e:
                logger.error("Failed to import GatewayClient from services/gateway_client.py: %s", e)
                raise

    client = GatewayClient(base_url=gateway_url)

    if not wallet_address:
        # Prefer explicit CLMM_WALLET_ADDRESS env var if set
        wallet_address = os.getenv("CLMM_WALLET_ADDRESS")
        if not wallet_address:
            try:
                # Use resolved chain when getting default wallet
                wallet_address = await client.get_default_wallet_address(chain)
            except Exception:
                wallet_address = None
    else:
        # dry-run: client remains None
        client = None

    logger.info("Demo parameters:\n  gateway=%s\n  connector=%s\n  network=%s\n  pool=%s\n  lower=%.8f\n  upper=%.8f\n  base=%s\n  quote=%s\n  wallet=%s\n  wait=%ds\n  execute=%s",
                gateway_url, connector, network, pool_address, lower_price, upper_price, str(base_amount), str(quote_amount), str(wallet_address), wait_seconds, execute)

    if not execute:
        logger.info("Dry-run mode. Exiting without sending transactions.")
        return

    # If executing, perform token approvals automatically when needed.
    # This avoids a manual approve roundtrip during the demo.
    try:
        # Fetch pool info to learn token addresses
        pool_info = await client.clmm_pool_info(connector=connector, network=network, pool_address=pool_address)
        base_token_address = pool_info.get("baseTokenAddress") if isinstance(pool_info, dict) else None
        quote_token_address = pool_info.get("quoteTokenAddress") if isinstance(pool_info, dict) else None

        # If a base amount is provided, ensure allowance exists for the CLMM Position Manager
        if base_amount and base_token_address:
            allowances = await client._request("POST", f"chains/{chain}/allowances", json={
                "chain": chain,
                "network": network,
                "address": wallet_address,
                "spender": f"{connector}/clmm",
                "tokens": [base_token_address]
            })

            # allowances may return a map of token symbol -> amount or a raw approvals object
            current_allowance = None
            if isinstance(allowances, dict) and allowances.get("approvals"):
                # Try to find any non-zero approval
                for v in allowances.get("approvals", {}).values():
                    try:
                        current_allowance = float(v)
                    except Exception:
                        current_allowance = 0.0
            
            if not current_allowance or current_allowance < float(base_amount):
                logger.info("Approving base token %s for spender %s", base_token_address, f"{connector}/clmm")
                approve_resp = await client._request("POST", f"chains/{chain}/approve", json={
                    "chain": chain,
                    "network": network,
                    "address": wallet_address,
                    "spender": f"{connector}/clmm",
                    "token": base_token_address,
                    "amount": str(base_amount)
                })
                logger.info("Approve response: %s", approve_resp)
                # If we got a signature, poll until confirmed
                sig = None
                if isinstance(approve_resp, dict):
                    sig = approve_resp.get("signature") or (approve_resp.get("data") or {}).get("signature")
                if sig:
                    poll = await client.poll_transaction(network, sig, wallet_address)
                    logger.info("Approve tx status: %s", poll)
    except Exception as e:
        logger.warning("Auto-approval step failed (continuing): %s", e)

    # 1) Open position
    try:
        open_resp = await client.clmm_open_position(
            connector=connector,
            network=network,
            wallet_address=wallet_address,
            pool_address=pool_address,
            lower_price=lower_price,
            upper_price=upper_price,
            base_token_amount=base_amount,
            quote_token_amount=quote_amount,
            slippage_pct=1.5,
        )
        logger.info("Open response: %s", open_resp)
    except Exception as e:
        logger.error("Open position failed: %s", e, exc_info=True)
        return

    # Support Gateway responses that nest result under a `data` key
    data = open_resp.get("data") if isinstance(open_resp, dict) else None
    position_address = (
        (data.get("positionAddress") if isinstance(data, dict) else None)
        or open_resp.get("positionAddress")
        or open_resp.get("position_address")
    )
    tx = (
        open_resp.get("signature")
        or open_resp.get("transaction_hash")
        or open_resp.get("txHash")
        or (data.get("signature") if isinstance(data, dict) else None)
    )
    logger.info("Opened position %s tx=%s", position_address, tx)

    # 2) Stake position
    if not position_address:
        logger.error("No position address returned from open; aborting stake/close")
        return
    if supports_stake:
        try:
            stake_resp = await client.clmm_stake_position(
                connector=connector,
                network=network,
                wallet_address=wallet_address,
                position_address=str(position_address),
            )
            logger.info("Stake response: %s", stake_resp)
        except Exception as e:
            logger.error("Stake failed: %s", e, exc_info=True)
            return

        stake_tx = stake_resp.get("signature") or stake_resp.get("transaction_hash") or stake_resp.get("txHash")
        logger.info("Staked position %s tx=%s", position_address, stake_tx)
    else:
        logger.info("Skipping stake step (supports_stake=False)")

    # 3) Wait
    logger.info("Waiting %d seconds before closing...", wait_seconds)
    await asyncio.sleep(wait_seconds)

    # 4) Close position (attempt to remove liquidity / close)
    try:
        close_resp = await client.clmm_close_position(
            connector=connector,
            network=network,
            wallet_address=wallet_address,
            position_address=str(position_address),
        )
        logger.info("Close response: %s", close_resp)
    except Exception as e:
        logger.error("Close failed: %s", e, exc_info=True)
        return

    close_tx = close_resp.get("signature") or close_resp.get("transaction_hash") or close_resp.get("txHash")
    logger.info("Closed position %s tx=%s", position_address, close_tx)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Demo bot: open, stake, wait, close a CLMM position via Gateway")
    p.add_argument("--gateway", default="http://localhost:15888", help="Gateway base URL")
    p.add_argument("--connector", default="pancakeswap", help="CLMM connector name (pancakeswap)")
    p.add_argument("--chain-network", dest="chain_network", required=False,
                   help="Chain-network id (format 'chain-network', e.g., 'bsc-mainnet'). Default from CLMM_CHAIN_NETWORK env")
    p.add_argument("--network", default="bsc-mainnet", help="Network id (e.g., bsc-mainnet or ethereum-mainnet). Deprecated: prefer --chain-network")
    p.add_argument("--pool", required=False, help="Pool address (CLMM pool) to open position in (default from CLMM_TOKENPOOL_ADDRESS env)")
    p.add_argument("--lower", required=False, type=float, help="Lower price for position range (optional; can be derived from CLMM_TOKENPOOL_RANGE when --execute)")
    p.add_argument("--upper", required=False, type=float, help="Upper price for position range (optional; can be derived from CLMM_TOKENPOOL_RANGE when --execute)")
    p.add_argument("--base", required=False, type=float, help="Base token amount (optional)")
    p.add_argument("--quote", required=False, type=float, help="Quote token amount (optional)")
    p.add_argument("--wallet", required=False, help="Wallet address to use (optional, default = Gateway default)")
    p.add_argument("--wait", required=False, type=int, default=60, help="Seconds to wait between stake and close (default 60)")
    p.add_argument("--execute", action="store_true", help="Actually call Gateway (default is dry-run)")
    p.add_argument("--supports-stake", dest="supports_stake", action="store_true",
                   help="Indicate the connector supports staking (default: enabled)")
    p.add_argument("--no-stake", dest="supports_stake", action="store_false",
                   help="Disable staking step even if connector supports it")
    p.set_defaults(supports_stake=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    # If pool not provided, try env CLMM_TOKENPOOL_ADDRESS
    if not args.pool:
        args.pool = os.getenv("CLMM_TOKENPOOL_ADDRESS")
        if not args.pool:
            logger.error("No pool provided and CLMM_TOKENPOOL_ADDRESS not set in env. Use --pool or set env var.")
            return 2

    # Resolve chain/network: prefer --chain-network, fall back to env CLMM_CHAIN_NETWORK, then to legacy --network
    chain_network = args.chain_network or os.getenv("CLMM_CHAIN_NETWORK")
    if not chain_network:
        # Fallback to legacy behavior: parse chain from default network
        chain = "bsc"
        network = args.network
    else:
        if "-" in chain_network:
            chain, network = chain_network.split("-", 1)
        else:
            chain = chain_network
            network = args.network

    # If lower/upper not provided, derive from CLMM_TOKENPOOL_RANGE and CLMM_TOKENPOOL_RANGE_TYPE (default = percent)
    if args.lower is None or args.upper is None:
        try:
            range_val = float(os.getenv("CLMM_TOKENPOOL_RANGE", "2.5"))
            range_type = os.getenv("CLMM_TOKENPOOL_RANGE_TYPE", "PERCENT").upper()
        except Exception:
            range_val = 2.5
            range_type = "PERCENT"

        if range_type == "PERCENT":
            # Need pool price to compute bounds; try to fetch when executing, otherwise fail
            if args.execute:
                try:
                    # import minimal gateway client to fetch pool info
                    import importlib.util
                    from pathlib import Path

                    gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
                    spec = importlib.util.spec_from_file_location("gateway_client", str(gw_path))
                    gw_mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(gw_mod)  # type: ignore
                    GatewayClient = getattr(gw_mod, "GatewayClient")
                    client = GatewayClient(base_url=args.gateway)
                    pool_info = asyncio.run(client.clmm_pool_info(connector=args.connector, network=network, pool_address=args.pool))
                    price = float(pool_info.get("price", 0)) if isinstance(pool_info, dict) else None
                except Exception as e:
                    logger.error("Failed to fetch pool price to derive bounds: %s", e)
                    price = None
            else:
                # dry-run: cannot fetch remote pool price; require user to pass lower/upper
                price = None

            if price:
                half = range_val / 100.0
                args.lower = price * (1.0 - half)
                args.upper = price * (1.0 + half)
                logger.info("Derived lower/upper from price %.8f and range %.4f%% -> lower=%.8f upper=%.8f", price, range_val, args.lower, args.upper)
            else:
                logger.error("Lower/upper not provided and cannot derive bounds (no pool price available). Please provide --lower and --upper or run with --execute so price can be fetched.")
                return 2
        else:
            logger.error("CLMM_TOKENPOOL_RANGE_TYPE=%s is not supported for auto-derivation. Please provide --lower and --upper explicitly.", range_type)
            return 2

    try:
        asyncio.run(
            run_demo(
                gateway_url=args.gateway,
                connector=args.connector,
                chain=chain,
                network=network,
                pool_address=args.pool,
                lower_price=args.lower,
                upper_price=args.upper,
                base_amount=args.base,
                quote_amount=args.quote,
                wallet_address=args.wallet,
                wait_seconds=args.wait,
                execute=args.execute,
                supports_stake=args.supports_stake,
            )
        )
    except KeyboardInterrupt:
        logger.info("Interrupted")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
