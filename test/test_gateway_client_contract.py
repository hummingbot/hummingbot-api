"""
Regression tests for the Gateway HTTP contract (paths, payload keys, error handling).

These pin the client to the Gateway route table verified live on 2026-07-13
(hummingbot/gateway feat-robinhood-chain):
- swaps go through the unified /trading/swap endpoints (NOT /connectors/{c}/router/...,
  which 404s for clmm-only connectors like meteora and doubles the path for
  connector values like "jupiter/router"),
- CLMM ops go through the unified /trading/clmm endpoints with camelCase keys
  (chainNetwork/walletAddress/percentageToRemove — NOT the legacy
  /clmm/liquidity/* paths, which do not exist on Gateway),
- non-OK Gateway responses surface as GatewayError instead of flowing onward
  as data (the "404 rendered as price 0" class of bug).

Run with: pytest test/test_gateway_client_contract.py -v
"""
import pytest

from services.gateway_client import GatewayClient, GatewayError, check_gateway_error


@pytest.fixture
def client_and_calls(monkeypatch):
    """A GatewayClient whose _request records calls instead of hitting the network."""
    client = GatewayClient()
    calls = []

    async def fake_request(method, path, params=None, json=None):
        calls.append({"method": method, "path": path, "params": params, "json": json})
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    return client, calls


# ============================================
# Connector normalization
# ============================================

@pytest.mark.parametrize("connector,expected", [
    ("jupiter", "jupiter/router"),
    ("0x", "0x/router"),
    ("uniswap", "uniswap/router"),
    ("pancakeswap", "pancakeswap/router"),
    ("meteora", "meteora/clmm"),
    ("orca", "orca/clmm"),
    ("raydium", "raydium/clmm"),
    ("pancakeswap-sol", "pancakeswap-sol/clmm"),
    # Already-typed providers pass through untouched (no doubled /router/router)
    ("jupiter/router", "jupiter/router"),
    ("meteora/clmm", "meteora/clmm"),
    ("raydium/amm", "raydium/amm"),
])
def test_normalize_swap_connector(connector, expected):
    assert GatewayClient.normalize_swap_connector(connector) == expected


# ============================================
# Swap paths and payloads (unified /trading/swap)
# ============================================

@pytest.mark.asyncio
async def test_quote_swap_uses_unified_endpoint(client_and_calls):
    client, calls = client_and_calls
    await client.quote_swap(
        connector="meteora", chain_network="solana-mainnet-beta",
        base_asset="SOL", quote_asset="USDC", amount=0.1, side="sell",
        slippage_pct=1.0,
    )
    call = calls[0]
    assert call["method"] == "GET"
    assert call["path"] == "trading/swap/quote"
    assert call["params"] == {
        "chainNetwork": "solana-mainnet-beta",
        "connector": "meteora/clmm",
        "baseToken": "SOL",
        "quoteToken": "USDC",
        "amount": "0.1",
        "side": "SELL",
        "slippagePct": "1.0",
    }


@pytest.mark.asyncio
async def test_execute_swap_uses_unified_endpoint(client_and_calls):
    client, calls = client_and_calls
    await client.execute_swap(
        connector="jupiter/router", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", base_asset="SOL", quote_asset="USDC",
        amount=0.1, side="buy",
    )
    call = calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "trading/swap/execute"
    assert call["json"]["chainNetwork"] == "solana-mainnet-beta"
    assert call["json"]["connector"] == "jupiter/router"
    assert call["json"]["walletAddress"] == "WALLET"
    assert call["json"]["side"] == "BUY"


@pytest.mark.asyncio
async def test_quote_swap_zero_slippage_is_sent(client_and_calls):
    """slippage_pct=0 must reach Gateway as 0, not be dropped as falsy."""
    client, calls = client_and_calls
    await client.quote_swap(
        connector="jupiter", chain_network="solana-mainnet-beta",
        base_asset="SOL", quote_asset="USDC", amount=1, side="BUY",
        slippage_pct=0,
    )
    assert calls[0]["params"]["slippagePct"] == "0"


