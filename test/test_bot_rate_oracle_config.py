"""
Tests for the bot rate oracle config endpoints.

The API prices through its own ticker pool, but deployed bots still run hummingbot's
RateOracle from the conf_client.yml copied out of their credentials profile. Removing
these endpoints left every bot stuck on the template's source; pinned here: the config
is read and persisted per account, invalid sources and names are rejected, and changing
master_account's global token also moves the API's own quote token.

Run with: pytest test/test_bot_rate_oracle_config.py -v
"""
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils.file_system import FileSystemUtil


@pytest.fixture
def env(tmp_path, monkeypatch):
    from deps import get_market_data_service
    from routers import bot_rate_oracle

    for account in ("master_account", "other"):
        (tmp_path / "credentials" / account).mkdir(parents=True)
        (tmp_path / "credentials" / account / "conf_client.yml").write_text(yaml.safe_dump({
            "instance_id": "x",
            "rate_oracle_source": {"name": "gate_io"},
            "global_token": {"global_token_name": "USDT", "global_token_symbol": "$"},
        }))
    monkeypatch.setattr(FileSystemUtil(), "base_path", str(tmp_path))

    service = SimpleNamespace(quote_token="USDT")
    app = FastAPI()
    app.include_router(bot_rate_oracle.router)
    app.dependency_overrides[get_market_data_service] = lambda: service
    return SimpleNamespace(client=TestClient(app), service=service, root=tmp_path)


def _conf(env, account):
    return yaml.safe_load((env.root / "credentials" / account / "conf_client.yml").read_text())


def test_get_config_reads_account_file(env):
    resp = env.client.get("/bot-orchestration/rate-oracle/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rate_oracle_source"]["name"] == "gate_io"
    assert body["global_token"]["global_token_name"] == "USDT"
    assert "binance" in body["available_sources"]


def test_update_source_persists_and_keeps_other_keys(env):
    resp = env.client.put("/bot-orchestration/rate-oracle/config", json={"rate_oracle_source": {"name": "binance"}})
    assert resp.status_code == 200
    conf = _conf(env, "master_account")
    assert conf["rate_oracle_source"]["name"] == "binance"
    assert conf["instance_id"] == "x"


def test_update_other_account_does_not_touch_api_quote_token(env):
    resp = env.client.put(
        "/bot-orchestration/rate-oracle/config",
        params={"account_name": "other"},
        json={"global_token": {"global_token_name": "USDC"}},
    )
    assert resp.status_code == 200
    assert _conf(env, "other")["global_token"] == {"global_token_name": "USDC", "global_token_symbol": "$"}
    assert _conf(env, "master_account")["global_token"]["global_token_name"] == "USDT"
    assert env.service.quote_token == "USDT"


def test_master_global_token_updates_api_quote_token(env):
    env.client.put("/bot-orchestration/rate-oracle/config", json={"global_token": {"global_token_name": "USDC"}})
    assert env.service.quote_token == "USDC"


def test_invalid_source_rejected(env):
    resp = env.client.put("/bot-orchestration/rate-oracle/config", json={"rate_oracle_source": {"name": "nope"}})
    assert resp.status_code == 400
    assert _conf(env, "master_account")["rate_oracle_source"]["name"] == "gate_io"


def test_unknown_or_unsafe_account(env):
    assert env.client.get("/bot-orchestration/rate-oracle/config", params={"account_name": "missing"}).status_code == 404
    assert env.client.get("/bot-orchestration/rate-oracle/config", params={"account_name": "../x"}).status_code == 400
