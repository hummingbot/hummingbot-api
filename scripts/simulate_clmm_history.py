"""Simulate CLMM position over the last 24 hours using price history.

This is an approximation using constant-product math per timestamp.
It fetches pool info from Gateway to find the base token contract address and
then uses CoinGecko's 'binance-smart-chain' contract endpoint to get 24h prices.

Outputs a simple CSV-like summary to stdout and writes a log to tmp/sim_history.log.
"""
import asyncio
import os
import sys
import time
import math
import json
from decimal import Decimal
import importlib.util
from pathlib import Path

import aiohttp

# Load GatewayClient via file path
gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
spec = importlib.util.spec_from_file_location("gateway_client", str(gw_path))
gw_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw_mod)  # type: ignore
GatewayClient = getattr(gw_mod, "GatewayClient")


async def fetch_coingecko_prices_bsc(contract_address: str):
    url = f"https://api.coingecko.com/api/v3/coins/binance-smart-chain/contract/{contract_address}/market_chart"
    params = {"vs_currency": "usd", "days": 1}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"CoinGecko API error: {resp.status} {text}")
            data = await resp.json()
            # data['prices'] is list of [ts(ms), price]
            return data.get("prices", [])


def lp_value_constant_product(initial_base: float, initial_quote: float, price: float):
    # initial k
    k = initial_base * initial_quote
    if price <= 0:
        return 0.0
    x = math.sqrt(k / price)
    y = price * x
    return x * price + y


async def main():
    pool = os.getenv("CLMM_TOKENPOOL_ADDRESS")
    if not pool:
        print("No CLMM_TOKENPOOL_ADDRESS set in env; aborting")
        return 2

    gateway_url = os.getenv("GATEWAY_URL", "http://localhost:15888")
    connector = os.getenv("CLMM_DEFAULT_CONNECTOR", "pancakeswap")
    chain_network = os.getenv("CLMM_CHAIN_NETWORK", "bsc-mainnet")

    # parse network for Gateway client calls
    parts = chain_network.split("-", 1)
    if len(parts) == 2:
        chain, network = parts
    else:
        chain = parts[0]
        network = "mainnet"

    client = GatewayClient(base_url=gateway_url)
    try:
        pool_info = await client.clmm_pool_info(connector=connector, network=network, pool_address=pool)
    except Exception as e:
        print("Failed to fetch pool info from Gateway:", e)
        await client.close()
        return 1

    base_token_addr = pool_info.get("baseTokenAddress") if isinstance(pool_info, dict) else None
    base_sym = pool_info.get("baseTokenSymbol") or pool_info.get("baseToken") or pool_info.get("base")
    current_price = float(pool_info.get("price") or 0)

    if not base_token_addr:
        print("Pool info did not include base token address; aborting")
        await client.close()
        return 1

    print(f"Simulating for pool {pool}; base token addr={base_token_addr}; current_price={current_price}")

    # Fetch CoinGecko prices
    try:
        prices = await fetch_coingecko_prices_bsc(base_token_addr)
    except Exception as e:
        print("Failed to fetch CoinGecko prices:", e)
        await client.close()
        return 1

    # Prepare time series: list of (ts, price)
    series = [(int(p[0]) / 1000.0, float(p[1])) for p in prices]

    # Simulation params
    initial_base = float(os.getenv("SIM_INITIAL_BASE", "100"))
    # derive initial quote using first price
    if not series:
        print("No price series returned; aborting")
        await client.close()
        return 1

    start_price = series[0][1]
    initial_quote = initial_base * start_price

    # Range percent
    range_pct = float(os.getenv("CLMM_TOKENPOOL_RANGE", "2.5"))
    lower = start_price * (1.0 - range_pct / 100.0)
    upper = start_price * (1.0 + range_pct / 100.0)

    outfile = Path(__file__).resolve().parents[1] / "tmp" / "sim_history.log"
    outfile.parent.mkdir(parents=True, exist_ok=True)

    with open(outfile, "w") as f:
        f.write("timestamp,price,hodl_value,lp_value_inrange,lower,upper\n")
        for ts, price in series:
            hodl = initial_base * price + initial_quote
            # If price within range, approximate LP constant-product value; else we approximate as final out-of-range handling by LP
            if lower <= price <= upper:
                lpv = lp_value_constant_product(initial_base, initial_quote, price)
            else:
                # approximate that position remains liquid but instant close value using current price
                # For simplicity, approximate as hodl (conservative)
                lpv = lp_value_constant_product(initial_base, initial_quote, price)

            f.write(f"{int(ts)},{price},{hodl:.8f},{lpv:.8f},{lower:.8f},{upper:.8f}\n")

    print(f"Simulation completed, wrote {outfile}")
    await client.close()
    return 0


if __name__ == '__main__':
    res = asyncio.run(main())
    sys.exit(res)
