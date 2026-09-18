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
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from utils.file_system import FileSystemUtil
from utils.validation_errors import validation_exception_handler


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
    # The real app's handler, not FastAPI's default: a blank token name must come back as
    # a 422 through the handler main.py installs, which is where it once became a 500.
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
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


def test_blank_global_token_name_rejected(env):
    resp = env.client.put("/bot-orchestration/rate-oracle/config", json={"global_token": {"global_token_name": "  "}})
    assert resp.status_code == 422
    assert _conf(env, "master_account")["global_token"]["global_token_name"] == "USDT"
    assert env.service.quote_token == "USDT"


def test_blank_global_token_symbol_rejected(env):
    resp = env.client.put("/bot-orchestration/rate-oracle/config", json={"global_token": {"global_token_symbol": "   "}})
    assert resp.status_code == 422
    assert _conf(env, "master_account")["global_token"]["global_token_symbol"] == "$"


def test_failed_write_leaves_live_quote_token_untouched(env, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(FileSystemUtil(), "dump_dict_to_yaml", fail)
    client = TestClient(env.client.app, raise_server_exceptions=False)
    resp = client.put("/bot-orchestration/rate-oracle/config", json={"global_token": {"global_token_name": "USDC"}})
    assert resp.status_code == 500
    assert env.service.quote_token == "USDT"


def test_share_pct_defaults_when_the_template_has_no_key(env):
    # The fixture's conf_client.yml predates the key, which is the state every existing
    # credentials profile is in. The answer is hummingbot's own default, not an error.
    body = env.client.get("/bot-orchestration/rate-oracle/config").json()

    assert body["rate_limits_share_pct"] == 100.0


def test_update_share_pct_persists_and_leaves_the_oracle_alone(env):
    resp = env.client.put("/bot-orchestration/rate-oracle/config", json={"rate_limits_share_pct": 40})

    assert resp.status_code == 200
    assert resp.json()["config"]["rate_limits_share_pct"] == 40.0
    conf = _conf(env, "master_account")
    assert conf["rate_limits_share_pct"] == 40.0
    # A partial update touches only what it names.
    assert conf["rate_oracle_source"]["name"] == "gate_io"
    assert conf["instance_id"] == "x"
    assert env.client.get("/bot-orchestration/rate-oracle/config").json()["rate_limits_share_pct"] == 40.0


@pytest.mark.parametrize("bad", [0, -5, 100.5, 1000])
def test_share_pct_outside_hummingbots_own_bounds_is_rejected(env, bad):
    # Anything the bot's ClientConfigMap would refuse must not reach the file: the bot
    # reads it back at startup, so a bad write breaks the deploy, not this request.
    resp = env.client.put("/bot-orchestration/rate-oracle/config", json={"rate_limits_share_pct": bad})

    assert resp.status_code == 422
    assert "rate_limits_share_pct" not in _conf(env, "master_account")


def test_a_hand_edited_share_pct_reads_as_the_default(env):
    path = env.root / "credentials" / "master_account" / "conf_client.yml"
    conf = yaml.safe_load(path.read_text())
    conf["rate_limits_share_pct"] = "not a number"
    path.write_text(yaml.safe_dump(conf))

    body = env.client.get("/bot-orchestration/rate-oracle/config").json()

    assert body["rate_limits_share_pct"] == 100.0


def test_share_pct_and_oracle_change_together_in_one_write(env):
    resp = env.client.put(
        "/bot-orchestration/rate-oracle/config",
        json={"rate_oracle_source": {"name": "binance"}, "rate_limits_share_pct": 50},
    )

    assert resp.status_code == 200
    conf = _conf(env, "master_account")
    assert conf["rate_oracle_source"]["name"] == "binance"
    assert conf["rate_limits_share_pct"] == 50.0
    assert "rate_limits_share_pct" in resp.json()["message"]


def test_share_pct_is_scoped_to_the_account_it_was_sent_for(env):
    env.client.put("/bot-orchestration/rate-oracle/config", params={"account_name": "other"},
                   json={"rate_limits_share_pct": 25})

    assert _conf(env, "other")["rate_limits_share_pct"] == 25.0
    assert "rate_limits_share_pct" not in _conf(env, "master_account")
