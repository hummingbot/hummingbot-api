import pytest
import asyncio
from scripts.auto_clmm_rebalancer import CLMMRebalancer, StopRequested

class DummyClient:
    async def get_balances(self, chain, network, wallet_address):
        if wallet_address == "fail":
            raise Exception("Failed to fetch balances")
        return {"balances": {"base": 100, "quote": 50}}
    async def get_tokens(self, chain, network):
        return [{"address": "base", "symbol": "BASE"}, {"address": "quote", "symbol": "QUOTE"}]
    async def clmm_pool_info(self, **kwargs):
        return {"baseTokenAddress": "base", "quoteTokenAddress": "quote", "price": 1.5}

@pytest.mark.asyncio
async def test_fetch_balances_success():
    rebalancer = CLMMRebalancer(
        gateway_url="http://localhost:15888",
        connector="pancakeswap",
        chain="ethereum",
        network="bsc",
        threshold_pct=0.5,
        interval=60,
        wallet_address="0xabc",
        execute=True,
        pool_address="0xpool"
    )
    rebalancer.pool_info = await rebalancer.fetch_pool_info(DummyClient())
    balances = await rebalancer.fetch_balances(DummyClient())
    assert balances == (100, 50)

@pytest.mark.asyncio
async def test_fetch_balances_failure():
    rebalancer = CLMMRebalancer(
        gateway_url="http://localhost:15888",
        connector="pancakeswap",
        chain="ethereum",
        network="bsc",
        threshold_pct=0.5,
        interval=60,
        wallet_address="fail",
        execute=True,
        pool_address="0xpool"
    )
    rebalancer.pool_info = await rebalancer.fetch_pool_info(DummyClient())
    with pytest.raises(Exception):
        await rebalancer.fetch_balances(DummyClient())

@pytest.mark.asyncio
async def test_stop_requested_exception():
    with pytest.raises(StopRequested):
        raise StopRequested()
