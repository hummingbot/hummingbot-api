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
    before_prune = path.read_text(encoding="utf-8")
    assert "old" in before_prune
    assert recent_order_ids("ex") == ["new", "newer"]
    modes = []
    opens = []
    _track_ledger_opens(monkeypatch, modes, opens)
    prune_order_ledger()
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        ids.append(json.loads(line)["order_id"])
    assert ids == ["new", "newer"]
    assert recent_order_ids("ex") == ["new", "newer"]
    assert not list(tmp_path.glob(".order-ledger-*"))
    assert not _live_opened_for_rewrite(path, modes, opens)


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


from decimal import Decimal
import os
import threading
import time

import json

from utils.executor_checkpoint import (
    LEDGER_LOCK,
    _LedgerReadError,
    cap_arguments,
    install_grid_resume_cap,
    merge_recent_ids,
    prune_order_ledger,
)


def _track_ledger_opens(monkeypatch, modes, opens):
    real_open = open
    real_os_open = os.open

    def tracking_open(file, mode="r", *args, **kwargs):
        modes.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    def tracking_os_open(file, flags, *args, **kwargs):
        opens.append((str(file), flags))
        return real_os_open(file, flags, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracking_open)
    monkeypatch.setattr(os, "open", tracking_os_open)


def _live_opened_for_rewrite(path, modes, opens):
    live = str(path)
    if any(name == live and isinstance(mode, str) and "w" in mode for name, mode in modes):
        return True
    return any(
        name == live and isinstance(flags, int) and flags & os.O_TRUNC
        for name, flags in opens
    )



class _Side:
    def __init__(self, name):
        self.name = name


def _proposal(price):
    return SimpleNamespace(price=Decimal(str(price)))


def _live(order_id, price, is_open=True):
    return SimpleNamespace(
        client_order_id=order_id,
        price=Decimal(str(price)),
        is_open=is_open,
        trading_pair="SOL-USDT",
    )


def _wrapped(proposals, plan, orders=None, cached=None, lost=None, ready=True,
             quote="120.01", side="BUY", tick="0.0001", spread="0.0001", raise_book=False):
    orders = list(orders or [])
    cached = list(cached or [])
    lost = list(lost or [])

    class _Inflight(dict):
        def values(self):
            if raise_book:
                raise OSError("book")
            return super().values()

    inflight = _Inflight({order.client_order_id: order for order in orders})
    connector = SimpleNamespace(
        ready=ready,
        in_flight_orders=inflight,
        _order_tracker=SimpleNamespace(
            cached_orders={order.client_order_id: order for order in cached},
            lost_orders={order.client_order_id: order for order in lost},
        ),
    )
    config = SimpleNamespace(
        side=_Side(side) if side else SimpleNamespace(),
        safe_extra_spread=Decimal(str(spread)),
        trading_pair="SOL-USDT",
        min_spread_between_orders=Decimal("0.0005"),
    )
    executor = SimpleNamespace(
        config=config,
        trading_rules=None if tick is None else SimpleNamespace(min_price_increment=Decimal(str(tick))),
        current_open_quote=None if quote is None else quote,
        connectors={"okx": connector},
        get_open_orders_to_create=lambda: list(proposals),
    )
    install_grid_resume_cap(executor, plan)
    return executor


