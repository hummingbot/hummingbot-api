# File renamed from clmm_open_runner.py
#!/usr/bin/env python3
"""Runner script to open a CLMM position (and optionally add liquidity immediately).

This script calls the API endpoints implemented in `routers/clmm_connector.py`.
It imports the token ratio helper to compute complementary amounts when needed.
"""
from __future__ import annotations

import os
import argparse
import json
from typing import Optional
import requests

from scripts.clmm_token_ratio import get_pool_info


def call_open_and_add(api_url: str, payload: dict, add_base: Optional[float] = None,
					  add_quote: Optional[float] = None, add_slippage: Optional[float] = None,
					  auth: Optional[tuple] = None) -> dict:
	url = f"{api_url.rstrip('/')}/gateway/clmm/open-and-add"
	params = {}
	if add_base is not None:
		params['additional_base_token_amount'] = add_base
	if add_quote is not None:
		params['additional_quote_token_amount'] = add_quote
	if add_slippage is not None:
		params['additional_slippage_pct'] = add_slippage

	headers = {"Content-Type": "application/json"}
	resp = requests.post(url, params=params, json=payload, headers=headers, auth=auth, timeout=30)
	resp.raise_for_status()
	return resp.json()


def main() -> None:
	p = argparse.ArgumentParser(description="Open CLMM position and optionally add liquidity")
	p.add_argument("--api", default=os.getenv("API_URL", "http://localhost:8000"))
	p.add_argument("--connector", required=True)
	p.add_argument("--network", required=True)
	p.add_argument("--pool", required=True, dest="pool_address")
	p.add_argument("--lower", required=True, type=float)
	p.add_argument("--upper", required=True, type=float)
	p.add_argument("--base", type=float)
	p.add_argument("--quote", type=float)
	p.add_argument("--slippage", default=1.0, type=float)
	p.add_argument("--add-base", type=float, dest="add_base")
	p.add_argument("--add-quote", type=float, dest="add_quote")
	p.add_argument("--add-slippage", type=float, dest="add_slippage")
	p.add_argument("--wallet", dest="wallet_address")
	p.add_argument("--auth-user", default=os.getenv("API_USER"))
	p.add_argument("--auth-pass", default=os.getenv("API_PASS"))
	args = p.parse_args()

	auth = (args.auth_user, args.auth_pass) if args.auth_user and args.auth_pass else None

	payload = {
		"connector": args.connector,
		"network": args.network,
		"pool_address": args.pool_address,
		"lower_price": args.lower,
		"upper_price": args.upper,
		"base_token_amount": args.base,
		"quote_token_amount": args.quote,
		"slippage_pct": args.slippage,
		"wallet_address": args.wallet_address,
		"extra_params": {}
	}

	result = call_open_and_add(
		api_url=args.api,
		payload=payload,
		add_base=args.add_base,
		add_quote=args.add_quote,
		add_slippage=args.add_slippage,
		auth=auth
	)

	print("Result:")
	print(json.dumps(result, indent=2))
	if result.get("position_address"):
		print("You can query events at /gateway/clmm/positions/{position_address}/events to see ADD_LIQUIDITY txs")


if __name__ == "__main__":
	main()
# File renamed from clmm_open_runner.py
