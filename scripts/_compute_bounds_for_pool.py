import asyncio
import os
import json
from pathlib import Path
import importlib.util

gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
spec = importlib.util.spec_from_file_location("gw", str(gw_path))
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)  # type: ignore
GatewayClient = gw.GatewayClient


async def main():
    client = GatewayClient(base_url=os.getenv('GATEWAY_URL','http://localhost:15888'))
    try:
        connector = os.getenv('CLMM_DEFAULT_CONNECTOR', 'pancakeswap')
        pool = os.getenv('CLMM_TOKENPOOL_ADDRESS') or '0xc397874a6cf0211537a488fa144103a009a6c619'
        # Try network candidates; pool found in earlier inspection under network 'bsc'
        networks = ['bsc', 'mainnet', 'bsc-mainnet']
        info = None
        for net in networks:
            try:
                info = await client.clmm_pool_info(connector=connector, network=net, pool_address=pool)
                print('Found pool info on network=', net)
                break
            except Exception as e:
                # continue trying
                pass

        if not info:
            print('Pool not found on attempted networks')
            return 1

        price = float((info.get('price') or 0) if isinstance(info, dict) else 0)
        range_pct = float(os.getenv('CLMM_TOKENPOOL_RANGE','2.5'))
        lower = price * (1.0 - range_pct/100.0)
        upper = price * (1.0 + range_pct/100.0)
        print(json.dumps({'pool': pool, 'price': price, 'lower': lower, 'upper': upper}, indent=2))
    finally:
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
