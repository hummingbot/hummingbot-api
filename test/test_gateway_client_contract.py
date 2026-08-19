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

# Gateway's own config/connectors listing decides an untyped connector's swap
# type; these trading_types mirror what Gateway reports today.
_CONNECTOR_LISTING = {"connectors": [
    {"name": "jupiter", "trading_types": ["router"]},
    {"name": "0x", "trading_types": ["router"]},
    {"name": "uniswap", "trading_types": ["router", "amm", "clmm"]},
    {"name": "pancakeswap", "trading_types": ["router", "amm", "clmm"]},
    {"name": "dflow", "trading_types": ["router"]},
    {"name": "okx", "trading_types": ["router"]},
    {"name": "titan", "trading_types": ["router"]},
    {"name": "meteora", "trading_types": ["clmm", "amm"]},
    {"name": "orca", "trading_types": ["clmm"]},
    {"name": "raydium", "trading_types": ["clmm", "amm"]},
    {"name": "pancakeswap-sol", "trading_types": ["clmm"]},
]}


@pytest.fixture
def client_and_calls(monkeypatch):
    """A GatewayClient whose _request records calls instead of hitting the network."""
    client = GatewayClient()
    calls = []

    async def fake_request(method, path, params=None, json=None):
        calls.append({"method": method, "path": path, "params": params, "json": json})
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    # Pre-seed Gateway's connector listing so swap payload assertions see only
    # the swap call itself, not the one-off discovery request behind it.
    client._connector_trading_types = {
        entry["name"]: entry["trading_types"] for entry in _CONNECTOR_LISTING["connectors"]
    }
    return client, calls


# ============================================
# Connector normalization
# ============================================


@pytest.mark.asyncio
@pytest.mark.parametrize("connector,expected", [
    ("jupiter", "jupiter/router"),
    ("0x", "0x/router"),
    ("uniswap", "uniswap/router"),
    ("pancakeswap", "pancakeswap/router"),
    ("dflow", "dflow/router"),
    ("okx", "okx/router"),
    ("titan", "titan/router"),
    ("meteora", "meteora/clmm"),
    ("orca", "orca/clmm"),
    ("raydium", "raydium/clmm"),
    ("pancakeswap-sol", "pancakeswap-sol/clmm"),
    # Already-typed providers pass through untouched (no doubled /router/router)
    ("jupiter/router", "jupiter/router"),
    ("meteora/clmm", "meteora/clmm"),
    ("raydium/amm", "raydium/amm"),
])
async def test_normalize_swap_connector(connector, expected):
    client = GatewayClient()
    client._connector_trading_types = {
        entry["name"]: entry["trading_types"] for entry in _CONNECTOR_LISTING["connectors"]
    }
    assert await client.normalize_swap_connector(connector) == expected


@pytest.mark.asyncio
async def test_normalize_swap_connector_rejects_unknown_name():
    client = GatewayClient()
    client._connector_trading_types = {"jupiter": ["router"]}
    with pytest.raises(GatewayError) as exc:
        await client.normalize_swap_connector("nosuchdex")
    assert "nosuchdex" in str(exc.value)


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


@pytest.mark.asyncio
async def test_quote_swap_omits_slippage_when_unset(client_and_calls):
    """No slippage_pct => omit the key so Gateway applies the connector's configured default."""
    client, calls = client_and_calls
    await client.quote_swap(
        connector="jupiter", chain_network="solana-mainnet-beta",
        base_asset="SOL", quote_asset="USDC", amount=1, side="SELL",
    )
    assert "slippagePct" not in calls[0]["params"]


@pytest.mark.asyncio
async def test_quote_swap_extra_params_bool_as_query_string(client_and_calls):
    """approximateIfNoExactOut rides extra_params; aiohttp needs query values as strings,
    and Gateway's schema coerces 'false' back to boolean."""
    client, calls = client_and_calls
    await client.quote_swap(
        connector="jupiter", chain_network="solana-mainnet-beta",
        base_asset="SOL", quote_asset="USDC", amount=1, side="BUY",
        extra_params={"approximateIfNoExactOut": False},
    )
    assert calls[0]["params"]["approximateIfNoExactOut"] == "false"


@pytest.mark.asyncio
async def test_execute_swap_extra_params_and_slippage_omission(client_and_calls):
    """extra_params keys land in the JSON body under Gateway's own names (booleans
    intact); unset slippage_pct is omitted so the connector default applies."""
    client, calls = client_and_calls
    await client.execute_swap(
        connector="jupiter/router", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", base_asset="SOL", quote_asset="USDC",
        amount=0.1, side="BUY", extra_params={"approximateIfNoExactOut": False},
    )
    body = calls[0]["json"]
    assert body["approximateIfNoExactOut"] is False
    assert "slippagePct" not in body


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
async def test_clmm_add_liquidity_extra_params_and_slippage_omission(client_and_calls):
    """strategyType rides extra_params into the body under Gateway's name; unset
    slippage_pct is omitted so the connector default applies."""
    client, calls = client_and_calls
    await client.clmm_add_liquidity(
        connector="meteora", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", position_address="POS",
        base_token_amount=0.5, quote_token_amount=50.0,
        extra_params={"strategyType": 0},
    )
    body = calls[0]["json"]
    assert body["strategyType"] == 0
    assert "slippagePct" not in body


@pytest.mark.asyncio
async def test_clmm_remove_liquidity_uses_percentage_to_remove(client_and_calls):
    client, calls = client_and_calls
    await client.clmm_remove_liquidity(
        connector="meteora", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", position_address="POS", percentage_to_remove=50.0,
    )
    call = calls[0]
    assert (call["method"], call["path"]) == ("POST", "trading/clmm/remove")
    assert call["json"]["percentageToRemove"] == 50.0
    assert "percentage" not in call["json"]
    # No slippage_pct given => omitted (Orca falls back to its configured default)
    assert "slippagePct" not in call["json"]


