import asyncio
import os
import json
from pathlib import Path
import importlib.util

# Load GatewayClient
gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
spec = importlib.util.spec_from_file_location("gw", str(gw_path))
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)  # type: ignore
GatewayClient = gw.GatewayClient


async def main():
    base_url = os.getenv('GATEWAY_URL', 'http://localhost:15888')
    client = GatewayClient(base_url=base_url)
    try:
        cn = os.getenv('CLMM_CHAIN_NETWORK', 'bsc-mainnet')
        if '-' in cn:
            chain, network = cn.split('-', 1)
        else:
            chain = cn
            network = os.getenv('CLMM_NETWORK', 'mainnet')

        connector = os.getenv('CLMM_DEFAULT_CONNECTOR', 'pancakeswap')
        pool = os.getenv('CLMM_TOKENPOOL_ADDRESS')
        # fallback to reading .env in repo if not provided in process env
        if not pool:
            env_file = Path(__file__).resolve().parents[1] / '.env'
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if line.strip().startswith('CLMM_TOKENPOOL_ADDRESS='):
                        pool = line.split('=', 1)[1].strip()
                        break
        print('Resolved pool address:', pool)

        print('Gateway URL:', base_url)
        ok = await client.ping()
        print('ping:', ok)

        wallets = await client.get_wallets()
        print('wallets:', json.dumps(wallets, indent=2))

        print('Listing pools for connector=%s' % connector)
        # Try multiple plausible network identifiers because Gateway historically accepts a few variants
        tried = []
        found = False
        network_candidates = [network, chain, f"{chain}-{network}"]
        for net in network_candidates:
            try:
                print(f"  trying network='{net}'...")
                pools = await client.get_pools(connector=connector, network=net)
                print(f"    got {len(pools) if isinstance(pools, list) else 'N/A'} pools")
                tried.append(net)
                if isinstance(pools, list):
                    for p in pools:
                        if 'address' in p and pool and p.get('address', '').lower() == pool.lower():
                            print('Found matching pool entry in Gateway (network=%s):' % net, json.dumps(p, indent=2))
                            found = True
                            break
                if found:
                    break
            except Exception as e:
                print(f"    get_pools({connector},{net}) raised: {repr(e)}")

        if not found:
            print('Pool address not found in Gateway pools list for tried networks:', tried)

        # Also try pool_info detail across candidates
        for net in network_candidates:
            try:
                info = await client.clmm_pool_info(connector=connector, network=net, pool_address=pool)
                print('pool_info (network=%s):' % net, json.dumps(info, indent=2))
                found = True
                break
            except Exception as e:
                print(f"  clmm_pool_info(connector={connector}, network={net}) raised: {repr(e)}")

    finally:
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
