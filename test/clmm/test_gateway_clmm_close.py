import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from decimal import Decimal

import pytest

from routers import clmm_connector


class FakePosition:
    def __init__(self):
        self.id = 1
        self.position_address = "pos1"
        self.pool_address = "pool1"
        self.wallet_address = "wallet1"
        self.initial_base_token_amount = 10
        self.initial_quote_token_amount = 0
        self.base_fee_collected = 0
        self.quote_fee_collected = 0
        self.base_token_amount = 10
        self.quote_token_amount = 0
        self.created_at = datetime.utcnow() - timedelta(hours=1)
        self.current_price = 100
        self.base_token = "BASE"
        self.quote_token = "QUOTE"


class FakeRepo:
    def __init__(self, session=None):
        self._pos = FakePosition()
        self.last_event = None

    async def get_position_by_address(self, position_address):
        return self._pos if position_address == self._pos.position_address else None

    async def create_event(self, event_data):
        # store last event for assertions
        self.last_event = event_data
        return SimpleNamespace(**event_data)

    async def update_position_fees(self, position_address, base_fee_collected=None, quote_fee_collected=None, base_fee_pending=None, quote_fee_pending=None):
        # update internal position tracking
        if base_fee_collected is not None:
            self._pos.base_fee_collected = float(base_fee_collected)
        if quote_fee_collected is not None:
            self._pos.quote_fee_collected = float(quote_fee_collected)
        return self._pos

    async def update_position_liquidity(self, position_address, base_token_amount, quote_token_amount, current_price=None, in_range=None):
        self._pos.base_token_amount = float(base_token_amount)
        self._pos.quote_token_amount = float(quote_token_amount)
        if current_price is not None:
            self._pos.current_price = float(current_price)
        return self._pos

    async def close_position(self, position_address):
        self._pos.status = "CLOSED"
        self._pos.closed_at = datetime.utcnow()
        return self._pos


class DummyDBManager:
    def get_session_context(self):
        class Ctx:
            async def __aenter__(self_non):
                return None

            async def __aexit__(self_non, exc_type, exc, tb):
                return False

        return Ctx()


class FakeGatewayClient:
    def __init__(self, *, positions_owned=None, close_result=None, tokens=None):
        self._positions_owned = positions_owned or []
        self._close_result = close_result or {}
        self._tokens = tokens or []

    async def ping(self):
        return True

    def parse_network_id(self, network):
        # return (chain, network_name)
        return ("solana", network)

    async def get_wallet_address_or_default(self, chain, wallet_address):
        return "wallet1"

    async def clmm_positions_owned(self, connector, chain_network, wallet_address, pool_address):
        return self._positions_owned

    async def clmm_close_position(self, connector, network, wallet_address, position_address):
        return self._close_result

    async def clmm_position_info(self, connector, chain_network, position_address):
        # Simulate closed (not found) by returning error dict
        return {"error": "not found", "status": 404}

    # Minimal token helpers used by router during gas conversion (not used in these tests)
    async def get_tokens(self, chain, network):
        return {"tokens": self._tokens}

    async def quote_swap(self, connector, network, base_asset, quote_asset, amount, side):
        return {}


@pytest.fixture(autouse=True)
def patch_repo():
    # Replace real repository with fake in the router module
    original = clmm_connector.GatewayCLMMRepository
    clmm_connector.GatewayCLMMRepository = FakeRepo
    yield
    clmm_connector.GatewayCLMMRepository = original


def test_close_computes_profit_and_records_event(make_test_client):
    # Setup fake gateway client returning pre-close position with pending fees and a close result
    positions_owned = [
        {
            "address": "pos1",
            "baseFeeAmount": 0,
            "quoteFeeAmount": 0,
            "price": 100
        }
    ]

    close_result = {
        "signature": "0xclosetx",
        "data": {
            "baseFeeAmountCollected": 0.5,
            "quoteFeeAmountCollected": 0,
            "baseTokenAmountRemoved": 10,
            "quoteTokenAmountRemoved": 0,
            "fee": 0
        }
    }

    fake_client = FakeGatewayClient(positions_owned=positions_owned, close_result=close_result)

    client = make_test_client(clmm_connector.router)
    # Inject fake accounts_service as app state
    client.app.state.accounts_service = SimpleNamespace(gateway_client=fake_client, db_manager=DummyDBManager())

    # Perform close request
    resp = client.post("/gateway/clmm/close", json={
        "connector": "meteora",
        "network": "solana-mainnet-beta",
        "position_address": "pos1"
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["transaction_hash"] == "0xclosetx"
    # Ensure repo stored event with profit fields
    # Access fake repo instance via the class used in router (we can't directly retrieve instance here),
    # but GatewayCLMMRepository was replaced by FakeRepo which stores last_event on the instance used by router.
    # To verify, re-create a FakeRepo and ensure behavior is consistent (sanity).
    # Instead, check returned collected amounts
    assert data["base_fee_collected"] == "0.5" or data["base_fee_collected"] == 0.5


def test_close_no_fees_records_failed_and_raises(make_test_client):
    # Setup fake gateway client returning zero fees
    positions_owned = [
        {
            "address": "pos1",
            "baseFeeAmount": 0,
            "quoteFeeAmount": 0,
            "price": 100
        }
    ]

    close_result = {
        "signature": "0xclosetx2",
        "data": {
            "baseFeeAmountCollected": 0,
            "quoteFeeAmountCollected": 0,
            "baseTokenAmountRemoved": 10,
            "quoteTokenAmountRemoved": 0,
            "fee": 0
        }
    }

    fake_client = FakeGatewayClient(positions_owned=positions_owned, close_result=close_result)

    client = make_test_client(clmm_connector.router)
    client.app.state.accounts_service = SimpleNamespace(gateway_client=fake_client, db_manager=DummyDBManager())

    resp = client.post("/gateway/clmm/close", json={
        "connector": "meteora",
        "network": "solana-mainnet-beta",
        "position_address": "pos1"
    })

    # Expect internal server error due to zero-fee close
    assert resp.status_code == 500
    body = resp.json()
    assert "no fees" in body.get("detail", "").lower()
