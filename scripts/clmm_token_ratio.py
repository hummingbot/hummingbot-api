#!/usr/bin/env python3
"""CLMM token ratio helper.

Provides functions to fetch pool price and compute complementary token amounts
for concentrated liquidity positions (CLMM). Includes a small CLI for quick use.

This file is intentionally dependency-light (uses requests) so it can be used
from a developer machine or CI quickly.
"""
from __future__ import annotations

import os
import argparse
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple
import requests


def get_pool_info(api_url: str, connector: str, network: str, pool_address: str, auth: Optional[tuple] = None) -> dict:
    """Fetch pool info from the API's /gateway/clmm/pool-info endpoint.

    Returns the parsed JSON response (dict). Raises requests.HTTPError on bad status.
    """
    url = f"{api_url.rstrip('/')}/gateway/clmm/pool-info"
    params = {"connector": connector, "network": network, "pool_address": pool_address}
    resp = requests.get(url, params=params, auth=auth, timeout=15)
    resp.raise_for_status()
    return resp.json()


def compute_amounts_from_price(current_price: Decimal, base_amount: Optional[Decimal] = None,
                               quote_amount: Optional[Decimal] = None,
                               quote_value: Optional[Decimal] = None) -> Tuple[Decimal, Decimal]:
    """Compute complementary base/quote amounts using the pool price.

    Price convention: price is amount of quote per 1 base (base/quote).

    Exactly one of base_amount, quote_amount or quote_value must be provided.
    Returns a tuple (base_amount, quote_amount) as Decimal values.
    """
    if current_price is None or current_price == Decimal(0):
        raise ValueError("Invalid current_price: must be non-zero Decimal")

    provided = sum(1 for v in (base_amount, quote_amount, quote_value) if v is not None)
    if provided == 0:
        raise ValueError("One of base_amount, quote_amount or quote_value must be provided")
    if provided > 1:
        raise ValueError("Provide only one of base_amount, quote_amount or quote_value")

    if base_amount is not None:
        quote_req = (base_amount * current_price).quantize(Decimal("1.0000000000"))
        return base_amount, quote_req

    if quote_amount is not None:
        try:
            base_req = (quote_amount / current_price).quantize(Decimal("1.0000000000"))
        except (InvalidOperation, ZeroDivisionError):
            raise ValueError("Invalid price or quote amount")
        return base_req, quote_amount

    if quote_value is not None:
        try:
            base_req = (quote_value / current_price).quantize(Decimal("1.0000000000"))
        except (InvalidOperation, ZeroDivisionError):
            raise ValueError("Invalid price or quote value")
        return base_req, quote_value

    # Shouldn't reach here
    raise ValueError("Invalid input combination")


def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"Invalid decimal value: {value}")


def main() -> None:
    p = argparse.ArgumentParser(description="Compute CLMM token ratio using pool price")
    p.add_argument("--api", default=os.getenv("API_URL", "http://localhost:8000"), help="Base API URL")
    p.add_argument("--connector", required=True, help="Connector name (e.g., meteora)")
    p.add_argument("--network", required=True, help="Network id (e.g., solana-mainnet-beta)")
    p.add_argument("--pool", required=True, dest="pool_address", help="Pool address/ID")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--base-amount", type=_parse_decimal, help="Amount of base token to supply (human units)")
    group.add_argument("--quote-amount", type=_parse_decimal, help="Amount of quote token to supply (human units)")
    group.add_argument("--quote-value", type=_parse_decimal, help="Quote token value to supply (human units)")
    p.add_argument("--auth-user", default=os.getenv("API_USER"))
    p.add_argument("--auth-pass", default=os.getenv("API_PASS"))

    args = p.parse_args()
    auth = (args.auth_user, args.auth_pass) if args.auth_user and args.auth_pass else None

    pool = get_pool_info(args.api, args.connector, args.network, args.pool_address, auth=auth)
    price = pool.get("price")
    if price is None:
        raise SystemExit("Pool did not return a price")

    price_dec = Decimal(str(price))

    base_amt, quote_amt = compute_amounts_from_price(
        current_price=price_dec,
        base_amount=args.base_amount,
        quote_amount=args.quote_amount,
        quote_value=args.quote_value,
    )

    print("Pool price (base/quote):", price_dec)
    print("Computed base token amount:", base_amt)
    print("Computed quote token amount:", quote_amt)
    print()
    print("Example JSON payload for open: ")
    example = {
        "connector": args.connector,
        "network": args.network,
        "pool_address": args.pool_address,
        "lower_price": None,
        "upper_price": None,
        "base_token_amount": float(base_amt),
        "quote_token_amount": float(quote_amt),
        "slippage_pct": 1.0,
    }
    print(example)


if __name__ == "__main__":
    main()
