"""Restart must reattach the same orders, not open a second trade.

The safety rules live here, without the exchange: a missing checkpoint is not
resumed, a grid does not guess between two levels, and an LP open that already
started is not minted again.
"""
from types import SimpleNamespace

from utils.executor_checkpoint import (
    can_resume,
    capture_executor,
    capture_tracked,
    grid_resume_plan,
    has_exposure,
    note_filled,
    note_gone,
    note_placed,
    parse_checkpoint,
    recent_order_ids,
    remember_order_now,
    seat_known_opens,
    would_place_at_touch,
)


def _level(level_id, price, open_order=None):
    return {
        "id": level_id,
        "price": price,
        "amount_quote": "10",
        "take_profit": "0.01",
        "side": "BUY",
        "open_order_type": "LIMIT",
        "take_profit_order_type": "LIMIT",
        "open_order": open_order,
        "close_order": None,
    }


def _grid(levels):
    return {"version": 1, "type": "grid_executor", "levels": levels, "close_order": None}


def test_missing_checkpoint_is_not_resumed():
    assert can_resume(None) == "no checkpoint"
    assert parse_checkpoint(None) is None
    assert parse_checkpoint("not json") is None


def test_capture_keeps_the_order_id_and_ignores_an_empty_slot():
    tracked = SimpleNamespace(order_id="abc", order=None)
    assert capture_tracked(tracked)["order_id"] == "abc"
    assert capture_tracked(SimpleNamespace(order_id=None, order=None)) is None

    level = SimpleNamespace(
        id="L0", price="100", amount_quote="10", take_profit="0.01",
        side=SimpleNamespace(name="BUY"),
        open_order_type=SimpleNamespace(name="LIMIT"),
        take_profit_order_type=SimpleNamespace(name="LIMIT"),
        active_open_order=tracked,
        active_close_order=None,
    )
    executor = SimpleNamespace(grid_levels=[level], _close_order=None)
    body = capture_executor(executor, "grid_executor")
    assert body["levels"][0]["open_order"]["order_id"] == "abc"
    assert has_exposure(body) is True


def test_grid_does_not_die_when_one_order_matches_two_levels():
    checkpoint = _grid([_level("L0", "100"), _level("L1", "100")])
    live = [{"order_id": "x", "price": "100", "is_open": True}]
    assert can_resume(checkpoint, live) is None
    plan = grid_resume_plan(checkpoint, live, market_price="118", side="BUY")
    assert plan["touch_budget"] == 1
    assert plan["ambiguous_ids"] == ["x"]


def test_grid_allows_an_order_that_matches_one_empty_level():
    checkpoint = _grid([_level("L0", "100"), _level("L1", "110")])
    live = [{"order_id": "x", "price": "100", "is_open": True}]
    assert can_resume(checkpoint, live) is None


def test_empty_checkpoint_does_not_start_beside_an_unnamed_open_order():
    checkpoint = {"version": 1, "type": "order_executor", "order": None}
    live = [{"order_id": "x", "price": "100", "is_open": True}]
    assert can_resume(checkpoint, live) is not None
    assert has_exposure(checkpoint) is False


def test_cleared_slot_keeps_the_id_until_the_exchange_confirms_cancel():
    """The grid drops the slot when it replaces an order. The id stays until cancel."""
    level = SimpleNamespace(
        id="L0", price="100", amount_quote="10", take_profit="0.01",
        side=SimpleNamespace(name="BUY"),
        open_order_type=SimpleNamespace(name="LIMIT"),
        take_profit_order_type=SimpleNamespace(name="LIMIT"),
        active_open_order=SimpleNamespace(order_id="abc", order=None),
        active_close_order=None,
    )
    executor = SimpleNamespace(grid_levels=[level], _close_order=None, connectors={})
    note_placed(executor, "abc")
    capture_executor(executor, "grid_executor")
    level.active_open_order = None
    body = capture_executor(executor, "grid_executor")
    assert body["levels"][0]["open_order"]["order_id"] == "abc"

    note_gone(executor, "abc")
    body = capture_executor(executor, "grid_executor")
    assert body["levels"][0]["open_order"] is None


