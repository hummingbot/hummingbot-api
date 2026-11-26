"""The API's position mode is the exchange's, and a switch it reports is one the exchange made.

The API creates perpetual connectors with no trading pairs. With none registered, a
position-mode switch cannot reach the exchange: bitget's and bybit's implementations loop
over the empty pair list and flip only the local mode, and bitget cannot even read the
account's mode until it has a pair to query with. So the API used to report HEDGE on
one-way bitget accounts, the switch endpoint reported success for switches that never
happened, and orders went out with the wrong position side. #210.

hummingbot reaches the account through the connector's registered pairs, so the API lends
the connector a listed pair for the call and takes it back; the caller is not asked for one.

These drive the real connectors with only their HTTP layer replaced by a fake account.
"""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from bidict import bidict
from fastapi import HTTPException
from hummingbot.connector.derivative.binance_perpetual.binance_perpetual_derivative import BinancePerpetualDerivative
from hummingbot.connector.derivative.bitget_perpetual import bitget_perpetual_constants as CONSTANTS
from hummingbot.connector.derivative.bitget_perpetual.bitget_perpetual_derivative import BitgetPerpetualDerivative
from hummingbot.connector.derivative.bybit_perpetual.bybit_perpetual_derivative import BybitPerpetualDerivative
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode, TradeType

from services.accounts_service import AccountsService
from services.perpetual_trading_service import PerpetualTradingService, sync_position_mode
from services.trading_service import AccountTradingInterface


class FakeBitgetAccount:
    def __init__(self, pos_mode: str, accept_switch: bool = True):
        self.pos_mode = pos_mode
        self.accept_switch = accept_switch
        self.switch_requests = []
        self.reads = []
        self.orders = []

    async def get(self, path_url, params=None, is_auth_required=False, **kwargs):
        assert path_url == CONSTANTS.ACCOUNT_INFO_ENDPOINT
        self.reads.append(params["productType"])
        return {"code": CONSTANTS.RET_CODE_OK, "data": {"posMode": self.pos_mode}}

    async def post(self, path_url, data=None, is_auth_required=False, **kwargs):
        if path_url == CONSTANTS.PLACE_ORDER_ENDPOINT:
            self.orders.append(data)
            return {"code": CONSTANTS.RET_CODE_OK, "data": {"orderId": "1"}}
        assert path_url == CONSTANTS.SET_POSITION_MODE_ENDPOINT
        self.switch_requests.append(data["posMode"])
        if not self.accept_switch:
            return {"code": "40920", "msg": "position exists"}
        self.pos_mode = data["posMode"]
        return {"code": CONSTANTS.RET_CODE_OK, "data": {}}


def _rule(trading_pair: str) -> TradingRule:
    return TradingRule(
        trading_pair, min_order_size=Decimal("0.001"), min_price_increment=Decimal("0.1"),
        min_base_amount_increment=Decimal("0.001"), min_notional_size=Decimal("5"))


def _bitget(account: FakeBitgetAccount) -> BitgetPerpetualDerivative:
    """A connector as the API creates it: symbols and rules loaded, no pair registered."""
    connector = BitgetPerpetualDerivative(
        bitget_perpetual_api_key="k",
        bitget_perpetual_secret_key="s",
        bitget_perpetual_passphrase="p",
        trading_pairs=[],
    )
    connector._set_trading_pair_symbol_map(bidict({"BTCUSDT": "BTC-USDT", "BTCPERP": "BTC-USDC"}))
    connector._trading_rules.update({"BTC-USDC": _rule("BTC-USDC"), "BTC-USDT": _rule("BTC-USDT")})
    connector._api_get = account.get
    connector._api_post = account.post
    return connector


def _service(connector) -> PerpetualTradingService:
    async def provider(account_name, connector_name):
        return connector
    return PerpetualTradingService(provider)


def _no_pair_left_behind(connector) -> bool:
    # The order book tracker subscribes to this same list
    return connector.trading_pairs == [] and connector.order_book_tracker._trading_pairs == []


async def test_hedge_account_is_adopted_with_no_pair_registered():
    account = FakeBitgetAccount("hedge_mode")
    connector = _bitget(account)

    await sync_position_mode(connector)

    assert connector.position_mode == PositionMode.HEDGE
    assert account.reads == ["USDT-FUTURES"]
    assert account.switch_requests == []
    assert _no_pair_left_behind(connector)


async def test_direct_order_on_a_fresh_connector_goes_out_in_the_accounts_hedge_mode():
    account = FakeBitgetAccount("hedge_mode")
    connector = _bitget(account)
    await sync_position_mode(connector)  # as connector creation does
    service = AccountsService.__new__(AccountsService)
    service.list_accounts = lambda: ["master"]
    service._connector_service = MagicMock()
    service._connector_service.get_trading_connector = AsyncMock(return_value=connector)

    await service.place_trade("master", "bitget_perpetual", "BTC-USDT", TradeType.BUY, Decimal("0.01"),
                              OrderType.LIMIT, Decimal("60000"), PositionAction.OPEN)
    for _ in range(100):
        if account.orders:
            break
        await asyncio.sleep(0.01)

    assert account.orders[0]["tradeSide"] == "open"
    assert _no_pair_left_behind(connector)


