"""The two-step swap flow has to be reachable, or the quote is decoration.

Gateway has had /trading/router/execute-quote all along, and nothing downstream exposed
it: not hummingbot-api, not the client, not condor. So every swap on record went through
the one-step execute, which re-prices at execution and discards the quote the caller saw
— which is the entire value of a held quote on dflow, titan and 0x.
"""
from decimal import Decimal

import pytest

from models import SwapExecuteQuoteRequest, SwapQuoteResponse
from services.gateway_client import GatewayClient


def test_a_quote_carries_the_handle_needed_to_execute_it():
    # Without quote_id on the response, the flow cannot even start: the caller has
    # nothing to pass back.
    assert "quote_id" in SwapQuoteResponse.model_fields
    assert SwapQuoteResponse.model_fields["quote_id"].default is None


def test_the_execute_quote_request_names_the_quote_and_what_it_was_for():
    request = SwapExecuteQuoteRequest(
        connector="jupiter",
        network="solana-mainnet-beta",
        quote_id="q-123",
        trading_pair="SOL-USDC",
        side="SELL",
        amount=Decimal("0.01"),
    )

    assert request.quote_id == "q-123"
    # The pair and amount are not sent to Gateway — it identifies the swap by quote_id —
    # but the recorded trade has to be filed under something.
    assert request.trading_pair == "SOL-USDC"
    assert request.amount == Decimal("0.01")


@pytest.mark.asyncio
async def test_the_client_posts_the_quote_id_to_gateways_router_route(monkeypatch):
    client = GatewayClient()
    calls = []

    async def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return {"signature": "sig", "status": 1}

    async def fake_resolve(connector):
        return ("jupiter", "router")

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(client, "resolve_swap_route", fake_resolve)

    await client.execute_quote(
        connector="jupiter",
        chain_network="solana-mainnet-beta",
        wallet_address="wallet",
        quote_id="q-123",
    )

    method, path, body = calls[0]
    assert (method, path) == ("POST", "trading/router/execute-quote")
    assert body["quoteId"] == "q-123"
    assert body["connector"] == "jupiter"


@pytest.mark.asyncio
async def test_a_pool_scoped_connector_is_refused_rather_than_re_priced(monkeypatch):
    # meteora prices against a pool at execution, so it has no cached quote. Silently
    # re-pricing would give the caller a swap at a price they never saw, which is the
    # failure this whole route exists to avoid.
    client = GatewayClient()

    async def fake_resolve(connector):
        return ("meteora", "clmm")

    monkeypatch.setattr(client, "resolve_swap_route", fake_resolve)

    with pytest.raises(ValueError, match="only routers hold a quote"):
        await client.execute_quote(
            connector="meteora",
            chain_network="solana-mainnet-beta",
            wallet_address="wallet",
            quote_id="q-123",
        )


def test_both_execute_paths_record_through_one_function():
    # /swap/execute and /swap/execute-quote differ in how the transaction was produced
    # and not at all in what has to be booked afterwards. Two copies would drift.
    source = open("routers/gateway_swap.py").read()

    assert source.count("return await _record_and_report_swap(") == 2