def test_fill_is_recorded_on_the_event_not_the_next_tick():
    executor = SimpleNamespace(grid_levels=[], _close_order=None, connectors={})
    note_placed(executor, "abc")
    note_filled(executor, "abc", {"client_order_id": "abc", "last_state": "filled"})
    assert executor._order_ledger["abc"]["filled"] is True
    assert executor._order_ledger["abc"]["order"]["client_order_id"] == "abc"


def test_unrecognized_touch_order_keeps_the_grid_and_adds_no_second():
    """The grid comes back. The order already at the market counts as the one extra."""
    checkpoint = _grid([_level("L0", "120"), _level("L1", "125")])
    live = [{"order_id": "touch", "price": "118.18", "is_open": True}]
    assert can_resume(checkpoint, live) is None
    plan = grid_resume_plan(checkpoint, live, market_price="118.20", side="BUY")
    assert plan["touch_budget"] == 0
    assert "no new order is placed there" in plan["notice"]
    assert would_place_at_touch("125", "118.20", "BUY") is True
    assert would_place_at_touch("100", "118.20", "BUY") is False


def test_foreign_order_is_not_seated_by_price():
    levels = [_level("L0", "100")]
    foreign = {"order_id": "other", "price": "100", "is_open": True, "side": "BUY"}
    assert seat_known_opens(levels, [foreign], allowed_ids=set()) == set()
    assert levels[0]["open_order"] is None
    plan = grid_resume_plan(_grid(levels), [foreign], market_price="90", side="BUY")
    assert "100" in plan["blocked_prices"]


def test_known_order_seats_on_one_level_and_not_on_two():
    one = [_level("L0", "100"), _level("L1", "110")]
    own = {"order_id": "mine", "price": "100", "is_open": True, "side": "BUY"}
    assert seat_known_opens(one, [own], allowed_ids={"mine"}) == {"mine"}
    assert one[0]["open_order"]["order_id"] == "mine"

    two = [_level("L0", "100"), _level("L1", "100")]
    assert seat_known_opens(two, [own], allowed_ids={"mine"}) == set()
    assert two[0]["open_order"] is None
    plan = grid_resume_plan(_grid(two), [own], market_price="90", side="BUY")
    assert can_resume(_grid(two), [own]) is None
    assert plan["ambiguous_ids"] == ["mine"]
    assert plan["touch_budget"] == 1


def test_unattached_retained_id_still_blocks_the_current_price():
    checkpoint = _grid([_level("L0", "120"), _level("L1", "125")])
    checkpoint["retained_orders"] = [{"order_id": "late", "filled": False}]
    live = [{"order_id": "late", "price": "118.18", "is_open": True, "side": "BUY"}]
    plan = grid_resume_plan(checkpoint, live, market_price="118.20", side="BUY")
    assert plan["touch_budget"] == 0
    assert "118.18" in plan["blocked_prices"]


def test_order_ledger_drops_ids_older_than_the_keep_window(tmp_path, monkeypatch):
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(tmp_path / "ledger.jsonl"))
    remember_order_now("ex", "new")
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        '{"executor_id": "ex", "order_id": "old", "ts": 1}\n' + path.read_text(),
        encoding="utf-8",
    )
    assert recent_order_ids("ex") == ["new"]
    remember_order_now("ex", "newer")
    assert "old" not in path.read_text(encoding="utf-8")


def test_lp_open_without_an_address_is_not_minted_again():
    checkpoint = {
        "version": 1,
        "type": "lp_executor",
        "position_address": None,
        "saw_open_attempt": True,
    }
    assert can_resume(checkpoint) is not None
    fresh = {
        "version": 1,
        "type": "lp_executor",
        "position_address": None,
        "saw_open_attempt": False,
    }
    assert can_resume(fresh) is None
