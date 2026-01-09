"""Continuous CLMM rebalancer bot

Behavior (configurable via CLI args):
- On start: open a CLMM position using as much of the base token as possible (wallet balance).
- Stake the position via Gateway (if connector implements stake endpoint).
- Run a loop checking the pool price every `--interval` seconds.
- If price exits the [lower, upper] range or comes within `--threshold-pct` of either boundary, close the position.
- After close, collect returned base/quote amounts and swap quote->base (via Gateway) to maximize base token for next open.
- Repeat until stopped (Ctrl-C). Supports dry-run (--execute flag toggles actual Gateway calls).

Notes:
- Uses the lightweight GatewayClient file directly to avoid importing the whole `services` package.
- The script is defensive: missing Gateway connector routes will be logged and the bot will continue where possible.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Optional

# Ensure .env file is loaded
from dotenv import load_dotenv

LOG = logging.getLogger("auto_clmm_rebalancer")
logging.basicConfig(level=logging.INFO)
load_dotenv()


class StopRequested(Exception):
    pass


class CLMMRebalancer:
    def __init__(
        self,
        gateway_url: str,
        connector: str,
        chain: str,
        network: str,
        threshold_pct: float,
        interval: int,
        wallet_address: Optional[str],
        execute: bool,
        pool_address: str,
        lower_price: float = 1.0,  # Default lower price
        upper_price: float = 2.0,  # Default upper price
    ):
        self.gateway_url = gateway_url
        self.connector = connector
        self.chain = chain
        self.network = network
        self.pool_address = pool_address
        # Ensure default values for lower_price and upper_price
        self.lower_price = lower_price if lower_price is not None else 1.0
        self.upper_price = upper_price if upper_price is not None else 2.0
        self.threshold_pct = threshold_pct
        self.interval = interval
        self.wallet_address = wallet_address
        self.execute = execute
        self.stop = False
        # Add supports_stake attribute with default value
        self.supports_stake = False

    async def resolve_wallet(self, client):
        if not self.wallet_address and self.execute:
            return await client.get_default_wallet_address(self.chain)

    async def fetch_pool_info(self, client):
        if self.execute:
            return await client.clmm_pool_info(connector=self.connector, network=self.network, pool_address=self.pool_address)
        else:
            return {"baseTokenAddress": "<BASE>", "quoteTokenAddress": "<QUOTE>", "price": (self.lower_price + self.upper_price) / 2}

    async def fetch_balances(self, client):
        balances = await client.get_balances(self.chain, self.network, self.wallet_address)
        LOG.debug("Raw balances response: %r", balances)

        # Normalize balances
        balance_map = balances.get("balances") if isinstance(balances, dict) and "balances" in balances else balances
        base_balance = self._resolve_balance_from_map(balance_map, self.pool_info.get("baseTokenAddress"))
        quote_balance = self._resolve_balance_from_map(balance_map, self.pool_info.get("quoteTokenAddress"))

        # If still not found, map token address -> symbol and try symbol lookups
        if base_balance is None or quote_balance is None:
            try:
                tokens_resp = await client.get_tokens(self.chain, self.network)
                # tokens_resp can be either a list or a dict {"tokens": [...]}
                tokens_list = None
                if isinstance(tokens_resp, dict):
                    tokens_list = tokens_resp.get("tokens") or tokens_resp.get("data") or tokens_resp.get("result")
                elif isinstance(tokens_resp, list):
                    tokens_list = tokens_resp

                addr_to_symbol = {}
                if isinstance(tokens_list, list):
                    for t in tokens_list:
                        try:
                            addr = (t.get("address") or "").lower()
                            sym = t.get("symbol")
                            if addr and sym:
                                addr_to_symbol[addr] = sym
                        except Exception:
                            continue

                # attempt lookup again using the addr->symbol mapping
                if base_balance is None:
                    base_balance = self._resolve_balance_from_map(balance_map, self.pool_info.get("baseTokenAddress"), addr_to_symbol)
                if quote_balance is None:
                    quote_balance = self._resolve_balance_from_map(balance_map, self.pool_info.get("quoteTokenAddress"), addr_to_symbol)
            except Exception:
                # ignore metadata fetch errors
                addr_to_symbol = {}

        return base_balance, quote_balance

    def _resolve_balance_from_map(self, balance_map: dict, token_addr_or_sym: Optional[str], addr_to_symbol_map: dict | None = None):
        """Resolve a balance value from a normalized balance_map given a token address or symbol.

        Tries direct key lookup, lowercase/uppercase variants, and uses an optional addr->symbol map.
        """
        if not token_addr_or_sym or not isinstance(balance_map, dict):
            return None
        # direct
        val = balance_map.get(token_addr_or_sym)
        if val is not None:
            return val
        # case variants
        val = balance_map.get(token_addr_or_sym.lower())
        if val is not None:
            return val
        val = balance_map.get(token_addr_or_sym.upper())
        if val is not None:
            return val
        # try addr->symbol mapping
        if addr_to_symbol_map:
            sym = addr_to_symbol_map.get((token_addr_or_sym or "").lower())
            if sym:
                val = balance_map.get(sym) or balance_map.get(sym.upper()) or balance_map.get(sym.lower())
                if val is not None:
                    return val
        return None

    async def open_position(self, client, amount_to_use):
        LOG.info("Opening position using base amount: %s", amount_to_use)
        if not self.execute:
            LOG.info("Dry-run: would call open-position with base=%s", amount_to_use)
            return "drypos-1"
        else:
            # Use Gateway quote-position to compute both sides and estimated liquidity
            try:
                chain_network = f"{self.chain}-{self.network}"
                quote_resp = await client.quote_position(
                    connector=self.connector,
                    chain_network=chain_network,
                    lower_price=self.lower_price,
                    upper_price=self.upper_price,
                    pool_address=self.pool_address,
                    base_token_amount=amount_to_use,
                    slippage_pct=1.5,
                )
                LOG.debug("Quote response: %s", quote_resp)
                # Quote response expected to include estimated base/quote amounts and liquidity
                qdata = quote_resp.get("data") if isinstance(quote_resp, dict) else quote_resp
                est_base = None
                est_quote = None
                est_liquidity = None
                if isinstance(qdata, dict):
                    est_base = qdata.get("baseTokenAmount") or qdata.get("baseTokenAmountEstimated") or qdata.get("baseAmount")
                    est_quote = qdata.get("quoteTokenAmount") or qdata.get("quoteTokenAmountEstimated") or qdata.get("quoteAmount")
                    est_liquidity = qdata.get("liquidity") or qdata.get("estimatedLiquidity")

                LOG.debug("Estimated base: %s, quote: %s, liquidity: %s", est_base, est_quote, est_liquidity)

                # If the quote indicates zero liquidity, abort early
                try:
                    if est_liquidity is not None and float(est_liquidity) <= 0:
                        LOG.error("Quote returned zero estimated liquidity; skipping open. Quote data: %s", qdata)
                        return None
                except Exception:
                    # ignore parsing errors and attempt open; downstream will error if needed
                    pass

                # Use the quoted amounts if provided to avoid ZERO_LIQUIDITY
                open_base_amount = est_base if est_base is not None else amount_to_use
                open_quote_amount = est_quote

                # Try opening the position with retry + scaling if connector reports ZERO_LIQUIDITY.
                open_resp = None
                # Scaling factors to try (1x already covered, but include it for unified loop)
                scale_factors = [1.0, 2.0, 5.0]
                for factor in scale_factors:
                    try_base = (float(open_base_amount) * factor) if open_base_amount is not None else None
                    try_quote = (float(open_quote_amount) * factor) if open_quote_amount is not None else None
                    LOG.info("Attempting open-position with scale=%.2fx base=%s quote=%s", factor, try_base, try_quote)
                    open_resp = await client.clmm_open_position(
                        connector=self.connector,
                        network=self.network,
                        wallet_address=self.wallet_address,
                        pool_address=self.pool_address,
                        lower_price=self.lower_price,
                        upper_price=self.upper_price,
                        base_token_amount=try_base,
                        quote_token_amount=try_quote,
                        slippage_pct=1.5,
                    )

                    # If request succeeded and returned data, break out
                    if isinstance(open_resp, dict) and open_resp.get("data"):
                        break

                    # If the gateway returned an error string, inspect it for ZERO_LIQUIDITY
                    err_msg = None
                    if isinstance(open_resp, dict):
                        err_msg = open_resp.get("error") or open_resp.get("message") or (open_resp.get("status") and str(open_resp))
                    elif isinstance(open_resp, str):
                        err_msg = open_resp

                    if err_msg and isinstance(err_msg, str) and "ZERO_LIQUIDITY" in err_msg.upper():
                        LOG.warning("Gateway reported ZERO_LIQUIDITY for scale=%.2fx. Trying larger scale if allowed.", factor)
                        # If this was the last factor, we'll exit loop and treat as failure
                        await asyncio.sleep(1)
                        continue

                    # If error was something else, don't retry (likely permissions/allowance issues)
                    break
            except Exception as e:
                LOG.exception("Open position failed during quote/open sequence: %s", e)
                open_resp = {"error": str(e)}

            LOG.debug("Open response: %s", open_resp)
            data = open_resp.get("data") if isinstance(open_resp, dict) else None
            position_address = (
                (data.get("positionAddress") if isinstance(data, dict) else None)
                or open_resp.get("positionAddress")
                or open_resp.get("position_address")
            )

            LOG.info("Position opened: %s", position_address)

            open_data = None
            if self.execute and isinstance(open_resp, dict):
                open_data = open_resp.get("data") or {}
            base_added = None
            try:
                base_added = float(open_data.get("baseTokenAmountAdded")) if open_data and open_data.get("baseTokenAmountAdded") is not None else None
            except Exception:
                base_added = None

            # stake if supported
            if self.supports_stake:
                if self.execute:
                    try:
                        stake_resp = await client.clmm_stake_position(
                            connector=self.connector,
                            network=self.network,
                            wallet_address=self.wallet_address,
                            position_address=str(position_address),
                        )
                        LOG.debug("Stake response: %s", stake_resp)
                    except Exception as e:
                        LOG.warning("Stake failed or unsupported: %s", e)
                else:
                    LOG.info("Dry-run: would call stake-position for %s", position_address)

            return position_address

    async def monitor_price_and_close(self, client, position_address, slept=0):
        while not self.stop and position_address:
            if self.execute:
                pi = await client.clmm_pool_info(connector=self.connector, network=self.network, pool_address=self.pool_address)
                price = pi.get("price")
            else:
                price = self.pool_info.get("price")

            thresh = self.threshold_pct / 100.0
            lower_bound_trigger = price <= self.lower_price * (1.0 + thresh)
            upper_bound_trigger = price >= self.upper_price * (1.0 - thresh)
            outside = price < self.lower_price or price > self.upper_price

            LOG.info("Observed price=%.8f; outside=%s; near_lower=%s; near_upper=%s", price, outside, lower_bound_trigger, upper_bound_trigger)

            if outside or lower_bound_trigger or upper_bound_trigger:
                LOG.info("Close condition met (price=%.8f). Closing position %s", price, position_address)
                if not self.execute:
                    LOG.info("Dry-run: would call close-position for %s", position_address)
                    returned = {"baseTokenAmountRemoved": 0, "quoteTokenAmountRemoved": 0}
                else:
                    close_resp = await client.clmm_close_position(
                        connector=self.connector,
                        network=self.network,
                        wallet_address=self.wallet_address,
                        position_address=str(position_address),
                    )
                    LOG.info("Close response: %s", close_resp)
                    data = close_resp.get("data") if isinstance(close_resp, dict) else None
                    returned = {
                        "base": (data.get("baseTokenAmountRemoved") if isinstance(data, dict) else None) or close_resp.get("baseTokenAmountRemoved") or 0,
                        "quote": (data.get("quoteTokenAmountRemoved") if isinstance(data, dict) else None) or close_resp.get("quoteTokenAmountRemoved") or 0,
                    }

                LOG.info("Returned tokens: %s", returned)

                # Placeholder for P/L computation logic
                LOG.info("P/L computation logic removed for cleanup.")

                # rebalance returned quote -> base
                if self.execute and returned.get("quote") and float(returned.get("quote", 0)) > 0:
                    try:
                        LOG.info("Swapping returned quote->base: %s", returned.get("quote"))
                        swap = await client.execute_swap(
                            connector=self.connector,
                            network=self.network,
                            wallet_address=self.wallet_address,
                            base_asset=self.pool_info.get("baseTokenAddress"),
                            quote_asset=self.pool_info.get("quoteTokenAddress"),
                            amount=float(returned.get("quote")),
                            side="SELL",
                        )
                        LOG.info("Swap result: %s", swap)
                    except Exception as e:
                        LOG.warning("Swap failed: %s", e)

                position_address = None
                first_iteration = False
                break

            await asyncio.sleep(1)
            slept += 1
            if slept >= self.interval:
                break

    async def run(self):
        LOG.info("Starting the rebalancer bot...")
        GatewayClient = None
        client = None

        if self.execute:
            try:
                import importlib.util
                from pathlib import Path

                gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
                spec = importlib.util.spec_from_file_location("gateway_client", str(gw_path))
                gw_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(gw_mod)  # type: ignore
                GatewayClient = getattr(gw_mod, "GatewayClient")
            except Exception as e:
                LOG.error("Failed to import GatewayClient: %s", e)
                raise

            client = GatewayClient(base_url=self.gateway_url)
        else:
            # Ensure client is initialized for dry-run mode
            class MockClient:
                async def get_balances(self, *args, **kwargs):
                    return {}

                async def get_tokens(self, *args, **kwargs):
                    return []

            client = MockClient()

        try:
            LOG.info("Starting CLMM rebalancer (dry-run=%s). Monitoring pool %s", not self.execute, self.pool_address)

            # Check and log wallet balance at startup
            if self.execute:
                LOG.info("Checking wallet balance before starting main loop...")
                balances = await client.get_balances(self.chain, self.network, self.wallet_address)
                LOG.info("Wallet balances: %s", balances)
            else:
                LOG.info("[DRY RUN] Skipping wallet balance check.")

            stop = False

            def _signal_handler(signum, frame):
                nonlocal stop
                LOG.info("Stop requested (signal %s). Will finish current loop then exit.", signum)
                stop = True

            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)

            position_address = None
            first_iteration = True

            while not stop:
                # 1) Resolve wallet
                self.wallet_address = await self.resolve_wallet(client)

                # Sanitize wallet address: strip whitespace, lowercase, ensure '0x' prefix
                if self.wallet_address:
                    self.wallet_address = self.wallet_address.strip().lower()
                    if not self.wallet_address.startswith('0x'):
                        self.wallet_address = '0x' + self.wallet_address
                LOG.info(f"Sanitized wallet address: {self.wallet_address}")

                # 2) Get pool info (price and token addresses)
                self.pool_info = await self.fetch_pool_info(client)

                # Log wallet address and balances for debugging
                LOG.debug("Resolved wallet address: %s", self.wallet_address)
                LOG.debug("Pool info: %s", self.pool_info)
                LOG.debug("Base token: %s, Quote token: %s", self.pool_info.get("baseTokenAddress"), self.pool_info.get("quoteTokenAddress"))

                base_token = self.pool_info.get("baseTokenAddress")
                quote_token = self.pool_info.get("quoteTokenAddress")

                LOG.debug("Base token: %s, Quote token: %s", base_token, quote_token)

                # 3) Get balances
                base_balance, quote_balance = await self.fetch_balances(client)

                LOG.debug("Balances: base=%s, quote=%s", base_balance, quote_balance)

                def _as_float(val):
                    try:
                        return float(val)
                    except Exception:
                        return 0.0

                base_amt = _as_float(base_balance)
                quote_amt = _as_float(quote_balance)
                # When executing live, fail-fast on zero usable funds. In dry-run mode we allow simulation even
                # when Gateway balances are not available.
                if self.execute and base_amt <= 0.0 and quote_amt <= 0.0:
                    LOG.info("Wallet %s has zero funds (base=%s, quote=%s). Shutting down bot.", self.wallet_address, base_amt, quote_amt)
                    return

                LOG.info("Pool price=%.8f; target range [%.8f, %.8f]; threshold=%.3f%%", self.pool_info.get("price"), self.lower_price, self.upper_price, self.threshold_pct)

                # 4) Open position if none
                if not position_address:
                    if self.execute and base_balance:
                        try:
                            amount_to_use = float(base_balance)
                        except Exception:
                            amount_to_use = None
                    else:
                        amount_to_use = None

                    if amount_to_use is None and self.execute and quote_balance:
                        # try swapping quote -> base
                        try:
                            LOG.info("No base balance available, attempting quote->base swap to seed base allocation")
                            swap_resp = await client.execute_swap(
                                connector=self.connector,
                                network=self.network,
                                wallet_address=self.wallet_address,
                                base_asset=base_token,
                                quote_asset=quote_token,
                                amount=float(quote_balance),
                                side="SELL",
                            )
                            LOG.info("Swap response: %s", swap_resp)
                            balances = await client.get_balances(self.chain, self.network, self.wallet_address)
                            LOG.debug("Raw balances response after swap: %r", balances)
                            if isinstance(balances, dict) and "balances" in balances and isinstance(balances.get("balances"), dict):
                                balance_map = balances.get("balances")
                            else:
                                balance_map = balances if isinstance(balances, dict) else {}
                            LOG.debug("Raw balances response after swap: %r", balances)
                            if isinstance(balances, dict) and "balances" in balances and isinstance(balances.get("balances"), dict):
                                balance_map = balances.get("balances")
                            else:
                                balance_map = balances if isinstance(balances, dict) else {}

                            # try to resolve base balance robustly (address, symbol, case variants)
                            amount_to_use = None
                            base_balance = self._resolve_balance_from_map(balance_map, base_token)
                            if base_balance is None:
                                try:
                                    tokens_resp = await client.get_tokens(self.chain, self.network)
                                    tokens_list = None
                                    if isinstance(tokens_resp, dict):
                                        tokens_list = tokens_resp.get("tokens") or tokens_resp.get("data") or tokens_resp.get("result")
                                    elif isinstance(tokens_resp, list):
                                        tokens_list = tokens_resp

                                    addr_to_symbol = {}
                                    if isinstance(tokens_list, list):
                                        for t in tokens_list:
                                            try:
                                                addr = (t.get("address") or "").lower()
                                                sym = t.get("symbol")
                                                if addr and sym:
                                                    addr_to_symbol[addr] = sym
                                            except Exception:
                                                continue

                                    base_balance = self._resolve_balance_from_map(balance_map, base_token, addr_to_symbol)
                                except Exception:
                                    base_balance = None

                            amount_to_use = float(base_balance) if base_balance else None
                        except Exception as e:
                            LOG.warning("Quote->base swap failed: %s", e)

                    if not amount_to_use:
                        if first_iteration and self.execute:
                            LOG.info("First attempt and wallet has no usable funds (base=%s, quote=%s). Shutting down.", base_balance, quote_balance)
                            return
                        LOG.info("No funds available to open a position; sleeping %ds and retrying", self.interval)
                        await asyncio.sleep(self.interval)
                        first_iteration = False
                        continue

                    position_address = await self.open_position(client, amount_to_use)

                # 5) Monitor price and close when needed
                await self.monitor_price_and_close(client, position_address)

        except StopRequested:
            LOG.info("Stop requested. Exiting loop.")
        except Exception as e:
            LOG.exception("Unexpected error in loop: %s", e)
        finally:
            LOG.info("Exiting the loop and cleaning up resources.")
            if client:
                await client.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Continuous CLMM rebalancer bot")
    p.add_argument("--gateway", default="http://localhost:15888", help="Gateway base URL")
    p.add_argument("--connector", default="pancakeswap", help="CLMM connector")
    p.add_argument("--chain-network", dest="chain_network", required=False,
                   help="Chain-network id (format 'chain-network', e.g., 'bsc-mainnet'). Default from CLMM_CHAIN_NETWORK env")
    p.add_argument("--network", default="bsc", help="Network id (e.g., bsc). Deprecated: prefer --chain-network")
    p.add_argument("--pool", required=False, help="Pool address to operate in (default from CLMM_TOKENPOOL_ADDRESS env)")
    p.add_argument("--lower", required=False, type=float, help="Lower price for position range (optional; can be derived from CLMM_TOKENPOOL_RANGE when --execute)")
    p.add_argument("--upper", required=False, type=float, help="Upper price for position range (optional; can be derived from CLMM_TOKENPOOL_RANGE when --execute)")
    p.add_argument("--threshold-pct", required=False, type=float, default=0.5, help="Threshold percent near boundaries to trigger close (default 0.5)")
    p.add_argument("--interval", required=False, type=int, default=60, help="Seconds between checks (default 60)")
    p.add_argument("--wallet", required=False, help="Wallet address to use (optional)")
    p.add_argument("--execute", action="store_true", help="Actually call Gateway (default = dry-run)")
    p.add_argument("--supports-stake", dest="supports_stake", action="store_true",
                   help="Indicate the connector supports staking (default: enabled)")
    p.add_argument("--no-stake", dest="supports_stake", action="store_false",
                   help="Disable staking step even if connector supports it")
    p.set_defaults(supports_stake=True)
    return p.parse_args()


# Refactor to ensure proper asynchronous handling and synchronous execution where possible
def run_bot_sync(
    gateway_url: str,
    connector: str,
    chain: str,
    network: str,
    pool_address: str,
    lower_price: float,
    upper_price: float,
    threshold_pct: float,
    interval: int,
    wallet_address: Optional[str],
    execute: bool,
    supports_stake: bool,
):
    """Wrapper to run the asynchronous bot synchronously."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        LOG.error("Cannot run asyncio.run() inside an existing event loop. Please ensure the script is executed in a standalone environment.")
        return

    asyncio.run(
        CLMMRebalancer(
            gateway_url=gateway_url,
            connector=connector,
            chain=chain,
            network=network,
            pool_address=pool_address,
            lower_price=lower_price,
            upper_price=upper_price,
            threshold_pct=threshold_pct,
            interval=interval,
            wallet_address=wallet_address,
            execute=execute,
            supports_stake=supports_stake,
        ).run()
    )