# ============================================
# CLMM paths and payloads (unified /trading/clmm)
# ============================================

@pytest.mark.asyncio
async def test_clmm_open_position_path_and_keys(client_and_calls):
    client, calls = client_and_calls
    await client.clmm_open_position(
        connector="meteora", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", pool_address="POOL",
        lower_price=150.0, upper_price=250.0,
        base_token_amount=0.01, quote_token_amount=2.0, slippage_pct=1.0,
        extra_params={"strategyType": 0},
    )
    call = calls[0]
    assert (call["method"], call["path"]) == ("POST", "trading/clmm/open")
    body = call["json"]
    assert body["connector"] == "meteora"
    assert body["chainNetwork"] == "solana-mainnet-beta"
    assert body["walletAddress"] == "WALLET"
    assert body["poolAddress"] == "POOL"
    assert body["strategyType"] == 0
    # Gateway's unified schema wants numbers, not strings
    assert body["baseTokenAmount"] == 0.01
    assert body["quoteTokenAmount"] == 2.0


@pytest.mark.asyncio
async def test_clmm_add_liquidity_path_and_keys(client_and_calls):
    client, calls = client_and_calls
    await client.clmm_add_liquidity(
        connector="meteora", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", position_address="POS",
        base_token_amount=0.5, quote_token_amount=50.0, slippage_pct=1.0,
    )
    call = calls[0]
    assert (call["method"], call["path"]) == ("POST", "trading/clmm/add")
    assert call["json"]["walletAddress"] == "WALLET"
    assert call["json"]["chainNetwork"] == "solana-mainnet-beta"


@pytest.mark.asyncio
async def test_clmm_remove_liquidity_uses_percentage_to_remove(client_and_calls):
    client, calls = client_and_calls
    await client.clmm_remove_liquidity(
        connector="meteora", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", position_address="POS", percentage=50.0,
    )
    call = calls[0]
    assert (call["method"], call["path"]) == ("POST", "trading/clmm/remove")
    assert call["json"]["percentageToRemove"] == 50.0
    assert "percentage" not in call["json"]


@pytest.mark.asyncio
async def test_clmm_close_and_collect_use_wallet_address_key(client_and_calls):
    """Gateway's schemas default walletAddress when absent — sending the wrong
    key ('address') silently operates on the default wallet."""
    client, calls = client_and_calls
    await client.clmm_close_position(
        connector="meteora", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", position_address="POS",
    )
    await client.clmm_collect_fees(
        connector="meteora", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", position_address="POS",
    )
    assert (calls[0]["method"], calls[0]["path"]) == ("POST", "trading/clmm/close")
    assert (calls[1]["method"], calls[1]["path"]) == ("POST", "trading/clmm/collect-fees")
    for call in calls:
        assert call["json"]["walletAddress"] == "WALLET"
        assert "address" not in call["json"]


@pytest.mark.asyncio
async def test_clmm_pool_info_uses_unified_endpoint(client_and_calls):
    client, calls = client_and_calls
    await client.clmm_pool_info(
        connector="meteora", chain_network="solana-mainnet-beta", pool_address="POOL",
    )
    call = calls[0]
    assert (call["method"], call["path"]) == ("GET", "trading/clmm/pool-info")
    assert call["params"] == {
        "connector": "meteora",
        "chainNetwork": "solana-mainnet-beta",
        "poolAddress": "POOL",
    }


@pytest.mark.asyncio
async def test_clmm_fetch_pools_path(client_and_calls):
    client, calls = client_and_calls
    await client.clmm_fetch_pools(connector="meteora", network="mainnet-beta", limit=10)
    call = calls[0]
    assert (call["method"], call["path"]) == ("GET", "connectors/meteora/clmm/fetch-pools")


# ============================================
# Error-shape detection (check_gateway_error)
# ============================================

def test_check_gateway_error_raises_on_error_dict():
    with pytest.raises(GatewayError) as exc:
        check_gateway_error({"error": "Route not found", "status": 404})
    assert exc.value.status == 404
    assert "Route not found" in str(exc.value)


