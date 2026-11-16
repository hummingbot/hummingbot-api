"""Cancel an order by its venue id when the in-flight tracker no longer holds it.

``cancel_order`` reaches orders through ``connector.in_flight_orders``, an
in-memory map owned by the running process. A bot that is stopped, crashes or is
archived while orders are resting leaves those orders live at the venue and
unreachable through the API -- there was no fallback path at all.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from services.accounts_service import AccountsService


def _service(connector):
    svc = AccountsService.__new__(AccountsService)
    svc.get_connector_instance = AsyncMock(return_value=connector)
    return svc


def _connector(in_flight=None, canceller=None, cancel=None):
    """A SimpleNamespace, so an absent method really is absent (unlike MagicMock)."""
    conn = SimpleNamespace(in_flight_orders=dict(in_flight or {}))
    if canceller is not None:
        conn.cancel_by_exchange_order_id = canceller
    if cancel is not None:
        conn.cancel = cancel
    return conn


@pytest.mark.asyncio
async def test_untracked_order_is_cancelled_at_the_venue():
    """The whole point: no tracker entry, still cancellable."""
    canceller = AsyncMock(return_value="106553233")
    svc = _service(_connector(canceller=canceller))

    out = await svc.cancel_order_by_exchange_id("master_account", "xrpl", "106553233")

    assert out["order_id"] == "106553233"
    assert out["via"] == "venue"
    assert out["status"] == "submitted"
    canceller.assert_awaited_once()
    assert canceller.await_args.args[1] == "106553233"


@pytest.mark.asyncio
async def test_a_tracked_order_still_uses_the_existing_path():
    """Same call is a no-op change for orders the process still owns."""
    order = MagicMock()
    order.exchange_order_id = "106553233"
    order.client_order_id = "hbot-123"
    cancel = MagicMock(return_value="hbot-123")
    svc = _service(_connector(in_flight={"hbot-123": order}, cancel=cancel))

    out = await svc.cancel_order_by_exchange_id("master_account", "xrpl", "106553233")

    assert out["order_id"] == "hbot-123"
    # connector.cancel only schedules the work, so the result must not claim more
    assert out["via"] == "tracker"
    assert out["status"] == "initiated"
    cancel.assert_called_once()


@pytest.mark.asyncio
async def test_a_connector_without_the_capability_says_so():
    svc = _service(_connector())
    with pytest.raises(HTTPException) as err:
        await svc.cancel_order_by_exchange_id("master_account", "binance", "123")
    assert err.value.status_code == 501


@pytest.mark.asyncio
async def test_a_venue_rejection_is_a_500_with_the_cause():
    async def boom(_pair, _id):
        raise ValueError("OfferCancel for sequence 1 failed: tecUNFUNDED")

    svc = _service(_connector(canceller=boom))
    with pytest.raises(HTTPException) as err:
        await svc.cancel_order_by_exchange_id("master_account", "xrpl", "1")
    assert err.value.status_code == 500
    assert "tecUNFUNDED" in err.value.detail


@pytest.mark.asyncio
async def test_the_route_never_says_cancelled_for_a_tracked_order():
    """A scheduled cancel must not read as venue confirmation (review feedback)."""
    from routers.trading import cancel_order_by_exchange_id as route

    class _Tracked:
        async def cancel_order_by_exchange_id(self, **_kw):
            return {"order_id": "hbot-123", "via": "tracker", "status": "initiated"}

    class _Venue:
        async def cancel_order_by_exchange_id(self, **_kw):
            return {"order_id": "106553233", "via": "venue", "status": "submitted"}

    tracked = await route("master_account", "xrpl", "123", None, _Tracked())
    venue = await route("master_account", "xrpl", "106553233", None, _Venue())

    assert tracked["status"] == "initiated"
    assert "initiated" in tracked["message"]
    assert "cancelled" not in tracked["message"].lower()
    assert venue["status"] == "submitted"
    assert "submitted to the venue" in venue["message"]
