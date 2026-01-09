#!/usr/bin/env python3
"""Add BSC wallet to Gateway using W_PK from .env and persist it.

This script reads W_PK and GATEWAY_URL from the repository .env (or environment),
posts it to the Gateway /wallet/add endpoint to register and persist a BSC wallet,
backs up the gateway wallet folder, and prints the resulting wallet address and
verification about the persisted wallet file. It NEVER prints the private key.
"""
import json
import os
import sys
import time
from pathlib import Path
from shutil import copytree


HERE = Path(__file__).resolve().parents[1]
ENV_PATH = HERE / ".env"


def read_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def post_wallet_add(gateway_url: str, private_key: str, chain: str = "bsc") -> dict:
    # Use urllib to avoid extra dependencies
    import urllib.request

    url = gateway_url.rstrip("/") + "/wallet/add"
    payload = {"chain": chain, "privateKey": private_key, "setDefault": True}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            try:
                return json.loads(body)
            except Exception:
                return {"raw": body}
    except urllib.error.HTTPError as e:
        # Try to extract response body for better diagnostics
        try:
            body = e.read().decode()
            return {"error": f"HTTP {e.code}: {body}"}
        except Exception:
            return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def main():
    env = read_env(ENV_PATH)
    # Allow override from environment variables
    private_key = os.environ.get("W_PK") or env.get("W_PK")
    gateway_url = os.environ.get("GATEWAY_URL") or env.get("GATEWAY_URL") or "http://localhost:15888"

    if not private_key:
        print("W_PK (private key) not found in environment or .env. Aborting.")
        sys.exit(2)

    print("Backing up gateway wallet folder before making changes...")
    wallets_dir = HERE / "gateway-files" / "conf" / "wallets"
    if wallets_dir.exists():
        ts = int(time.time())
        bak = wallets_dir.parent / f"wallets.bak.{ts}"
        try:
            copytree(wallets_dir, bak)
            print(f"Backed up wallets to {bak}")
        except Exception as e:
            print(f"Warning: could not backup wallets folder: {e}")
    else:
        print("No existing wallets folder found; will create on add.")

    print("Registering BSC wallet with Gateway... (address will be printed once created)")
    # Try adding with chain='bsc' first; if Gateway rejects 'bsc' (some Gateway builds accept only ethereum/solana),
    # fall back to using 'ethereum' which works for EVM-compatible chains like BSC.
    resp = post_wallet_add(gateway_url, private_key, chain="bsc")
    if isinstance(resp, dict) and resp.get("error") and "must be equal to one of the allowed values" in str(resp.get("error")):
        print("Gateway rejected chain='bsc', retrying with chain='ethereum' (EVM compatibility)")
        resp = post_wallet_add(gateway_url, private_key, chain="ethereum")

    if isinstance(resp, dict) and resp.get("error"):
        print("Gateway API error:", resp.get("error"))
        sys.exit(1)

    # Attempt to extract wallet address from response
    addr = None
    if isinstance(resp, dict):
        # Look for common fields
        for key in ("address", "walletAddress", "data"):
            if key in resp:
                v = resp[key]
                if isinstance(v, dict) and "address" in v:
                    addr = v.get("address")
                elif isinstance(v, str) and v.startswith("0x"):
                    addr = v
                elif isinstance(v, dict) and v.get("address"):
                    addr = v.get("address")
                break

    # Fallback: search strings in raw JSON for 0x...
    if not addr:
        s = json.dumps(resp)
        import re

        m = re.search(r"0x[a-fA-F0-9]{40}", s)
        if m:
            addr = m.group(0)

    if not addr:
        print("Could not determine wallet address from Gateway response. Raw response:")
        print(json.dumps(resp))
        sys.exit(1)

    print(f"Successfully added wallet address: {addr}")

    # Verify wallet file presence
    bsc_dir = wallets_dir / "bsc"
    expected_file = bsc_dir / f"{addr}.json"
    if expected_file.exists():
        print(f"Wallet file persisted at: {expected_file}")
    else:
        print(f"Wallet file not yet present at {expected_file}. Listing {bsc_dir} contents if available:")
        if bsc_dir.exists():
            for p in sorted(bsc_dir.iterdir()):
                print(" -", p.name)
        else:
            print(" - bsc wallet directory does not exist yet")

    # Update .env with CLMM_WALLET_ADDRESS if not present
    current_clmm = env.get("CLMM_WALLET_ADDRESS") or os.environ.get("CLMM_WALLET_ADDRESS")
    if not current_clmm:
        # Append to .env
        try:
            with open(ENV_PATH, "a") as f:
                f.write(f"\nCLMM_WALLET_ADDRESS={addr}\n")
            print("Wrote CLMM_WALLET_ADDRESS to .env")
        except Exception as e:
            print(f"Warning: could not write to .env: {e}")
    else:
        print(f"CLMM_WALLET_ADDRESS already set to {current_clmm}; not modifying .env")

    # Final note
    print("Done. Please verify Gateway lists the wallet and then I can start the rebalancer in execute mode if you confirm.")


if __name__ == "__main__":
    main()