def test_check_gateway_error_raises_on_none():
    with pytest.raises(GatewayError) as exc:
        check_gateway_error(None)
    assert exc.value.status == 503


def test_check_gateway_error_passes_valid_payloads():
    quote = {"price": 75.2, "amountIn": 7.52, "amountOut": 0.1}
    assert check_gateway_error(quote) is quote
    positions = [{"address": "POS"}]
    assert check_gateway_error(positions) is positions


def test_check_gateway_error_ignores_legit_error_fields():
    """Poll responses legitimately contain an 'error' key next to tx data —
    only the exact {'error','status'} shape is the client's HTTP-error marker."""
    poll = {"txStatus": -1, "error": "SLIPPAGE_EXCEEDED (0x1771)", "signature": "abc", "fee": 0.1}
    assert check_gateway_error(poll) is poll


# ============================================
# AMM paths and payloads (unified /trading/amm)
# ============================================
# Pin the amm_* client methods to Gateway's /trading/amm/* route table (verified live on
# 2026-08-05, hummingbot/gateway feat/meteora-damm-v2): camelCase keys, connector + chainNetwork,
# position-addressing for meteora, and per-connector create-pool extras with unset optionals omitted.

NET = "solana-mainnet-beta"
WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"
POOL = "Bv65dPQKpUo7vRELhEGBkkm5wq9J3MvKGyUj8WxYtunM"


@pytest.mark.asyncio
async def test_amm_pool_info_path(client_and_calls):
    client, calls = client_and_calls
    await client.amm_pool_info(connector="meteora", chain_network=NET, pool_address=POOL)
    c = calls[0]
    assert (c["method"], c["path"]) == ("GET", "trading/amm/pool-info")
    assert c["params"] == {"connector": "meteora", "chainNetwork": NET, "poolAddress": POOL}


@pytest.mark.asyncio
async def test_amm_position_info_path(client_and_calls):
    client, calls = client_and_calls
    await client.amm_position_info(connector="meteora", chain_network=NET, pool_address=POOL, wallet_address=WALLET)
    c = calls[0]
    assert (c["method"], c["path"]) == ("GET", "trading/amm/position-info")
    assert c["params"] == {"connector": "meteora", "chainNetwork": NET, "poolAddress": POOL, "walletAddress": WALLET}


@pytest.mark.asyncio
async def test_amm_positions_owned_path(client_and_calls):
    client, calls = client_and_calls
    await client.amm_positions_owned(connector="meteora", chain_network=NET, wallet_address=WALLET)
    c = calls[0]
    assert (c["method"], c["path"]) == ("GET", "trading/amm/positions-owned")
    assert c["params"] == {"connector": "meteora", "chainNetwork": NET, "walletAddress": WALLET}


@pytest.mark.asyncio
async def test_amm_quote_swap_path_and_slippage_omitted(client_and_calls):
    client, calls = client_and_calls
    await client.amm_quote_swap(connector="raydium", chain_network=NET, pool_address=POOL,
                                base_token="SOL", side="SELL", amount=0.01)
    c = calls[0]
    assert (c["method"], c["path"]) == ("GET", "trading/amm/quote-swap")
    assert c["params"] == {"connector": "raydium", "chainNetwork": NET, "poolAddress": POOL,
                           "baseToken": "SOL", "side": "SELL", "amount": 0.01}
    assert "slippagePct" not in c["params"]


@pytest.mark.asyncio
async def test_amm_execute_swap_path(client_and_calls):
    client, calls = client_and_calls
    await client.amm_execute_swap(connector="uniswap", chain_network="ethereum-mainnet", wallet_address=WALLET,
                                  pool_address=POOL, base_token="WETH", side="BUY", amount=1.0, slippage_pct=0.5)
    c = calls[0]
    assert (c["method"], c["path"]) == ("POST", "trading/amm/execute-swap")
    assert c["json"]["walletAddress"] == WALLET
    assert c["json"]["chainNetwork"] == "ethereum-mainnet"
    assert c["json"]["slippagePct"] == 0.5