def main() -> int:
    args = parse_args()

    # If pool not provided, read from env
    if not args.pool:
        args.pool = os.getenv("CLMM_TOKENPOOL_ADDRESS")
        if not args.pool:
            LOG.error("No pool provided and CLMM_TOKENPOOL_ADDRESS not set in env. Use --pool or set env var.")
            return 2

    # Resolve chain/network: prefer --chain-network, fall back to env CLMM_CHAIN_NETWORK, then to legacy --network
    chain_network = args.chain_network or os.getenv("CLMM_CHAIN_NETWORK")
    if not chain_network:
        # Fallback to legacy behavior (network only) with default chain 'bsc'
        chain = "bsc"
        network = args.network
    else:
        if "-" in chain_network:
            chain, network = chain_network.split("-", 1)
        else:
            chain = chain_network
            network = args.network

    # Force chain to 'ethereum' and network to 'bsc' if BSC is detected
    if chain.lower() == "bsc":
        chain = "ethereum"
        network = "bsc"

    # Run the bot synchronously
    rebalancer = CLMMRebalancer(
        gateway_url=args.gateway,
        connector=args.connector,
        chain=chain,
        network=network,
        pool_address=args.pool,
        lower_price=args.lower,
        upper_price=args.upper,
        threshold_pct=args.threshold_pct,
        interval=args.interval,
        wallet_address=args.wallet,
        execute=args.execute,
    )
    asyncio.run(rebalancer.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
