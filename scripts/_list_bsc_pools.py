import asyncio
import os
from pathlib import Path
import importlib.util
import json

gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
spec = importlib.util.spec_from_file_location("gw", str(gw_path))
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)  # type: ignore
GatewayClient = gw.GatewayClient

async def main():
    client = GatewayClient(base_url=os.getenv('GATEWAY_URL','http://localhost:15888'))
    try:
        pools = await client.get_pools(connector='pancakeswap', network='bsc')
        print('pools count:', len(pools) if isinstance(pools, list) else 'N/A')
        if isinstance(pools, list):
            for p in pools:
                print(json.dumps(p, indent=2))
    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(main())
