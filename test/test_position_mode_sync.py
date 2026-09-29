"""The API's position mode is the exchange's, and a switch it reports is one the exchange made.

The API creates perpetual connectors with no trading pairs. With none registered, a
position-mode switch cannot reach the exchange: bitget's and bybit's implementations loop
over the empty pair list and flip only the local mode, and bitget cannot even read the
account's mode until it has a pair to query with. So the API used to report HEDGE on
one-way bitget accounts, the switch endpoint reported success for switches that never
happened, and orders went out with the wrong position side. #210.

These drive the real bitget connector with only its HTTP layer replaced by a fake account.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from bidict import bidict
from fastapi import HTTPException
from hummingbot.connector.derivative.bitget_perpetual import bitget_perpetual_constants as CONSTANTS
from hummingbot.connector.derivative.bitget_perpetual.bitget_perpetual_derivative import BitgetPerpetualDerivative
from hummingbot.core.data_type.common import PositionMode

from services.perpetual_trading_service import PerpetualTradingService
from services.trading_service import AccountTradingInterface


class FakeBitgetAccount:
    def __init__(self, pos_mode: str, accept_switch: bool = True):
        self.pos_mode = pos_mode
        self.accept_switch = accept_switch
        self.switch_requests = []

    async def get(self, path_url, params=None, is_auth_required=False, **kwargs):
        assert path_url == CONSTANTS.ACCOUNT_INFO_ENDPOINT
        return {"code": CONSTANTS.RET_CODE_OK, "data": {"posMode": self.pos_mode}}

    async def post(self, path_url, data=None, is_auth_required=False, **kwargs):
        assert path_url == CONSTANTS.SET_POSITION_MODE_ENDPOINT
        self.switch_requests.append(data["posMode"])
        if not self.accept_switch:
            return {"code": "40920", "msg": "position exists"}
        self.pos_mode = data["posMode"]
        return {"code": CONSTANTS.RET_CODE_OK, "data": {}}


def _bitget(account: FakeBitgetAccount) -> BitgetPerpetualDerivative:
    connector = BitgetPerpetualDerivative(
        bitget_perpetual_api_key="k",
        bitget_perpetual_secret_key="s",
        bitget_perpetual_passphrase="p",
        trading_pairs=[],
    )
    connector._set_trading_pair_symbol_map(bidict({"BTCUSDT": "BTC-USDT"}))
    connector._api_get = account.get
    connector._api_post = account.post
    return connector


def _service(connector) -> PerpetualTradingService:
    async def provider(account_name, connector_name):
        return connector
    return PerpetualTradingService(provider)


async def test_first_pair_adopts_the_bitget_accounts_hedge_mode():
    account = FakeBitgetAccount("hedge_mode")
    connector = _bitget(account)
    market_data = MagicMock()
    market_data.initialize_order_book = AsyncMock(return_value=True)
    connector_service = MagicMock()
    connector_service.get_trading_connector = AsyncMock(return_value=connector)
    connector_service.get_account_connectors.return_value = {"bitget_perpetual": connector}
    interface = AccountTradingInterface(connector_service, market_data, "master")

    await interface.add_market("bitget_perpetual", "BTC-USDT")

    assert connector.position_mode == PositionMode.HEDGE
    assert account.switch_requests == []


async def test_switch_without_a_registered_pair_is_refused():
    account = FakeBitgetAccount("one_way_mode")
    connector = _bitget(account)

    with pytest.raises(HTTPException) as e:
        await _service(connector).set_position_mode("master", "bitget_perpetual", PositionMode.HEDGE)

    assert e.value.status_code == 400
    assert connector.position_mode == PositionMode.ONEWAY
    assert account.switch_requests == []


async def test_switch_with_an_unlisted_pair_is_refused_without_registering_it():
    connector = _bitget(FakeBitgetAccount("one_way_mode"))

    with pytest.raises(HTTPException) as e:
        await _service(connector).set_position_mode(
            "master", "bitget_perpetual", PositionMode.HEDGE, trading_pair="NOPE-USDT")

    assert e.value.status_code == 400
    assert connector.trading_pairs == []


async def test_switch_reaches_the_exchange_through_the_given_pair():
    account = FakeBitgetAccount("one_way_mode")
    connector = _bitget(account)

    result = await _service(connector).set_position_mode(
        "master", "bitget_perpetual", PositionMode.HEDGE, trading_pair="BTC-USDT")

    assert result["status"] == "success"
    assert account.pos_mode == "hedge_mode"
    assert connector.position_mode == PositionMode.HEDGE


async def test_switch_the_exchange_rejects_is_reported_as_rejected():
    account = FakeBitgetAccount("one_way_mode", accept_switch=False)
    connector = _bitget(account)

    with pytest.raises(HTTPException) as e:
        await _service(connector).set_position_mode(
            "master", "bitget_perpetual", PositionMode.HEDGE, trading_pair="BTC-USDT")

    assert e.value.status_code == 502
    assert account.switch_requests == ["hedge_mode"]
    assert connector.position_mode == PositionMode.ONEWAY
