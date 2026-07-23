"""Tests for clearing the phantom LP position hold on a full unwind.

An LP slot's base is bought by an entry order_executor (stopped
keep_position=True, which records a PositionHold) and then deposited into the
pool. Neither the add-liquidity deposit nor the mostly-quote withdrawal is a
swap fill, so that hold's net_amount_base never comes back down and it lingers
as a phantom long after the position is closed on-chain. When the lp_executor
closes with keep_position=False the base is gone (returned as quote), so
``_handle_executor_completion`` clears the hold.

Run with: pytest test/test_position_hold_lp_clear.py -v
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("hummingbot")

from hummingbot.strategy_v2.models.executors import CloseType  # noqa: E402

from models.executors import PositionHold  # noqa: E402
from services.executor_service import ExecutorService  # noqa: E402

KEY_ARGS = ("master", "meteora/clmm", "BOP-SOL", "agent.lp_slot_operator_13")


def _svc_with_hold():
    """Minimal ExecutorService carrying one seeded hold and no DB."""
    svc = object.__new__(ExecutorService)
    svc.default_account = "master"
    svc.db_manager = None
    svc._positions_held = {}
    svc._log_capture = MagicMock()
    # Seed the phantom hold the entry swap would have created.
    key = svc._get_position_key(*KEY_ARGS)
    svc._positions_held[key] = PositionHold(
        trading_pair="BOP-SOL",
        connector_name="meteora/clmm",
        account_name="master",
        controller_id="agent.lp_slot_operator_13",
        buy_amount_base=1163,
    )
    # Stub the heavy completion collaborators.
    svc._persist_executor_completed = AsyncMock()
    svc._aggregate_position_hold = AsyncMock()
    return svc, key


def _completion(svc, executor_id, close_type, executor_type):
    svc._active_executors = {
        executor_id: SimpleNamespace(close_type=close_type, is_closed=True)
    }
    svc._executor_metadata = {
        executor_id: {
            "executor_type": executor_type,
            "account_name": "master",
            "connector_name": "meteora/clmm",
            "trading_pair": "BOP-SOL",
            "controller_id": "agent.lp_slot_operator_13",
        }
    }
    return svc._handle_executor_completion(executor_id)


@pytest.mark.asyncio
async def test_lp_clean_unwind_clears_phantom_hold():
    """A keep_position=False LP close (EARLY_STOP) clears the stale hold."""
    svc, key = _svc_with_hold()
    await _completion(svc, "e_lp", CloseType.EARLY_STOP, "lp_executor")
    assert key not in svc._positions_held
    svc._aggregate_position_hold.assert_not_called()


@pytest.mark.asyncio
async def test_lp_failed_open_keeps_hold():
    """A FAILED LP open leaves the swapped base in the wallet as a real spot
    position -- the hold must NOT be cleared."""
    svc, key = _svc_with_hold()
    await _completion(svc, "e_lp", CloseType.FAILED, "lp_executor")
    assert key in svc._positions_held


@pytest.mark.asyncio
async def test_lp_position_hold_close_aggregates_not_clears():
    """keep_position=True (POSITION_HOLD) still aggregates, never the clear path."""
    svc, key = _svc_with_hold()
    await _completion(svc, "e_lp", CloseType.POSITION_HOLD, "lp_executor")
    svc._aggregate_position_hold.assert_called_once()
    assert key in svc._positions_held  # aggregate stubbed; clear not invoked


@pytest.mark.asyncio
async def test_non_lp_executor_does_not_clear_hold():
    """The clear path is LP-specific: an order_executor close leaves holds alone."""
    svc, key = _svc_with_hold()
    await _completion(svc, "e_ord", CloseType.EARLY_STOP, "order_executor")
    assert key in svc._positions_held
