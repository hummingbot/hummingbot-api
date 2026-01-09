#!/usr/bin/env python3
"""Check whether a given CLMM pool is registered in Gateway and print pool info."""
import asyncio
import os
import importlib.util
from pathlib import Path
async def main():
    gateway = os.environ.get("GATEWAY_URL", "http://localhost:15888")
    pool = os.environ.get("CLMM_TOKENPOOL_ADDRESS")
    connector = os.environ.get("CLMM_CONNECTOR", "pancakeswap")
    chain_network = os.environ.get("CLMM_CHAIN_NETWORK", "bsc-mainnet")

    if not pool:
        print("No CLMM_TOKENPOOL_ADDRESS set in env or .env. Provide pool address via env CLMM_TOKENPOOL_ADDRESS.")
        return
    # parse chain-network
    if "-" in chain_network:
        chain, network = chain_network.split("-", 1)
    else:
        chain, network = chain_network, "mainnet"

    # Import GatewayClient by path
    gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
    spec = importlib.util.spec_from_file_location("gateway_client", str(gw_path))
    gw_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw_mod)  # type: ignore
    GatewayClient = getattr(gw_mod, "GatewayClient")
    client = GatewayClient(base_url=gateway)
    try:
        print(f"Querying Gateway {gateway} for connector={connector} network={network} pool={pool}")
    info = await client.clmm_pool_info(connector=connector, network=network, pool_address=pool)
    print("Pool info:")
    print(info)
    except Exception as e:
        print("Failed to fetch pool info:", e)
    finally:
    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
#!/usr/bin/env python3
"""Check whether a given CLMM pool is registered in Gateway and print pool info."""
import asyncio
import os
import importlib.util
from pathlib import Path


async def main():
    gateway = os.environ.get("GATEWAY_URL", "http://localhost:15888")
    pool = os.environ.get("CLMM_TOKENPOOL_ADDRESS")
    connector = os.environ.get("CLMM_CONNECTOR", "pancakeswap")
    chain_network = os.environ.get("CLMM_CHAIN_NETWORK", "bsc-mainnet")

    if not pool:
        print("No CLMM_TOKENPOOL_ADDRESS set in env or .env. Provide pool address via env CLMM_TOKENPOOL_ADDRESS.")
        return

    # parse chain-network
    if "-" in chain_network:
        chain, network = chain_network.split("-", 1)
    else:
        chain, network = chain_network, "mainnet"

    # Import GatewayClient by path
    gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
    spec = importlib.util.spec_from_file_location("gateway_client", str(gw_path))
    gw_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw_mod)  # type: ignore
    GatewayClient = getattr(gw_mod, "GatewayClient")

    client = GatewayClient(base_url=gateway)
    try:
        print(f"Querying Gateway {gateway} for connector={connector} network={network} pool={pool}")
        info = await client.clmm_pool_info(connector=connector, network=network, pool_address=pool)
        print("Pool info:")
        print(info)
    except Exception as e:
        print("Failed to fetch pool info:", e)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
