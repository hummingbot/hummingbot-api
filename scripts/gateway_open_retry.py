#!/usr/bin/env python3
"""Simple Gateway open-position retry script.

Posts a fixed CLMM open request directly to Gateway (no API auth) and
retries until a successful transaction signature is returned. Uses only
the Python standard library so it works in minimal environments.

Usage: python scripts/gateway_open_retry.py
"""
import json
import time
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:15888")

# Configure the payload here. Adjust prices/amounts as needed.
PAYLOAD = {
    "connector": "pancakeswap",
    # Gateway expects short network name when called directly
    "network": "bsc",
    "pool_address": "0xc397874a6Cf0211537a488fa144103A009A6C619",
    # Use camelCase keys expected by Gateway
    "lowerPrice": 0.000132417,
    "upperPrice": 0.000143445,
    "quoteTokenAmount": 0.015005159330376614,
    "slippagePct": 1.0,
}

OPEN_PATH = "/connectors/pancakeswap/clmm/open-position"


def is_successful_response(obj: dict) -> bool:
    # Gateway returns a 'signature' (tx hash) and status==1 on success
    if not isinstance(obj, dict):
        return False
    if obj.get("signature"):
        return True
    # Some gateways return {"status":1, "data":{...}}
    if obj.get("status") in (1, "1"):
        return True
    # Or include a position address in data
    data = obj.get("data") or {}
    if isinstance(data, dict) and data.get("positionAddress"):
        return True
    return False


def post_open(payload: dict):
    url = GATEWAY_URL.rstrip("/") + OPEN_PATH
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            try:
                obj = json.loads(raw)
            except Exception:
                print("Non-JSON response:", raw)
                return False, raw
            return True, obj
    except HTTPError as e:
        try:
            raw = e.read().decode("utf-8")
            obj = json.loads(raw)
            return False, obj
        except Exception:
            return False, {"error": str(e)}
    except URLError as e:
        return False, {"error": str(e)}


def main():
    print("Gateway open-position retry script")
    print(f"Gateway URL: {GATEWAY_URL}{OPEN_PATH}")
    attempt = 0
    while True:
        attempt += 1
        print(f"\nAttempt {attempt}: posting open-position...")
        ok, resp = post_open(PAYLOAD)
        if ok and is_successful_response(resp):
            print("Success! Gateway returned:")
            print(json.dumps(resp, indent=2))
            return 0
        # Print the response for debugging
        print("Attempt result:")
        try:
            print(json.dumps(resp, indent=2, ensure_ascii=False))
        except Exception:
            print(resp)

        # Backoff before retrying
        time.sleep(1)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted by user")
        sys.exit(2)
