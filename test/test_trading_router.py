import ast
import math
import sys
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import SimpleNamespace


SOURCE_PATH = Path("/root/hummingbot-stack/upstream/hummingbot-api/routers/trading.py")


class OrderState(Enum):
    PENDING_CREATE = 0
    OPEN = 1
    PENDING_CANCEL = 2
    CANCELED = 3
    PARTIALLY_FILLED = 4
    FILLED = 5
    FAILED = 6
    PENDING_APPROVAL = 7
    APPROVED = 8
    CREATED = 9
    COMPLETED = 10


def _load_standardizer():
    source = SOURCE_PATH.read_text()
    module = ast.parse(source)
    target = None
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_standardize_in_flight_order_response":
            target = node
            break
    if target is None:
        raise AssertionError("standardizer function not found")

    func_source = ast.get_source_segment(source, target)
    fake_module = SimpleNamespace(OrderState=OrderState)
    sys.modules.setdefault("hummingbot", SimpleNamespace())
    sys.modules.setdefault("hummingbot.core", SimpleNamespace())
    sys.modules.setdefault("hummingbot.core.data_type", SimpleNamespace())
    sys.modules["hummingbot.core.data_type.in_flight_order"] = fake_module
    namespace = {"math": math}
    exec(func_source, namespace)
    return namespace["_standardize_in_flight_order_response"]


def test_standardize_in_flight_order_response_handles_nan_timestamps():
    standardize = _load_standardizer()
    order = SimpleNamespace(
        client_order_id="cid-1",
        trading_pair="BTC-USD",
        trade_type=SimpleNamespace(name="BUY"),
        order_type=SimpleNamespace(name="LIMIT"),
        amount=Decimal("0.02"),
        price=Decimal("40000"),
        current_state=OrderState.PENDING_CREATE,
        executed_amount_base=Decimal("0"),
        last_executed_price=None,
        cumulative_fee_paid_quote=None,
        creation_timestamp=float("nan"),
        last_update_timestamp=float("nan"),
        exchange_order_id=None,
    )

    result = standardize(
        order=order,
        account_name="master_account",
        connector_name="hyperliquid_perpetual_testnet",
    )

    assert result["created_at"] is None
    assert result["updated_at"] is None
    assert result["price"] == 40000.0
    assert result["filled_amount"] == 0