async def test_adding_a_market_reads_the_mode_again_after_a_read_that_failed():
    account = FakeBitgetAccount("hedge_mode")
    connector = _bitget(account)
    connector._api_get = AsyncMock(side_effect=IOError("timeout"))
    await sync_position_mode(connector)
    assert connector.position_mode == PositionMode.ONEWAY
    connector._api_get = account.get

    async def initialize_order_book(connector_name, trading_pair, **kwargs):
        # The tracker's pair list is the connector's own, so the order book registers the pair
        connector.order_book_tracker._trading_pairs.append(trading_pair)
        return True
    market_data = MagicMock()
    market_data.initialize_order_book = initialize_order_book
    connector_service = MagicMock()
    connector_service.get_trading_connector = AsyncMock(return_value=connector)
    connector_service.get_account_connectors.return_value = {"bitget_perpetual": connector}
    interface = AccountTradingInterface(connector_service, market_data, "master")

    await interface.add_market("bitget_perpetual", "BTC-USDT")

    assert connector.position_mode == PositionMode.HEDGE
    assert connector.trading_pairs == ["BTC-USDT"]


async def test_get_reports_the_exchanges_mode_not_a_cached_one():
    account = FakeBitgetAccount("one_way_mode")
    connector = _bitget(account)
    await sync_position_mode(connector)
    account.pos_mode = "hedge_mode"  # switched in the exchange UI

    result = await _service(connector).get_position_mode("master", "bitget_perpetual")

    assert result["position_mode"] == "HEDGE"
    assert _no_pair_left_behind(connector)


async def test_switch_reaches_the_exchange_with_no_pair_registered_or_given():
    account = FakeBitgetAccount("one_way_mode")
    connector = _bitget(account)

    result = await _service(connector).set_position_mode("master", "bitget_perpetual", PositionMode.HEDGE)

    assert result["status"] == "success"
    assert account.pos_mode == "hedge_mode"
    assert connector.position_mode == PositionMode.HEDGE
    assert _no_pair_left_behind(connector)


async def test_a_given_pair_selects_the_product_type_and_is_not_registered():
    account = FakeBitgetAccount("one_way_mode")
    connector = _bitget(account)

    await _service(connector).set_position_mode(
        "master", "bitget_perpetual", PositionMode.HEDGE, trading_pair="BTC-USDC")

    assert account.reads == ["USDC-FUTURES"]
    assert account.switch_requests == ["hedge_mode"]
    assert _no_pair_left_behind(connector)


async def test_switch_with_an_unlisted_pair_is_refused():
    account = FakeBitgetAccount("one_way_mode")
    connector = _bitget(account)

    with pytest.raises(HTTPException) as e:
        await _service(connector).set_position_mode(
            "master", "bitget_perpetual", PositionMode.HEDGE, trading_pair="NOPE-USDT")

    assert e.value.status_code == 400
    assert account.switch_requests == []
    assert _no_pair_left_behind(connector)


async def test_switch_the_exchange_rejects_is_reported_as_rejected():
    account = FakeBitgetAccount("one_way_mode", accept_switch=False)
    connector = _bitget(account)

    with pytest.raises(HTTPException) as e:
        await _service(connector).set_position_mode("master", "bitget_perpetual", PositionMode.HEDGE)

    assert e.value.status_code == 502
    assert account.switch_requests == ["hedge_mode"]
    assert connector.position_mode == PositionMode.ONEWAY
    assert _no_pair_left_behind(connector)


async def test_account_wide_exchange_switches_with_no_pair():
    connector = BinancePerpetualDerivative(
        binance_perpetual_api_key="k", binance_perpetual_api_secret="s", trading_pairs=[])
    connector._trading_rules["BTC-USDT"] = _rule("BTC-USDT")
    exchange = {"dualSidePosition": False}

    async def get(path_url, **kwargs):
        return dict(exchange)

    async def post(path_url, data=None, **kwargs):
        exchange.update(data)
        return {"msg": "success", "code": 200}
    connector._api_get, connector._api_post = get, post

    await _service(connector).set_position_mode("master", "binance_perpetual", PositionMode.HEDGE)

    assert exchange["dualSidePosition"] is True
    assert connector.position_mode == PositionMode.HEDGE
    assert _no_pair_left_behind(connector)


def _bybit(switched: list) -> BybitPerpetualDerivative:
    connector = BybitPerpetualDerivative(
        bybit_perpetual_api_key="k", bybit_perpetual_secret_key="s", trading_pairs=[])
    connector._set_trading_pair_symbol_map(bidict({"BTCUSDT": "BTC-USDT"}))
    connector._trading_rules["BTC-USDT"] = _rule("BTC-USDT")

    async def post(path_url, data=None, **kwargs):
        switched.append(data["symbol"])
        return {"retCode": 0, "retMsg": "OK"}
    connector._api_post = post
    return connector


async def test_per_symbol_exchange_is_not_switched_through_a_symbol_nobody_named():
    switched = []
    connector = _bybit(switched)

    with pytest.raises(HTTPException) as e:
        await _service(connector).set_position_mode("master", "bybit_perpetual", PositionMode.HEDGE)

    assert e.value.status_code == 400
    assert switched == []
    assert connector.position_mode == PositionMode.ONEWAY


async def test_per_symbol_exchange_switches_the_symbol_it_is_given():
    switched = []
    connector = _bybit(switched)

    await _service(connector).set_position_mode(
        "master", "bybit_perpetual", PositionMode.HEDGE, trading_pair="BTC-USDT")

    assert switched == ["BTCUSDT"]
    assert connector.position_mode == PositionMode.HEDGE
    assert _no_pair_left_behind(connector)
