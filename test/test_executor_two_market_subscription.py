"""POST /executors subscribes both legs of a two-market executor (#158).

`create_executor` read only the top-level connector_name/trading_pair before preparing the
market. xemm_executor and arbitrage_executor carry buying_market and selling_market instead
and no top-level pair, so neither leg's order book was subscribed and every tick raised
"No order book exists for '<pair>'". Strategy mode never hit this: StrategyV2ConfigBase
subscribes the markets at init; the REST path has to do it in create_executor.

Run with: pytest test/test_executor_two_market_subscription.py -v
"""
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from hummingbot.strategy_v2.executors.arbitrage_executor.data_types import ArbitrageExecutorConfig
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.executors.xemm_executor.data_types import XEMMExecutorConfig
from hummingbot.core.data_type.common import TradeType

from services.executor_service import ExecutorService


def _arbitrage_config() -> ArbitrageExecutorConfig:
    return ArbitrageExecutorConfig(
        timestamp=1.0,
        buying_market=ConnectorPair(connector_name="binance", trading_pair="BTC-USDT"),
        selling_market=ConnectorPair(connector_name="kucoin", trading_pair="BTC-USDT"),
        order_amount=Decimal("0.01"),
        min_profitability=Decimal("0.005"),
    )


def test_a_two_market_executor_lists_both_legs():
    request = {"type": "arbitrage_executor"}  # no top-level connector_name / trading_pair

    markets = ExecutorService._executor_markets(request, _arbitrage_config())

    assert markets == [("binance", "BTC-USDT"), ("kucoin", "BTC-USDT")]


def test_xemm_lists_both_legs_too():
    config = XEMMExecutorConfig(
        timestamp=1.0,
        buying_market=ConnectorPair(connector_name="binance", trading_pair="ETH-USDT"),
        selling_market=ConnectorPair(connector_name="okx", trading_pair="ETH-USDT"),
        maker_side=TradeType.BUY,
        order_amount=Decimal("0.1"),
        min_profitability=Decimal("0.002"),
        target_profitability=Decimal("0.003"),
        max_profitability=Decimal("0.01"),
    )

    assert ExecutorService._executor_markets({"type": "xemm_executor"}, config) == [
        ("binance", "ETH-USDT"),
        ("okx", "ETH-USDT"),
    ]


def test_a_single_market_executor_is_unchanged():
    request = {"type": "position_executor", "connector_name": "binance_perpetual", "trading_pair": "BTC-USDT"}
    config = PositionExecutorConfig(
        timestamp=1.0,
        connector_name="binance_perpetual",
        trading_pair="BTC-USDT",
        side=TradeType.BUY,
        amount=Decimal("0.01"),
    )

    assert ExecutorService._executor_markets(request, config) == [("binance_perpetual", "BTC-USDT")]


@pytest.mark.asyncio
async def test_create_executor_prepares_both_legs(monkeypatch):
    """The flow around create_executor is stubbed at its seams; the market preparation is real."""
    service = ExecutorService.__new__(ExecutorService)
    service.default_account = "master_account"

    trading_interface = type("Interface", (), {})()
    trading_interface.current_timestamp = 1.0
    trading_interface.add_market = AsyncMock()
    trading_interface.ensure_connector = AsyncMock()
    monkeypatch.setattr(service, "_get_trading_interface", lambda account: trading_interface)

    typed_config = _arbitrage_config()
    monkeypatch.setattr(
        service,
        "_validate_executor_config",
        lambda config, default_timestamp: (object, type(typed_config), typed_config),
    )
    executor = type("Executor", (), {"is_closed": False, "status": type("S", (), {"name": "RUNNING"})()})()
    monkeypatch.setattr(service, "_instantiate_and_register", lambda *args, **kwargs: ("executor-1", executor))
    monkeypatch.setattr(service, "_persist_executor_created", AsyncMock())

    result = await service.create_executor({
        "type": "arbitrage_executor",
        "buying_market": {"connector_name": "binance", "trading_pair": "BTC-USDT"},
        "selling_market": {"connector_name": "kucoin", "trading_pair": "BTC-USDT"},
        "order_amount": "0.01",
        "min_profitability": "0.005",
    })

    assert result["executor_id"] == "executor-1"
    assert [call.args for call in trading_interface.add_market.await_args_list] == [
        ("binance", "BTC-USDT"),
        ("kucoin", "BTC-USDT"),
    ]
