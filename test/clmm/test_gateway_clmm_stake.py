import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routers import clmm_connector
from deps import get_accounts_service, get_database_manager
from models import CLMMStakePositionResponse


class DummyGatewayClient:
    def __init__(self, stake_result=None):
        # allow passing an empty dict to simulate missing signature
        self._stake_result = {"signature": "0xstaketx"} if stake_result is None else stake_result

    async def clmm_stake_position(self, **kwargs):
        return self._stake_result

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
    client = TestClient(app)
    return client


def setup_dependencies(client_app, accounts_service, db_manager):
    client_app.app.dependency_overrides[get_accounts_service] = lambda: accounts_service
    client_app.app.dependency_overrides[get_database_manager] = lambda: db_manager


def test_stake_success(monkeypatch, client):
    gateway_client = DummyGatewayClient(stake_result={"signature": "0xstaketx", "data": {"fee": 0.001}})
    accounts_service = DummyAccountsService(gateway_client)
    db_manager = DummyDBManager()

    setup_dependencies(client, accounts_service, db_manager)

    payload = {
        "connector": "pancakeswap",
        "network": "bsc-mainnet",
        "position_address": "pos123",
    }

    resp = client.post("/gateway/clmm/stake", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    parsed = CLMMStakePositionResponse.model_validate(data)
    assert parsed.transaction_hash == "0xstaketx"
    assert parsed.position_address == "pos123"


def test_stake_missing_hash(monkeypatch, client):
    gateway_client = DummyGatewayClient(stake_result={})
    accounts_service = DummyAccountsService(gateway_client)
    db_manager = DummyDBManager()

    setup_dependencies(client, accounts_service, db_manager)

    payload = {
        "connector": "pancakeswap",
        "network": "bsc-mainnet",
        "position_address": "pos456",
    }

    resp = client.post("/gateway/clmm/stake", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    parsed = CLMMStakePositionResponse.model_validate(data)
    assert parsed.position_address == "pos456"
    assert parsed.transaction_hash in (None, "0xstaketx")
