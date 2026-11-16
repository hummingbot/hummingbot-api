"""A refused order must not come back as ``201 submitted``.

``connector.buy``/``sell`` return a *client* order id immediately; the venue
call happens in the connector's own task. Answering 201 from that id reported a
submission nothing had confirmed: a Derive order refused for a risk-universe
mismatch returned ``status: submitted`` with an id the venue never saw, and only
the container log carried the refusal.

These tests pin the outcome: refused -> 502 with the venue's reason, accepted ->
the id, still-unconfirmed -> the id (the order may well be live).
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState

from services.accounts_service import AccountsService

PAIR = "ETH-USDC"


class _Order:
    """In-flight order whose state resolves after ``pending_reads`` polls."""

    def __init__(self, final_state, reason=None, pending_reads=0):
        self._final = final_state
        self._pending = pending_reads
        self.current_state = (
            OrderState.PENDING_CREATE if pending_reads else final_state
        )
        self.last_failure_reason = reason

    def poll(self):
        if self._pending > 0:
            self._pending -= 1
            if self._pending == 0:
                self.current_state = self._final


class _TrackingDict(dict):
    """``in_flight_orders`` that advances the fake order on every read."""

    def get(self, key, default=None):
        order = super().get(key, default)
        if isinstance(order, _Order):
            order.poll()
            # One read is the current state; hand back what it was before the
            # poll so a test can drive PENDING -> OPEN across two reads.
        return order


def _connector(order, order_id="cid-1"):
    returns = {}

    def _buy(**kwargs):
        returns.update(kwargs)
        return order_id

    def _sell(**kwargs):
        returns.update(kwargs)
        return order_id

    return SimpleNamespace(
        trading_rules={
            PAIR: SimpleNamespace(
                min_order_size=Decimal("0.1"), min_notional_size=Decimal("0")
            )
        },
        supported_order_types=lambda: [OrderType.LIMIT],
        quantize_order_amount=lambda pair, amount: Decimal(amount),
        quantize_order_price=lambda pair, price: Decimal(price),
        in_flight_orders=_TrackingDict({order_id: order} if order else {}),
        buy=_buy,
        sell=_sell,
        calls=returns,
    )


def _service(connector):
    svc = AccountsService.__new__(AccountsService)
    svc.list_accounts = lambda: ["master_account"]
    svc._connector_service = SimpleNamespace(
        get_trading_connector=AsyncMock(return_value=connector)
    )
    svc.SUBMISSION_TIMEOUT_SEC = 0.05
    svc.SUBMISSION_POLL_SEC = 0.01
    return svc


async def _place(svc, trade_type=TradeType.BUY):
    return await svc.place_trade(
        account_name="master_account",
        connector_name="derive_perpetual",
        trading_pair=PAIR,
        trade_type=trade_type,
        amount=Decimal("0.1"),
        order_type=OrderType.LIMIT,
        price=Decimal("1660.88"),
        position_action=PositionAction.OPEN,
    )


@pytest.mark.asyncio
async def test_accepted_order_returns_its_id():
    order = _Order(OrderState.OPEN)
    svc = _service(_connector(order))
    assert await _place(svc) == "cid-1"


@pytest.mark.asyncio
async def test_refused_order_raises_with_the_venue_reason():
    """The whole point: a venue refusal is an error, not a successful submit."""
    reason = (
        "code=-32602 Invalid params (subaccount 121338 is in risk universe 1 but "
        "instrument SOL-PERP is in risk universe 3)"
    )
    order = _Order(OrderState.FAILED, reason=reason, pending_reads=2)
    svc = _service(_connector(order))

    with pytest.raises(HTTPException) as exc:
        await _place(svc)

    assert exc.value.status_code == 502
    assert "risk universe" in exc.value.detail
    assert "refused" in exc.value.detail


@pytest.mark.asyncio
async def test_refusal_without_a_reason_still_fails():
    order = _Order(OrderState.FAILED, reason=None)
    svc = _service(_connector(order))

    with pytest.raises(HTTPException) as exc:
        await _place(svc)

    assert exc.value.status_code == 502
    assert "rejected" in exc.value.detail


@pytest.mark.asyncio
async def test_unconfirmed_order_is_still_a_success():
    """Still PENDING_CREATE at the timeout: the order may well be live."""
    order = _Order(OrderState.OPEN, pending_reads=10_000)
    svc = _service(_connector(order))
    assert await _place(svc) == "cid-1"


@pytest.mark.asyncio
async def test_untracked_order_is_not_treated_as_a_failure():
    """A connector that does not track its orders must not start failing."""
    svc = _service(_connector(order=None))
    assert await _place(svc) == "cid-1"


class _LateTracker:
    """Tracker that reports nothing for the first ``missing_polls`` reads."""

    def __init__(self, order, missing_polls=2):
        self._order = order
        self._missing = missing_polls
        self._map = {}

    def poll_once(self):
        if self._missing > 0:
            self._missing -= 1
            if self._missing == 0:
                self._map = {"cid-1": self._order}

    @property
    def all_orders(self):
        # `_tracked_order` reads this attribute before calling .get(), so the
        # advance has to happen here for a test clock to work at all.
        self.poll_once()
        return self

    def get(self, key, default=None):
        return self._map.get(key, default)


class _LateConnector:
    """Connector whose tracker only learns about the order a few polls later."""

    def __init__(self, order):
        self.trading_rules = {
            PAIR: SimpleNamespace(
                min_order_size=Decimal("0.1"), min_notional_size=Decimal("0")
            )
        }
        self.supported_order_types = lambda: [OrderType.LIMIT]
        self.quantize_order_amount = lambda pair, amount: Decimal(amount)
        self.quantize_order_price = lambda pair, price: Decimal(price)
        self.in_flight_orders = {}
        self._order_tracker = _LateTracker(order)

    def buy(self, **kwargs):
        return "cid-1"

    def sell(self, **kwargs):
        return "cid-1"


@pytest.mark.asyncio
async def test_order_registered_late_is_still_checked():
    """The order is not tracked at submit time; its refusal must still be caught.

    This is the production shape: Derive registers the order with its tracker
    after the venue answers, so a first-poll miss used to be read as success.
    """
    order = _Order(OrderState.FAILED, reason="risk universe mismatch")
    svc = _service(_LateConnector(order))

    with pytest.raises(HTTPException) as exc:
        await _place(svc)

    assert exc.value.status_code == 502
    assert "risk universe" in exc.value.detail


@pytest.mark.asyncio
async def test_failed_order_that_left_the_in_flight_map_is_still_caught():
    """The tracker drops a failed order from ``in_flight_orders``; catch it anyway.

    This is the shape that matters in production: by the time the refusal is
    processed, the order is no longer "in flight", so a check that reads only
    that map sees nothing and reports success.
    """
    order = _Order(OrderState.FAILED, reason="risk universe mismatch")
    conn = _connector(order=None)  # nothing in in_flight_orders
    conn._order_tracker = SimpleNamespace(all_orders={"cid-1": order})
    svc = _service(conn)

    with pytest.raises(HTTPException) as exc:
        await _place(svc)

    assert exc.value.status_code == 502
    assert "risk universe" in exc.value.detail


@pytest.mark.asyncio
async def test_sell_path_checks_the_outcome_too():
    order = _Order(OrderState.FAILED, reason="margin insufficient")
    svc = _service(_connector(order))

    with pytest.raises(HTTPException) as exc:
        await _place(svc, trade_type=TradeType.SELL)

    assert exc.value.status_code == 502
    assert "margin insufficient" in exc.value.detail
