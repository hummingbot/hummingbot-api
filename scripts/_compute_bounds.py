import importlib.util
import asyncio
import json
import os
from pathlib import Path

gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
spec = importlib.util.spec_from_file_location("gw", str(gw_path))
gw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw)  # type: ignore
GatewayClient = gw.GatewayClient

async def main():
    client = GatewayClient(base_url=os.getenv('GATEWAY_URL','http://localhost:15888'))
    try:
        cn = os.getenv('CLMM_CHAIN_NETWORK','bsc-mainnet')
        if '-' in cn:
            chain, network = cn.split('-', 1)
        else:
            chain = cn
            network = os.getenv('CLMM_NETWORK', 'mainnet')
        pool = os.getenv('CLMM_TOKENPOOL_ADDRESS')
        # fallback: read from .env file if not set in environment
        if not pool:
            env_file = Path(__file__).resolve().parents[1] / '.env'
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if line.strip().startswith('CLMM_TOKENPOOL_ADDRESS='):
                        pool = line.split('=', 1)[1].strip()
                        break
        connector = os.getenv('CLMM_DEFAULT_CONNECTOR', 'pancakeswap')
        info = await client.clmm_pool_info(connector=connector, network=network, pool_address=pool)
        price = float((info.get('price') or 0) if isinstance(info, dict) else 0)
        range_pct = float(os.getenv('CLMM_TOKENPOOL_RANGE','2.5'))
        lower = price * (1.0 - range_pct/100.0)
        upper = price * (1.0 + range_pct/100.0)
        print(json.dumps({'pool': pool, 'price': price, 'lower': lower, 'upper': upper}))
    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(main())
