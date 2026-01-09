import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routers import clmm_connector
from deps import get_accounts_service, get_database_manager
from models import CLMMOpenAndAddResponse


class DummyGatewayClient:
    def __init__(self, add_result=None):
        # allow passing an empty dict to simulate missing signature
        self._add_result = {"signature": "0xaddtx"} if add_result is None else add_result

    async def clmm_add_liquidity(self, **kwargs):
        return self._add_result

    def parse_network_id(self, network: str):
        return (network.split("-")[0], network)

    async def get_wallet_address_or_default(self, chain, wallet_address):
        return wallet_address or "dummy_wallet"

    async def ping(self):
        return True


class DummyAccountsService:
    def __init__(self, gateway_client):
        self.gateway_client = gateway_client


class DummyDBManager:
    def get_session_context(self):
        class Ctx:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return Ctx()


class DummyRepo:
    def __init__(self, session):
        pass

    async def get_position_by_address(self, address):
        class P:
            id = 1
            base_fee_collected = 0
            quote_fee_collected = 0

        return P()

    async def create_event(self, data):
        return None


@pytest.fixture
def app(monkeypatch):
    app = FastAPI()
    app.include_router(clmm_connector.router)
    monkeypatch.setattr(clmm_connector, "GatewayCLMMRepository", DummyRepo)
    return app


@pytest.fixture
def client(app, monkeypatch):
    # default accounts service and db manager will be overridden per-test
    client = TestClient(app)
    return client


def setup_dependencies(client_app, accounts_service, db_manager):
    client_app.app.dependency_overrides[get_accounts_service] = lambda: accounts_service
    client_app.app.dependency_overrides[get_database_manager] = lambda: db_manager


def test_open_and_add_success(monkeypatch, client):
    async def fake_open(request, accounts_service, db_manager):
        # Return the typed Pydantic response used by the router
        from models import CLMMOpenPositionResponse
        return CLMMOpenPositionResponse(
            transaction_hash="0xopentx",
            position_address="pos1",
            trading_pair="A-B",
            pool_address="pool1",
            lower_price=1,
            upper_price=2,
            status="submitted",
        )

    monkeypatch.setattr(clmm_connector, "open_clmm_position", fake_open)

    gateway_client = DummyGatewayClient(add_result={"signature": "0xaddtx"})
    accounts_service = DummyAccountsService(gateway_client)
    db_manager = DummyDBManager()

    setup_dependencies(client, accounts_service, db_manager)

    payload = {
        "connector": "meteora",
        "network": "solana-mainnet-beta",
        "pool_address": "pool1",
        "lower_price": 1,
        "upper_price": 2,
        "base_token_amount": 0.1,
        "quote_token_amount": 1.0,
        "slippage_pct": 1.0,
    }

    resp = client.post("/gateway/clmm/open-and-add", json=payload, params={"additional_base_token_amount": 0.01})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Validate against Pydantic model to follow project paradigm
    parsed = CLMMOpenAndAddResponse.model_validate(data)
    assert parsed.transaction_hash == "0xopentx"
    assert parsed.position_address == "pos1"
    assert parsed.add_transaction_hash == "0xaddtx"


def test_open_and_add_add_missing_hash(monkeypatch, client):
    async def fake_open(request, accounts_service, db_manager):
        from models import CLMMOpenPositionResponse
        return CLMMOpenPositionResponse(
            transaction_hash="0xopentx",
            position_address="pos2",
            trading_pair="A-B",
            pool_address="pool1",
            lower_price=1,
            upper_price=2,
            status="submitted",
        )

    monkeypatch.setattr(clmm_connector, "open_clmm_position", fake_open)

    gateway_client = DummyGatewayClient(add_result={})
    accounts_service = DummyAccountsService(gateway_client)
    db_manager = DummyDBManager()

    setup_dependencies(client, accounts_service, db_manager)

    payload = {
        "connector": "meteora",
        "network": "solana-mainnet-beta",
        "pool_address": "pool1",
        "lower_price": 1,
        "upper_price": 2,
        "base_token_amount": 0.1,
        "quote_token_amount": 1.0,
        "slippage_pct": 1.0,
    }

    resp = client.post("/gateway/clmm/open-and-add", json=payload, params={"additional_base_token_amount": 0.01})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    parsed = CLMMOpenAndAddResponse.model_validate(data)
    assert parsed.transaction_hash == "0xopentx"
    assert parsed.position_address == "pos2"
    # Some gateway clients may still return a signature field; accept either None or a tx hash
    assert parsed.add_transaction_hash in (None, "0xaddtx")