def test_ledger_replace_failure_keeps_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    fresh = json.dumps({"executor_id": "ex", "order_id": "keep", "ts": time.time()})
    path.write_text(
        '{"executor_id": "ex", "order_id": "old", "ts": 1}\n' + fresh + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    before = path.read_bytes()
    modes = []
    opens = []
    _track_ledger_opens(monkeypatch, modes, opens)

    def fail_replace(*_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    prune_order_ledger()
    assert path.read_bytes() == before
    assert not _live_opened_for_rewrite(path, modes, opens)
    assert not list(tmp_path.glob(".order-ledger-*"))


def test_ledger_replace_writes_a_complete_file(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    remember_order_now("ex", "keep")
    remember_order_now("ex", "next")
    text = path.read_text(encoding="utf-8")
    assert "keep" in text and "next" in text
    assert not list(tmp_path.glob(".order-ledger-*"))


def test_ledger_read_error_appends_without_replace(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"executor_id": "ex", "order_id": "keep", "ts": 10}')
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    before = path.read_bytes()
    synced = []
    real_fsync = os.fsync

    def tracking_fsync(descriptor):
        synced.append(descriptor)
        return real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", tracking_fsync)
    monkeypatch.setattr(
        "utils.executor_checkpoint._read_ledger",
        lambda: (_ for _ in ()).throw(_LedgerReadError("unread")),
    )
    replaced = []
    monkeypatch.setattr(os, "replace", lambda *args: replaced.append(args))
    remember_order_now("ex", "new")
    text = path.read_text(encoding="utf-8")
    assert text.startswith(before.decode("utf-8"))
    assert '"order_id": "new"' in text.splitlines()[-1]
    assert replaced == []
    assert synced
    monkeypatch.setattr(os, "write", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("append")))
    failed = path.read_bytes()
    remember_order_now("ex", "lost")
    assert path.read_bytes() == failed
    assert replaced == []


def test_ledger_lock_covers_read_and_write(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    remember_order_now("ex", "first")
    holding = {"on": False}
    real_read = __import__("utils.executor_checkpoint", fromlist=["_read_ledger"])._read_ledger

    def guarded_read():
        if holding["on"]:
            raise AssertionError("read while the test holds the lock")
        return real_read()

    monkeypatch.setattr("utils.executor_checkpoint._read_ledger", guarded_read)
    done = []
    LEDGER_LOCK.acquire()
    holding["on"] = True

    def second():
        remember_order_now("ex", "second")
        done.append(True)

    worker = threading.Thread(target=second)
    worker.start()
    time.sleep(0.05)
    assert done == []
    holding["on"] = False
    LEDGER_LOCK.release()
    worker.join(1)
    text = path.read_text(encoding="utf-8")
    assert "first" in text and "second" in text


def test_remember_order_appends_without_replace_or_truncate(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b'{"executor_id": "ex", "order_id": "keep", "ts": 10}')
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    modes = []
    opens = []
    _track_ledger_opens(monkeypatch, modes, opens)
    replaced = []
    monkeypatch.setattr(os, "replace", lambda *args: replaced.append(args))
    remember_order_now("ex", "new")
    text = path.read_text(encoding="utf-8")
    assert text.startswith('{"executor_id": "ex", "order_id": "keep", "ts": 10}\n')
    assert json.loads(text.splitlines()[-1])["order_id"] == "new"
    assert replaced == []
    assert not _live_opened_for_rewrite(path, modes, opens)


def test_prune_read_error_does_not_replace(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"executor_id": "ex", "order_id": "keep", "ts": 10}\n', encoding="utf-8")
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    before = path.read_bytes()
    monkeypatch.setattr(
        "utils.executor_checkpoint._read_ledger",
        lambda: (_ for _ in ()).throw(_LedgerReadError("unread")),
    )
    replaced = []
    monkeypatch.setattr(os, "replace", lambda *args: replaced.append(args))
    prune_order_ledger()
    assert path.read_bytes() == before
    assert replaced == []


def test_prune_skips_nonempty_file_with_no_rows(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    before = path.read_bytes()
    replaced = []
    monkeypatch.setattr(os, "replace", lambda *args: replaced.append(args))
    prune_order_ledger()
    assert path.read_bytes() == before
    assert replaced == []
    assert not list(tmp_path.glob(".order-ledger-*"))


def test_prune_does_not_create_a_missing_file(tmp_path, monkeypatch):
    path = tmp_path / "missing.jsonl"
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    prune_order_ledger()
    assert not path.exists()


def test_prune_waits_for_the_lock_and_keeps_both_fresh_ids(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    fresh = json.dumps({"executor_id": "ex", "order_id": "fresh", "ts": time.time()})
    path.write_text(
        '{"executor_id": "ex", "order_id": "old", "ts": 1}\n' + fresh + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    done = []
    LEDGER_LOCK.acquire()
    try:
        def prune():
            prune_order_ledger()
            done.append("prune")

        def append():
            remember_order_now("ex", "second")
            done.append("append")

        pruner = threading.Thread(target=prune)
        appender = threading.Thread(target=append)
        pruner.start()
        appender.start()
        time.sleep(0.1)
        assert done == []
    finally:
        LEDGER_LOCK.release()
    pruner.join(2)
    appender.join(2)
    assert not pruner.is_alive() and not appender.is_alive()
    text = path.read_text(encoding="utf-8")
    assert "fresh" in text and "second" in text and "old" not in text


def test_prune_keeps_a_line_appended_after_read_before_replace(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    fresh = json.dumps({"executor_id": "ex", "order_id": "fresh", "ts": time.time()})
    path.write_text(
        '{"executor_id": "ex", "order_id": "old", "ts": 1}\n' + fresh + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(path))
    read_done = threading.Event()
    real_read = __import__("utils.executor_checkpoint", fromlist=["_read_ledger"])._read_ledger

    def wrapped_read():
        rows = real_read()
        read_done.set()
        return rows

    monkeypatch.setattr("utils.executor_checkpoint._read_ledger", wrapped_read)

    def appender():
        assert read_done.wait(2)
        remember_order_now("ex", "during")

    worker = threading.Thread(target=appender)
    worker.start()
    real_replace = os.replace

    def replace_after_append_window(src, dst):
        # If the lock was dropped after the read, this append finishes and the
        # following replace would erase it. A held lock makes the join time out.
        worker.join(1)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_after_append_window)
    prune_order_ledger()
    worker.join(2)
    assert not worker.is_alive()
    ids = [json.loads(line)["order_id"] for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert "old" not in ids
    assert "fresh" in ids and "during" in ids


def test_two_orders_at_one_price_stay_blocked_until_both_are_finished():
    plan = {
        "touch_budget": 1,
        "blocked_orders": [
            {"order_id": "a", "price": "100"},
            {"order_id": "b", "price": "100"},
        ],
        "blocked_prices": ["100"],
        "touch_hold_ids": [],
    }
    level = _proposal("100")
    seen_only_b = _wrapped([level], plan, orders=[_live("b", "100")], quote="110", tick="0.01")
    assert seen_only_b.get_open_orders_to_create() == []
    lost = _wrapped([level], plan, lost=[_live("a", "100")], quote="110", tick="0.01")
    assert lost.get_open_orders_to_create() == []
    done = _wrapped(
        [level], plan, cached=[_live("a", "100", is_open=False), _live("b", "100", is_open=False)],
        quote="110", tick="0.01",
    )
    assert done.get_open_orders_to_create() == [level]
    fake = SimpleNamespace(ready=True, in_flight_orders={}, cached_orders={"a": _live("a", "100", False)})
    executor = _wrapped([level], plan, quote="110", tick="0.01")
    executor.connectors = {"okx": fake}
    assert executor.get_open_orders_to_create() == []


def test_unreadable_book_does_not_place_or_raise():
    plan = {"touch_budget": 0, "blocked_orders": [{"order_id": "a", "price": "100"}], "blocked_prices": ["100"]}
    empty = _wrapped([_proposal("100")], plan)
    empty.connectors = {}
    assert empty.get_open_orders_to_create() == []
    not_dict = _wrapped([_proposal("100")], plan)
    not_dict.connectors["okx"].in_flight_orders = []
    assert not_dict.get_open_orders_to_create() == []
    exploding = _wrapped([_proposal("100")], plan, raise_book=True)
    assert exploding.get_open_orders_to_create() == []


def test_recent_order_ids_on_read_error_is_an_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("EXECUTOR_ORDER_LEDGER", str(tmp_path / "missing-read.jsonl"))
    monkeypatch.setattr(
        "utils.executor_checkpoint._read_ledger",
        lambda: (_ for _ in ()).throw(_LedgerReadError("unread")),
    )
    assert recent_order_ids("ex") == []
    assert merge_recent_ids({"levels": []}, recent_order_ids("ex"))["levels"] == []


def test_resume_plan_fields_cover_the_early_return_and_unknown_market():
    loose = [{"order_id": "live", "price": "100", "is_open": True, "side": "BUY"}]
    unknown = grid_resume_plan(_grid([_level("L0", "100")]), loose, market_price=None, side="BUY")
    assert unknown["touch_zero_unknown_market"] is True
    assert unknown["touch_budget"] == 0
    assert unknown["touch_hold_ids"] == []
    assert unknown["blocked_orders"][0]["order_id"] == "live"
    nan = grid_resume_plan(_grid([_level("L0", "100")]), loose, market_price=float("nan"), side="BUY")
    assert nan["touch_zero_unknown_market"] is True
    near = {"order_id": "near", "price": "100", "is_open": True, "side": "BUY"}
    aside = {"order_id": "aside", "price": "80", "is_open": True, "side": "BUY"}
    mixed = grid_resume_plan(_grid([_level("L0", "100")]), [near, aside], market_price="100", side="BUY")
    assert mixed["touch_hold_ids"] == ["near"]
    assert {row["order_id"] for row in mixed["blocked_orders"]} == {"near", "aside"}
    early = grid_resume_plan(_grid([_level("L0", "100")]), [], market_price=None, side="BUY")
    assert early["blocked_orders"] == []
    assert early["touch_hold_ids"] == []
    assert early["touch_zero_unknown_market"] is False
    assert early["touch_budget"] is None


def test_cap_arguments_keep_a_missing_key_missing():
    missing = cap_arguments({"blocked_prices": ["120"], "touch_budget": 0})
    assert "blocked_orders" not in missing
    assert missing["blocked_prices"] == ["120"]
    explicit = cap_arguments({
        "blocked_orders": [],
        "touch_budget": None,
        "blocked_prices": [],
        "touch_hold_ids": [],
        "touch_zero_unknown_market": False,
    })
    assert explicit["blocked_orders"] == []
    assert explicit["touch_budget"] is None


def test_missing_blocked_orders_keep_the_price_ban():
    plan = {"blocked_prices": ["120"], "touch_budget": 0}
    level = _proposal("120")
    crossing = _proposal("130")
    executor = _wrapped([level, crossing], plan, quote="120.01", tick="0.01")
    assert executor.get_open_orders_to_create() == []
    assert executor._touch_place_budget == 0


def test_touch_zero_waits_for_every_holder_and_one_touch_per_call():
    plan = {
        "touch_budget": 0,
        "blocked_orders": [
            {"order_id": "first", "price": "100"},
            {"order_id": "second", "price": "119.9979"},
        ],
        "blocked_prices": ["100", "119.9979"],
        "touch_hold_ids": ["first"],
    }
    crossing = _proposal("130")
    same_tick = _proposal("119.9979")
    holding = _wrapped(
        [crossing], plan,
        cached=[_live("first", "100", False)],
        orders=[_live("second", "119.9979")],
        quote="120.01", tick="0.0001",
    )
    assert holding.get_open_orders_to_create() == []
    assert holding._touch_place_budget == 0
    clear = _wrapped(
        [crossing, same_tick], plan,
        cached=[_live("first", "100", False), _live("second", "119.9979", False)],
        quote="120.01", tick="0.0001",
    )
    assert clear.get_open_orders_to_create() == [crossing]
    assert clear._touch_place_budget == 0
    assert clear.get_open_orders_to_create() == []
    no_touch = _wrapped(
        [_proposal("80")], plan,
        cached=[_live("first", "100", False)],
        orders=[_live("second", "119.9979")],
        quote="120.01", tick="0.0001",
    )
    assert no_touch.get_open_orders_to_create() == [_proposal("80")]
    assert no_touch._touch_place_budget == 0


def test_budget_one_does_not_glue_a_level_under_the_quote():
    plan = {"touch_budget": 1, "blocked_orders": [{"order_id": "live", "price": "80"}], "blocked_prices": ["80"]}
    crossing = _proposal("130")
    own = _proposal("120")
    for tick in ("0.0001", "0.01"):
        executor = _wrapped([crossing, own], plan, orders=[_live("live", "80")], quote="120.01", tick=tick)
        assert executor.get_open_orders_to_create() == [crossing, own]
        assert executor._touch_place_budget == 0
        assert executor.get_open_orders_to_create() == [own]
    blind = _wrapped([crossing], plan, orders=[_live("live", "120")], quote="120.01", tick=None)
    assert crossing not in blind.get_open_orders_to_create()
    assert blind._touch_place_budget == 1


def test_missing_or_nan_quote_does_not_release_a_touch():
    plan = {"touch_budget": 0, "blocked_orders": [], "blocked_prices": [], "touch_hold_ids": []}
    level = _proposal("130")
    for quote in (None, float("nan"), Decimal("NaN")):
        executor = _wrapped([level], plan, quote=quote, ready=True)
        assert executor.get_open_orders_to_create() == []
        assert executor._touch_place_budget == 0


def test_synthetic_unknown_market_cap_is_not_what_resume_returns():
    assert grid_resume_plan(_grid([_level("L0", "100")]), [], market_price=None)["touch_budget"] is None
    plan = {
        "touch_budget": 0,
        "blocked_orders": [],
        "blocked_prices": [],
        "touch_hold_ids": [],
        "touch_zero_unknown_market": True,
    }
    level = _proposal("130")
    executor = _wrapped([level], plan, quote="120.01", ready=True)
    assert executor.get_open_orders_to_create() == [level]
    assert executor._touch_place_budget == 0
    assert executor.get_open_orders_to_create() == []
    unseen = dict(plan)
    unseen["blocked_orders"] = [{"order_id": "late", "price": "119.9979"}]
    held = _wrapped([level], unseen, quote="120.01", ready=True)
    assert held.get_open_orders_to_create() == []
    assert held._touch_place_budget == 0
    quiet = _wrapped([level], plan, quote=None, ready=True)
    assert quiet.get_open_orders_to_create() == []
    not_ready = _wrapped([level], unseen, quote="120.01", ready=False)
    assert not_ready.get_open_orders_to_create() == []
    remembered = _wrapped(
        [level], unseen, quote="120.01", ready=False,
        cached=[_live("late", "119.9979", False)],
    )
    assert remembered.get_open_orders_to_create() == []
    assert "late" in remembered._seen_finished
    remembered.connectors["okx"].ready = True
    remembered.connectors["okx"]._order_tracker.cached_orders = {}
    assert remembered.get_open_orders_to_create() == [level]


def test_level_just_under_the_quote_is_not_a_second_touch():
    plan = {"touch_budget": 1, "blocked_orders": [], "blocked_prices": []}
    crossing = _proposal("130")
    own = _proposal("120")
    for tick in ("0.0001", "0.01"):
        executor = _wrapped([crossing, own], plan, quote="120.01", tick=tick)
        assert executor.get_open_orders_to_create() == [crossing, own]
        assert executor._touch_place_budget == 0
    nameless = _wrapped([crossing], plan, quote="120.01")
    nameless.config.side = SimpleNamespace()
    assert nameless.get_open_orders_to_create() == []


def test_completed_snapshot_id_does_not_free_another_order_at_that_price():
    plan = {
        "touch_budget": 0,
        "blocked_orders": [{"order_id": "snap", "price": "100"}],
        "blocked_prices": ["100"],
    }
    level = _proposal("100")
    occupied = _wrapped(
        [level], plan, quote="110", tick="0.01",
        cached=[_live("snap", "100", False)],
        orders=[_live("other", "100")],
    )
    assert occupied.get_open_orders_to_create() == []
    assert occupied._touch_place_budget == 0
    lost = _wrapped(
        [level], plan, quote="110", tick="0.01",
        cached=[_live("snap", "100", False)],
        lost=[_live("other", "100")],
    )
    assert lost.get_open_orders_to_create() == []
    clear = _wrapped(
        [level], plan, quote="110", tick="0.01",
        cached=[_live("snap", "100", False)],
    )
    assert clear.get_open_orders_to_create() == [level]


def test_unseen_or_lost_id_holds_zero_even_when_its_price_is_away():
    plan = {
        "touch_budget": 0,
        "blocked_orders": [{"order_id": "late", "price": "80"}],
        "blocked_prices": ["80"],
        "touch_hold_ids": [],
    }
    crossing = _proposal("130")
    empty = _wrapped([crossing], plan, quote="120.01", ready=True)
    assert empty.get_open_orders_to_create() == []
    assert empty._touch_place_budget == 0
    assert empty._touch_zero_released is False
    lost = _wrapped([crossing], plan, lost=[_live("late", "80")], quote="120.01", ready=True)
    assert lost.get_open_orders_to_create() == []
    assert lost._touch_place_budget == 0
    priceless = {
        "touch_budget": 0,
        "blocked_orders": [{"order_id": "late", "price": None}],
        "blocked_prices": [],
        "touch_hold_ids": [],
    }
    own = _proposal("90")
    priced = _wrapped([own, crossing], priceless, orders=[_live("late", "70")], quote="120.01")
    assert priced.get_open_orders_to_create() == []
    assert priced._touch_place_budget == 0


def test_sell_touch_follows_quantization_not_the_spread_window():
    plan = {"touch_budget": 1, "blocked_orders": [], "blocked_prices": []}
    crossing = _proposal("110")
    near = _proposal("120")
    coarse = _wrapped([crossing, near], plan, quote="119.99", side="SELL", tick="0.01")
    assert coarse.get_open_orders_to_create() == [crossing]
    assert coarse._touch_place_budget == 0
    assert coarse.get_open_orders_to_create() == []
    fine = _wrapped([crossing, near], plan, quote="119.99", side="SELL", tick="0.0001")
    assert fine.get_open_orders_to_create() == [crossing, near]
    assert fine._touch_place_budget == 0


def test_unread_active_book_does_not_finish_or_open_an_id():
    plan = {
        "touch_budget": 0,
        "blocked_orders": [{"order_id": "a", "price": "100"}],
        "blocked_prices": ["100"],
        "touch_hold_ids": [],
    }
    level = _proposal("100")
    closed = _wrapped(
        [level], plan, orders=[_live("a", "100", False)], quote="110", tick="0.01", ready=False,
    )
    assert closed.get_open_orders_to_create() == []
    assert "a" not in closed._seen_finished
    closed.connectors["okx"].ready = True
    closed.connectors["okx"].in_flight_orders = {}
    assert closed.get_open_orders_to_create() == []
    assert closed._touch_place_budget == 0
    opened = _wrapped(
        [_proposal("130")],
        {"touch_budget": 0, "blocked_orders": [{"order_id": "a", "price": "80"}], "blocked_prices": ["80"], "touch_hold_ids": []},
        orders=[_live("a", "80")],
        quote="120.01",
        ready=False,
    )
    assert opened.get_open_orders_to_create() == []
    assert "a" not in opened._seen_open
    opened.connectors["okx"].ready = True
    opened.connectors["okx"].in_flight_orders = {}
    assert opened.get_open_orders_to_create() == []
    assert opened._touch_zero_released is False


def test_uncomparable_price_holds_the_ban():
    plan = {
        "touch_budget": 0,
        "blocked_orders": [{"order_id": "snap", "price": float("nan")}],
        "blocked_prices": [float("nan")],
    }
    own = _proposal("80")
    crossing = _proposal("130")
    finished = _wrapped(
        [own, crossing], plan, quote="120.01", tick="0.01",
        cached=[_live("snap", "100", False)],
    )
    assert finished.get_open_orders_to_create() == []
    assert finished._touch_place_budget == 0
    live = _wrapped(
        [crossing],
        {"touch_budget": 1, "blocked_orders": [{"order_id": "live", "price": "NaN"}], "blocked_prices": ["NaN"]},
        orders=[_live("live", Decimal("NaN"))],
        quote="120.01",
        tick="0.01",
    )
    assert live.get_open_orders_to_create() == []
    assert live._touch_place_budget == 1
    book_only = _wrapped(
        [crossing],
        {"touch_budget": 0, "blocked_orders": [], "blocked_prices": []},
        orders=[SimpleNamespace(client_order_id="stray", price=None, is_open=True, trading_pair="SOL-USDT")],
        quote="120.01",
        tick="0.01",
    )
    assert book_only.get_open_orders_to_create() == []
    assert book_only._touch_place_budget == 0


def test_sell_live_order_at_the_quantized_touch_is_not_duplicated():
    plan = {"touch_budget": 1, "blocked_orders": [{"order_id": "ask", "price": "120"}], "blocked_prices": ["120"]}
    level = _proposal("120")
    executor = _wrapped(
        [level], plan, orders=[_live("ask", "120")], quote="119.99", side="SELL", tick="0.01",
    )
    assert executor.get_open_orders_to_create() == []
    assert executor._touch_place_budget == 1


def test_conservative_cap_does_not_release_zero():
    from utils.executor_checkpoint import conservative_grid_cap
    plan = {"touch_budget": 0, "blocked_orders": [{"order_id": "a", "price": "100"}], "blocked_prices": ["100"]}
    executor = SimpleNamespace(
        config=SimpleNamespace(side=_Side("BUY"), safe_extra_spread=Decimal("0.0001")),
        current_open_quote=Decimal("110"),
        get_open_orders_to_create=lambda: [_proposal("100"), _proposal("80")],
    )
    conservative_grid_cap(executor, plan)
    assert executor.get_open_orders_to_create() == [_proposal("80")]
    assert executor._touch_place_budget == 0