@pytest.mark.asyncio
async def test_amm_add_liquidity_omits_position_when_unset(client_and_calls):
    client, calls = client_and_calls
    await client.amm_add_liquidity(connector="meteora", chain_network=NET, wallet_address=WALLET,
                                   pool_address=POOL, base_token_amount=1.0, quote_token_amount=2.0)
    c = calls[0]
    assert (c["method"], c["path"]) == ("POST", "trading/amm/add-liquidity")
    assert "positionAddress" not in c["json"]  # omit => open a new Meteora position


@pytest.mark.asyncio
async def test_amm_add_liquidity_includes_position_when_set(client_and_calls):
    client, calls = client_and_calls
    await client.amm_add_liquidity(connector="meteora", chain_network=NET, wallet_address=WALLET,
                                   pool_address=POOL, base_token_amount=1.0, quote_token_amount=2.0,
                                   position_address="POS123")
    assert calls[0]["json"]["positionAddress"] == "POS123"


@pytest.mark.asyncio
async def test_amm_remove_liquidity_includes_position_when_set(client_and_calls):
    client, calls = client_and_calls
    await client.amm_remove_liquidity(connector="meteora", chain_network=NET, wallet_address=WALLET,
                                      pool_address=POOL, percentage_to_remove=100, position_address="POS123")
    c = calls[0]
    assert (c["method"], c["path"]) == ("POST", "trading/amm/remove-liquidity")
    assert c["json"]["percentageToRemove"] == 100
    assert c["json"]["positionAddress"] == "POS123"


@pytest.mark.asyncio
async def test_amm_remove_liquidity_omits_position_for_fungible(client_and_calls):
    client, calls = client_and_calls
    await client.amm_remove_liquidity(connector="raydium", chain_network=NET, wallet_address=WALLET,
                                      pool_address=POOL, percentage_to_remove=50)
    assert "positionAddress" not in calls[0]["json"]


@pytest.mark.asyncio
async def test_amm_create_pool_meteora_extras(client_and_calls):
    client, calls = client_and_calls
    await client.amm_create_pool(connector="meteora", chain_network=NET, wallet_address=WALLET,
                                 base_token="SOL", quote_token="USDC", base_token_amount=1.0,
                                 extra_params={"configAddress": "CFG123"})
    c = calls[0]
    assert (c["method"], c["path"]) == ("POST", "trading/amm/create-pool")
    assert c["json"]["configAddress"] == "CFG123"
    # Raydium/Uniswap extras and seed-price fields omitted when unset
    for k in ("feeConfigIndex", "gasPrice", "maxGas", "quoteTokenAmount", "initialPrice"):
        assert k not in c["json"]


@pytest.mark.asyncio
async def test_amm_create_pool_raydium_fee_config_index(client_and_calls):
    client, calls = client_and_calls
    await client.amm_create_pool(connector="raydium", chain_network=NET, wallet_address=WALLET,
                                 base_token="SOL", quote_token="USDC", base_token_amount=1.0,
                                 extra_params={"feeConfigIndex": 0}, quote_token_amount=100.0)
    c = calls[0]
    assert c["json"]["feeConfigIndex"] == 0
    assert c["json"]["quoteTokenAmount"] == 100.0
    assert "configAddress" not in c["json"]


@pytest.mark.asyncio
async def test_amm_create_pool_uniswap_gas_extras(client_and_calls):
    client, calls = client_and_calls
    await client.amm_create_pool(connector="uniswap", chain_network="ethereum-mainnet", wallet_address=WALLET,
                                 base_token="WETH", quote_token="USDC", base_token_amount=1.0,
                                 initial_price=3000.0,
                                 extra_params={"gasPrice": 20.0, "maxGas": 500000})
    c = calls[0]
    assert c["json"]["gasPrice"] == 20.0
    assert c["json"]["maxGas"] == 500000
    assert c["json"]["initialPrice"] == 3000.0
    assert "configAddress" not in c["json"] and "feeConfigIndex" not in c["json"]