@pytest.mark.asyncio
async def test_clmm_remove_liquidity_sends_slippage_when_set(client_and_calls):
    """Orca honors slippagePct on remove; the client must forward it."""
    client, calls = client_and_calls
    await client.clmm_remove_liquidity(
        connector="orca", chain_network="solana-mainnet-beta",
        wallet_address="WALLET", position_address="POS", percentage_to_remove=100.0,
        slippage_pct=0.5,
    )
    assert calls[0]["json"]["slippagePct"] == 0.5


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
async def test_clmm_fetch_pools_meteora_params(client_and_calls):
    """Meteora's fetch-pools paginates and filters via page/includeUnverified."""
    client, calls = client_and_calls
    await client.clmm_fetch_pools(connector="meteora", network="mainnet-beta", limit=10,
                                  sort_by="volume_24h:desc", page=2, include_unverified=False)
    call = calls[0]
    assert (call["method"], call["path"]) == ("GET", "connectors/meteora/clmm/fetch-pools")
    assert call["params"]["page"] == 2
    assert call["params"]["includeUnverified"] == "false"
    assert call["params"]["sortBy"] == "volume_24h:desc"
    for orca_only in ("sortDirection", "verifiedOnly"):
        assert orca_only not in call["params"]


@pytest.mark.asyncio
async def test_clmm_fetch_pools_orca_params(client_and_calls):
    """Orca's fetch-pools takes sortDirection/verifiedOnly and has no pagination —
    sending meteora's knobs would be silently stripped by Gateway's AJV."""
    client, calls = client_and_calls
    await client.clmm_fetch_pools(connector="orca", network="mainnet-beta", limit=10,
                                  sort_by="volume", sort_direction="desc", verified_only=True)
    call = calls[0]
    assert (call["method"], call["path"]) == ("GET", "connectors/orca/clmm/fetch-pools")
    assert call["params"]["sortDirection"] == "desc"
    assert call["params"]["verifiedOnly"] == "true"
    for meteora_only in ("page", "includeUnverified"):
        assert meteora_only not in call["params"]


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
async def test_amm_quote_liquidity_path_and_slippage_omitted(client_and_calls):
    client, calls = client_and_calls
    await client.amm_quote_liquidity(connector="meteora", chain_network=NET, pool_address=POOL,
                                     base_token_amount=1.0, quote_token_amount=100.0)
    c = calls[0]
    assert (c["method"], c["path"]) == ("GET", "trading/amm/quote-liquidity")
    assert c["params"] == {"connector": "meteora", "chainNetwork": NET, "poolAddress": POOL,
                           "baseTokenAmount": 1.0, "quoteTokenAmount": 100.0}
    # Omitted slippage means "use the connector's configured slippagePct"
    assert "slippagePct" not in c["params"]


@pytest.mark.asyncio
async def test_amm_quote_liquidity_sends_zero_slippage(client_and_calls):
    client, calls = client_and_calls
    await client.amm_quote_liquidity(connector="meteora", chain_network=NET, pool_address=POOL,
                                     base_token_amount=1.0, quote_token_amount=100.0, slippage_pct=0)
    assert calls[0]["params"]["slippagePct"] == 0


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
    # Raydium extras, seeding slippage and seed-price fields omitted when unset
    for k in ("ammConfigIndex", "slippagePct", "quoteTokenAmount", "initialPrice"):
        assert k not in c["json"]


@pytest.mark.asyncio
async def test_amm_create_pool_raydium_amm_config_index(client_and_calls):
    client, calls = client_and_calls
    await client.amm_create_pool(connector="raydium", chain_network=NET, wallet_address=WALLET,
                                 base_token="SOL", quote_token="USDC", base_token_amount=1.0,
                                 extra_params={"ammConfigIndex": 0}, quote_token_amount=100.0)
    c = calls[0]
    assert c["json"]["ammConfigIndex"] == 0
    assert c["json"]["quoteTokenAmount"] == 100.0
    assert "configAddress" not in c["json"]


@pytest.mark.asyncio
async def test_amm_create_pool_uniswap_seeding_slippage(client_and_calls):
    """EVM seeding slippage is the standard slippagePct field, not an extra param."""
    client, calls = client_and_calls
    await client.amm_create_pool(connector="uniswap", chain_network="ethereum-mainnet", wallet_address=WALLET,
                                 base_token="WETH", quote_token="USDC", base_token_amount=1.0,
                                 initial_price=3000.0, slippage_pct=0.5)
    c = calls[0]
    assert c["json"]["slippagePct"] == 0.5
    assert c["json"]["initialPrice"] == 3000.0
    assert "configAddress" not in c["json"] and "ammConfigIndex" not in c["json"]


@pytest.mark.asyncio
async def test_clmm_create_pool_meteora_extras(client_and_calls):
    """CLMM create-pool extras ride extra_params under Gateway's names
    (binStep/feeBps/ammConfigIndex — no gas keys, those don't exist on the route)."""
    client, calls = client_and_calls
    await client.clmm_create_pool(connector="meteora", chain_network=NET, wallet_address=WALLET,
                                  base_token="SOL", quote_token="USDC", initial_price=100.0,
                                  extra_params={"binStep": 20, "feeBps": 20})
    c = calls[0]
    assert (c["method"], c["path"]) == ("POST", "trading/clmm/create-pool")
    assert c["json"]["binStep"] == 20
    assert c["json"]["feeBps"] == 20
    assert c["json"]["initialPrice"] == 100.0
    assert "ammConfigIndex" not in c["json"]
