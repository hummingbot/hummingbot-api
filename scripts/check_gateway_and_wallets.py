import asyncio
import os
import sys
import importlib.util
from pathlib import Path

# Import GatewayClient by file path to avoid package import issues in dev env
gw_path = Path(__file__).resolve().parents[1] / "services" / "gateway_client.py"
spec = importlib.util.spec_from_file_location("gateway_client", str(gw_path))
gw_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gw_mod)  # type: ignore
GatewayClient = getattr(gw_mod, "GatewayClient")

async def main():
    base_url = os.getenv("GATEWAY_URL", "http://localhost:15888")
    print(f"Using Gateway URL: {base_url}")
    client = GatewayClient(base_url=base_url)
    try:
        ok = await client.ping()
        print("Gateway ping:", ok)
        wallets = await client.get_wallets()
        print("Wallets:", wallets)

        # Resolve chain/network from env CLMM_CHAIN_NETWORK if present (canonical format 'chain-network')
        chain_network = os.getenv('CLMM_CHAIN_NETWORK') or os.getenv('CLMM_TOKENPOOL_NETWORK')
        if chain_network and '-' in chain_network:
            chain, network = chain_network.split('-', 1)
        else:
            # Fallback: try legacy env or defaults
            chain = os.getenv('CLMM_CHAIN', 'bsc')
            network = os.getenv('CLMM_NETWORK', 'mainnet')

        print(f"Resolved chain/network: {chain}/{network}")

        # Prefer explicit CLMM_WALLET_ADDRESS env if provided (so scripts can pin a wallet)
        env_wallet = os.getenv('CLMM_WALLET_ADDRESS')
        if env_wallet:
            print(f'CLMM_WALLET_ADDRESS env is set: {env_wallet}')

        default_wallet = env_wallet or await client.get_default_wallet_address(chain)
        print(f'Default wallet for chain {chain}:', default_wallet)

        if default_wallet:
            # Verify the wallet exists in Gateway's wallet list
            all_wallets = await client.get_all_wallet_addresses(chain)
            known = all_wallets.get(chain, [])
            if default_wallet not in known:
                print(f'Warning: wallet {default_wallet} is not registered in Gateway for chain {chain}. Gateway knows: {known}')

            print(f'Fetching balances for address {default_wallet} on chain={chain} network={network}')
            balances = await client.get_balances(chain, network, default_wallet)
            print('Balances:', balances)
        else:
            print('No default wallet found to fetch balances for.')
    finally:
        await client.close()

if __name__ == '__main__':
    asyncio.run(main())
