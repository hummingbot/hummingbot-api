"""Save and restore the orders an API executor already owns.

A restart used to mark every RUNNING row SYSTEM_CLEANUP and forget the exchange.
Recreating the executor from its create-config places a second set of orders.
This module records the order ids (and, for a filled order, the order itself)
so the same executor can be started again and reattached.

Nothing here places or cancels an order. A grid is not refused because one
open order fits two levels: that order stays unattached and new orders at its
price are capped. An order id this checkpoint does not already own is never
adopted by price.
"""
import json
import logging
import os
import tempfile
import threading
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1

# Relative price match for an order the checkpoint missed but the connector
# still tracks. Wider than one tick, narrower than a grid step we would confuse
# with the next level.
PRICE_MATCH = Decimal("0.001")

SUPPORTED_TYPES = frozenset({
    "position_executor",
    "grid_executor",
    "dca_executor",
    "twap_executor",
    "arbitrage_executor",
    "xemm_executor",
    "order_executor",
    "lp_executor",
})


def _enum_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else str(value)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def capture_tracked(order: Any) -> Optional[Dict[str, Any]]:
    """A TrackedOrder, or None when it does not own an exchange order yet."""
    if order is None:
        return None
    order_id = getattr(order, "order_id", None)
    if not order_id:
        return None
    payload: Dict[str, Any] = {"order_id": order_id}
    live = getattr(order, "order", None)
    if live is not None and hasattr(live, "to_json"):
        payload["order"] = live.to_json()
        payload["filled"] = bool(getattr(live, "is_filled", False))
    else:
        payload["filled"] = False
    return payload


def _capture_grid(executor: Any) -> Dict[str, Any]:
    levels = []
    for level in getattr(executor, "grid_levels", []) or []:
        levels.append({
            "id": level.id,
            "price": _text(level.price),
            "amount_quote": _text(level.amount_quote),
            "take_profit": _text(level.take_profit),
            "side": _enum_name(level.side),
            "open_order_type": _enum_name(level.open_order_type),
            "take_profit_order_type": _enum_name(level.take_profit_order_type),
            "open_order": capture_tracked(getattr(level, "active_open_order", None)),
            "close_order": capture_tracked(getattr(level, "active_close_order", None)),
        })
    close_order = capture_tracked(getattr(executor, "_close_order", None))
    return {"levels": levels, "close_order": close_order}


def _capture_position(executor: Any) -> Dict[str, Any]:
    return {
        "open_order": capture_tracked(getattr(executor, "_open_order", None)),
        "close_order": capture_tracked(getattr(executor, "_close_order", None)),
        "take_profit_order": capture_tracked(getattr(executor, "_take_profit_limit_order", None)),
    }


def _capture_dca(executor: Any) -> Dict[str, Any]:
    return {
        "open_orders": [item for item in (
            capture_tracked(order) for order in getattr(executor, "_open_orders", []) or []
        ) if item],
        "close_orders": [item for item in (
            capture_tracked(order) for order in getattr(executor, "_close_orders", []) or []
        ) if item],
    }


def _capture_order(executor: Any) -> Dict[str, Any]:
    return {"order": capture_tracked(getattr(executor, "_order", None))}


def _capture_twap(executor: Any) -> Dict[str, Any]:
    plan = []
    for timestamp, order in (getattr(executor, "_order_plan", {}) or {}).items():
        plan.append({
            "timestamp": _text(timestamp),
            "order": capture_tracked(order),
        })
    return {
        "start_timestamp": _text(getattr(executor, "_start_timestamp", None)),
        "plan": plan,
    }


def _capture_arbitrage(executor: Any) -> Dict[str, Any]:
    return {
        "buy_order": capture_tracked(getattr(executor, "_buy_order", None)),
        "sell_order": capture_tracked(getattr(executor, "_sell_order", None)),
    }


def _capture_xemm(executor: Any) -> Dict[str, Any]:
    return {
        "maker_order": capture_tracked(getattr(executor, "maker_order", None)),
        "taker_order": capture_tracked(getattr(executor, "taker_order", None)),
    }


def _capture_lp(executor: Any) -> Dict[str, Any]:
    state = getattr(executor, "lp_position_state", None)
    dumped: Dict[str, Any] = {}
    if state is not None and hasattr(state, "model_dump"):
        dumped = state.model_dump(mode="json")
        # TrackedOrder is not a pydantic field we can round-trip blindly.
        for key in ("active_open_order", "active_close_order", "active_swap_order"):
            dumped.pop(key, None)
    position_address = dumped.get("position_address")
    state_name = _enum_name(dumped.get("state")) or _text(dumped.get("state"))
    opening = state_name == "OPENING" or bool(position_address)
    return {
        "position_address": position_address,
        "state": state_name,
        "saw_open_attempt": opening or bool(getattr(executor, "_position_seen_onchain", False)),
        "state_json": dumped,
        "open_order": capture_tracked(getattr(state, "active_open_order", None)) if state else None,
        "close_order": capture_tracked(getattr(state, "active_close_order", None)) if state else None,
    }


_CAPTURERS = {
    "grid_executor": _capture_grid,
    "position_executor": _capture_position,
    "dca_executor": _capture_dca,
    "order_executor": _capture_order,
    "twap_executor": _capture_twap,
    "arbitrage_executor": _capture_arbitrage,
    "xemm_executor": _capture_xemm,
    "lp_executor": _capture_lp,
}


def order_ledger(executor: Any) -> Dict[str, Dict[str, Any]]:
    """Ids this executor has placed, kept until the exchange confirms they are gone."""
    ledger = getattr(executor, "_order_ledger", None)
    if not isinstance(ledger, dict):
        ledger = {}
        try:
            executor._order_ledger = ledger
        except Exception:
            return ledger
    return ledger


# Written in the same moment the exchange accepts the order, before the
# database task gets a turn. Lives under bots/ so a container recreate keeps it:
# compose mounts that directory, not /hummingbot-api/data.
LEDGER_KEEP_SECONDS = 48 * 3600
LEDGER_LOCK = threading.Lock()


class _LedgerReadError(OSError):
    """The ledger file exists but could not be read. Not an empty ledger."""


def order_ledger_path() -> str:
    return os.environ.get(
        "EXECUTOR_ORDER_LEDGER",
        "/hummingbot-api/bots/data/order-ledger.jsonl",
    )


def _read_ledger() -> List[Dict[str, Any]]:
    path = order_ledger_path()
    if not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError as exc:
        logger.warning("order ledger read failed: %s", exc)
        raise _LedgerReadError(str(exc)) from exc
    return rows


def _fsync_directory(path: str) -> bool:
    """Fsync the parent of path. True only when that fsync succeeded."""
    directory = os.path.dirname(path)
    if not directory:
        return False
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        logger.warning("order ledger directory fsync failed: %s", exc)
        return False
    try:
        os.fsync(descriptor)
    except OSError as exc:
        logger.warning("order ledger directory fsync failed: %s", exc)
        return False
    finally:
        os.close(descriptor)
    return True


# One ledger path owes a directory entry until that fsync is seen to succeed.
# A different path does not inherit the debt. Not a second file.
_LEDGER_NAME_FSYNC_OWED: Optional[str] = None
_LEDGER_PARENT_FSYNC_OWED: Optional[str] = None


def _write_ledger(rows: List[Dict[str, Any]]) -> None:
    """Replace the ledger only after the new bytes are on disk."""
    path = order_ledger_path()
    temporary = None
    descriptor = None
    replaced = False
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".order-ledger-", dir=directory)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            for row in rows:
                handle.write(json.dumps(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        temporary = None
        _fsync_directory(path)
    except OSError as exc:
        logger.warning("order ledger write failed: %s", exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary and not replaced:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _append_ledger_line(row: Dict[str, Any]) -> None:
    """Append one id. Fsync the directory only while this process still owes the name."""
    global _LEDGER_NAME_FSYNC_OWED, _LEDGER_PARENT_FSYNC_OWED
    path = order_ledger_path()
    descriptor = None
    file_synced = False
    directory = os.path.dirname(path) or "."
    try:
        created_directory = not os.path.isdir(directory)
        os.makedirs(directory, exist_ok=True)
        if created_directory:
            _LEDGER_PARENT_FSYNC_OWED = directory
        created_file = not os.path.exists(path)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        if created_file:
            _LEDGER_NAME_FSYNC_OWED = path
        size = os.lseek(descriptor, 0, os.SEEK_END)
        if size:
            os.lseek(descriptor, size - 1, os.SEEK_SET)
            last = os.read(descriptor, 1)
            os.lseek(descriptor, 0, os.SEEK_END)
            if last != b"\n":
                os.write(descriptor, b"\n")
        os.write(descriptor, (json.dumps(row) + "\n").encode("utf-8"))
        os.fsync(descriptor)
        file_synced = True
    except OSError as exc:
        logger.warning("order ledger append failed: %s", exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not file_synced:
        return
    if _LEDGER_NAME_FSYNC_OWED == path and _fsync_directory(path):
        _LEDGER_NAME_FSYNC_OWED = None
    if _LEDGER_PARENT_FSYNC_OWED == directory and _LEDGER_NAME_FSYNC_OWED != path:
        if _fsync_directory(directory):
            _LEDGER_PARENT_FSYNC_OWED = None


def remember_order_now(executor_id: str, order_id: Optional[str]) -> None:
    """Durably note an order id before the async database write runs.

    Append only. Rewriting the file here waits on a multi-day ledger and delays
    the next order the exchange has not accepted yet.
    """
    if not executor_id or not order_id:
        return
    row = {"executor_id": executor_id, "order_id": order_id, "ts": time.time()}
    with LEDGER_LOCK:
        _append_ledger_line(row)


def prune_order_ledger() -> None:
    """Drop ids older than the keep window once, before resume places again.

    Not on the place path. The same keep rule as the old rewrite: a row with no
    numeric ts is not kept, and ts below the cutoff is not kept. A failed read
    must not replace the live file.
    """
    path = order_ledger_path()
    with LEDGER_LOCK:
        if not os.path.exists(path):
            return
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            logger.warning("order ledger prune skipped: %s", exc)
            return
        try:
            existing = _read_ledger()
        except _LedgerReadError:
            logger.warning("order ledger prune skipped; file left unchanged")
            return
        if size > 0 and not existing:
            logger.warning("order ledger prune skipped; file has no readable rows")
            return
        cutoff = time.time() - LEDGER_KEEP_SECONDS
        kept = []
        for item in existing:
            try:
                ts = float(item.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if ts >= cutoff:
                kept.append(item)
        if len(kept) == len(existing):
            return
        _write_ledger(kept)


def recent_order_ids(executor_id: str) -> List[str]:
    """Ids noted for this executor inside the keep window, newest unique."""
    if not executor_id:
        return []
    try:
        rows = _read_ledger()
    except _LedgerReadError:
        logger.warning("order ledger unreadable during resume; no extra ids")
        return []
    cutoff = time.time() - LEDGER_KEEP_SECONDS
    latest: Dict[str, float] = {}
    for row in rows:
        if row.get("executor_id") != executor_id or not row.get("order_id"):
            continue
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        latest[str(row["order_id"])] = ts
    return list(latest)


def note_placed(executor: Any, order_id: Optional[str]) -> None:
    if not order_id:
        return
    row = order_ledger(executor).setdefault(order_id, {
        "order_id": order_id,
        "filled": False,
        "gone": False,
    })
    row["gone"] = False


def note_filled(executor: Any, order_id: Optional[str], order_json: Optional[Dict[str, Any]] = None) -> None:
    """A fill is recorded when the event arrives, not on the next one-second tick."""
    if not order_id:
        return
    row = order_ledger(executor).setdefault(order_id, {
        "order_id": order_id,
        "filled": True,
        "gone": False,
    })
    row["filled"] = True
    row["gone"] = False
    if order_json:
        row["order"] = order_json


def note_gone(executor: Any, order_id: Optional[str]) -> None:
    """Drop an id only after the exchange has confirmed cancel or failure.

    A still-open in-flight order is not gone, even if the grid already cleared
    the level slot.
    """
    if not order_id:
        return
    if _order_still_open(executor, order_id):
        return
    row = order_ledger(executor).get(order_id)
    if row is None or row.get("filled"):
        return
    row["gone"] = True


def _order_still_open(executor: Any, order_id: str) -> bool:
    connectors = getattr(executor, "connectors", None) or {}
    for connector in connectors.values():
        live = (getattr(connector, "in_flight_orders", None) or {}).get(order_id)
        if live is not None and getattr(live, "is_open", False):
            return True
    return False


def remember_current_slots(executor: Any) -> None:
    """Copy ids the executor currently holds into the ledger, including fills."""
    pairs = (
        ("open_order", "active_open_order"),
        ("close_order", "active_close_order"),
    )
    for level in getattr(executor, "grid_levels", []) or []:
        for role, attr in pairs:
            _remember_tracked(executor, getattr(level, attr, None), role, getattr(level, "id", None))
    for role, attr in (
        ("open_order", "_open_order"),
        ("close_order", "_close_order"),
        ("take_profit_order", "_take_profit_limit_order"),
        ("order", "_order"),
    ):
        _remember_tracked(executor, getattr(executor, attr, None), role, None)
    for tracked in list(getattr(executor, "_open_orders", []) or []) + list(getattr(executor, "_close_orders", []) or []):
        _remember_tracked(executor, tracked, "open_order", None)


def _remember_tracked(executor: Any, tracked: Any, role: str, level_id: Optional[str]) -> None:
    order_id = getattr(tracked, "order_id", None) if tracked is not None else None
    if not order_id:
        return
    note_placed(executor, order_id)
    row = order_ledger(executor)[order_id]
    row["role"] = role
    if level_id:
        row["level_id"] = level_id
    live = getattr(tracked, "order", None)
    if live is not None and hasattr(live, "to_json"):
        row["order"] = live.to_json()
        if getattr(live, "is_filled", False):
            note_filled(executor, order_id, row["order"])


def _saved_order_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "order_id": row["order_id"],
        "filled": bool(row.get("filled")),
        "order": row.get("order"),
        "level_id": row.get("level_id"),
        "role": row.get("role"),
    }


def merge_ledger(body: Dict[str, Any], ledger: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Put ledger ids back onto empty slots. A cleared slot must not forget a live order."""
    kept = [row for row in ledger.values() if row.get("order_id") and not row.get("gone")]
    if body.get("type") == "grid_executor":
        used = set()
        for level in body.get("levels") or []:
            for key in ("open_order", "close_order"):
                item = level.get(key) or {}
                if item.get("order_id"):
                    used.add(item["order_id"])
                    fresher = ledger.get(item["order_id"])
                    if fresher and fresher.get("filled"):
                        level[key] = _saved_order_payload(fresher)
        for row in kept:
            if row["order_id"] in used:
                continue
            role = row.get("role") if row.get("role") in ("open_order", "close_order") else "open_order"
            target = next((level for level in body.get("levels") or [] if level.get("id") == row.get("level_id")), None)
            if target is not None and not (target.get(role) or {}).get("order_id"):
                target[role] = _saved_order_payload(row)
                used.add(row["order_id"])
            else:
                body.setdefault("retained_orders", []).append(_saved_order_payload(row))
    else:
        named = {item.get("order_id") for item in _saved_orders(body)}
        extra = [_saved_order_payload(row) for row in kept if row["order_id"] not in named]
        if extra:
            body["retained_orders"] = extra
    return body


def capture_executor(executor: Any, executor_type: str) -> Dict[str, Any]:
    """JSON-ready ownership snapshot. Unknown types are refused, not guessed."""
    if executor_type not in _CAPTURERS:
        raise ValueError(f"cannot checkpoint executor type {executor_type}")
    remember_current_slots(executor)
    body = _CAPTURERS[executor_type](executor)
    body["version"] = CHECKPOINT_VERSION
    body["type"] = executor_type
    return merge_ledger(body, order_ledger(executor))


def parse_checkpoint(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("version") != CHECKPOINT_VERSION:
        return None
    if parsed.get("type") not in SUPPORTED_TYPES:
        return None
    return parsed


def _saved_orders(node: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    if isinstance(node, dict):
        if "order_id" in node and "filled" in node:
            found.append(node)
        for value in node.values():
            found.extend(_saved_orders(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_saved_orders(value))
    return found


def saved_order_ids(checkpoint: Optional[Dict[str, Any]]) -> set:
    if not checkpoint:
        return set()
    return {item["order_id"] for item in _saved_orders(checkpoint) if item.get("order_id")}


def has_exposure(checkpoint: Dict[str, Any]) -> bool:
    """True when the snapshot already owns an order or an on-chain position."""
    if checkpoint.get("type") == "lp_executor" and (
        checkpoint.get("position_address") or checkpoint.get("saw_open_attempt")
    ):
        return True
    return any(item.get("order_id") for item in _saved_orders(checkpoint))


def _prices_match(left: Any, right: Any) -> bool:
    try:
        a = Decimal(str(left))
        b = Decimal(str(right))
    except (ArithmeticError, ValueError, TypeError):
        return False
    if a == 0 or b == 0:
        return a == b
    return abs(a - b) / max(abs(a), abs(b)) <= PRICE_MATCH


def _live_index(live_orders: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {order["order_id"]: order for order in live_orders if order.get("order_id")}


def resolve_saved_order(
    saved: Optional[Dict[str, Any]],
    live_by_id: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """How to reattach one saved order.

    live: the connector still tracks it — use that object.
    snapshot: it filled and left the book — use the saved order JSON.
    None: it is gone and was not filled — the level may place a replacement.
    """
    if not saved or not saved.get("order_id"):
        return None
    live = live_by_id.get(saved["order_id"])
    if live is not None:
        return {"order_id": saved["order_id"], "source": "live", "order": saved.get("order")}
    if saved.get("filled") and saved.get("order"):
        return {"order_id": saved["order_id"], "source": "snapshot", "order": saved["order"]}
    return None


# An order this close to the market is "at the current price", not at its level.
TOUCH_BAND = Decimal("0.003")


def _price_missing(value: Any) -> bool:
    """True for a missing quote, including NaN, which does not raise."""
    if value is None:
        return True
    if isinstance(value, float):
        return value != value
    if isinstance(value, Decimal):
        return value.is_nan()
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return True
    return parsed.is_nan()


def _near_market(price: Any, market: Any) -> bool:
    try:
        mid = Decimal(str(market))
        quoted = Decimal(str(price))
    except (ArithmeticError, ValueError, TypeError):
        return False
    if mid == 0:
        return False
    return abs(quoted - mid) / abs(mid) <= TOUCH_BAND


def _saved_ids(checkpoint: Dict[str, Any]) -> set:
    claimed = set()
    for level in checkpoint.get("levels") or []:
        for key in ("open_order", "close_order"):
            saved = level.get(key) or {}
            if saved.get("order_id"):
                claimed.add(saved["order_id"])
    for saved in checkpoint.get("retained_orders") or []:
        if saved.get("order_id"):
            claimed.add(saved["order_id"])
    return claimed


def merge_recent_ids(checkpoint: Dict[str, Any], order_ids: List[str]) -> Dict[str, Any]:
    """Add ids written to disk a moment before the database note."""
    claimed = _saved_ids(checkpoint)
    extra = [order_id for order_id in order_ids if order_id not in claimed]
    if not extra:
        return checkpoint
    updated = dict(checkpoint)
    retained = list(updated.get("retained_orders") or [])
    for order_id in extra:
        retained.append({
            "order_id": order_id,
            "filled": False,
            "order": None,
            "role": "open_order",
        })
    updated["retained_orders"] = retained
    return updated


def would_place_at_touch(level_price: Any, market_price: Any, side: Optional[str]) -> bool:
    """True when the grid would put this level on the current price, not its own."""
    try:
        price = Decimal(str(level_price))
        market = Decimal(str(market_price))
    except (ArithmeticError, ValueError, TypeError):
        return False
    if side == "BUY":
        return price >= market
    if side == "SELL":
        return price <= market
    return False


def _order_id(order: Any) -> Optional[str]:
    if isinstance(order, dict):
        raw = order.get("order_id") or order.get("client_order_id")
    else:
        raw = getattr(order, "client_order_id", None) or getattr(order, "order_id", None)
    return str(raw) if raw else None


def _order_price(order: Any) -> Any:
    if isinstance(order, dict):
        return order.get("price")
    return getattr(order, "price", None)


def _is_open(order: Any) -> bool:
    if isinstance(order, dict):
        return bool(order.get("is_open", True))
    return bool(getattr(order, "is_open", False))


def _side_name(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    name = getattr(raw, "name", None)
    text = str(name or raw).upper()
    if text.endswith("BUY"):
        return "BUY"
    if text.endswith("SELL"):
        return "SELL"
    return None


def _order_side(order: Any) -> Optional[str]:
    if isinstance(order, dict):
        return _side_name(order.get("side") or order.get("trade_type"))
    return _side_name(getattr(order, "trade_type", None) or getattr(order, "side", None))


def _level_side(level: Dict[str, Any]) -> Optional[str]:
    return _side_name(level.get("side"))


def _copy_levels(levels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    copied = []
    for level in levels:
        item = dict(level)
        if item.get("open_order"):
            item["open_order"] = dict(item["open_order"])
        copied.append(item)
    return copied


def seat_known_opens(
    levels: List[Dict[str, Any]],
    live_orders: List[Any],
    allowed_ids: set,
) -> set:
    """Seat an order this checkpoint already owns onto one empty level.

    A foreign id is not seated. Two matching levels are not a guess: the order
    stays loose and the resume plan caps a new order at that price.
    """
    claimed = set()
    for level in levels:
        for key in ("open_order", "close_order"):
            saved = level.get(key) or {}
            if saved.get("order_id"):
                claimed.add(saved["order_id"])
    seated = set()
    for order in live_orders:
        order_id = _order_id(order)
        if not order_id or order_id not in allowed_ids or order_id in claimed or not _is_open(order):
            continue
        side = _order_side(order)
        if side is None:
            continue
        matches = [
            level for level in levels
            if not (level.get("open_order") or {}).get("order_id")
            and _level_side(level) == side
            and _prices_match(_order_price(order), level.get("price"))
        ]
        if len(matches) != 1:
            continue
        payload = {"order_id": order_id, "filled": False, "order": None}
        if hasattr(order, "to_json"):
            payload["order"] = order.to_json()
        matches[0]["open_order"] = payload
        claimed.add(order_id)
        seated.add(order_id)
    return seated


def matches_blocked_price(level_price: Any, blocked_prices: List[Any]) -> bool:
    return any(_prices_match(level_price, blocked) for blocked in blocked_prices)


def grid_resume_plan(
    checkpoint: Dict[str, Any],
    live_orders: List[Dict[str, Any]],
    market_price: Any = None,
    side: Optional[str] = None,
) -> Dict[str, Any]:
    """How a grid may continue when one order was not in the last note.

    The grid stays alive. Orders whose numbers we have keep working. An order
    that is still open and not seated on a level blocks a new order at that
    price. Orders that would land on the current market price are capped.
    """
    levels = _copy_levels(checkpoint.get("levels") or [])
    seat_known_opens(levels, live_orders, _saved_ids(checkpoint))
    attached = set()
    for level in levels:
        for key in ("open_order", "close_order"):
            saved = level.get(key) or {}
            if saved.get("order_id"):
                attached.add(saved["order_id"])
    loose = [
        order for order in live_orders
        if _is_open(order) and _order_id(order) and _order_id(order) not in attached
    ]
    if not loose:
        return {
            "touch_budget": None,
            "notice": None,
            "ambiguous_ids": [],
            "blocked_prices": [],
            "blocked_orders": [],
            "touch_hold_ids": [],
            "touch_zero_unknown_market": False,
        }
    unknown_market = _price_missing(market_price)
    near = [] if unknown_market else [
        order for order in loose if _near_market(_order_price(order), market_price)
    ]
    budget = 0 if near or unknown_market else 1
    blocked_prices = [
        _text(_order_price(order)) for order in loose if _order_price(order) is not None
    ]
    blocked_orders = [
        {"order_id": _order_id(order), "price": _text(_order_price(order))}
        for order in loose
        if _order_id(order)
    ]
    pair = checkpoint.get("trading_pair") or "the grid"
    side_name = "buy" if side == "BUY" else "sell" if side == "SELL" else "either"
    if budget == 0:
        detail = (
            "An unrecognized order is already at the current price, "
            "so no new order is placed there."
        )
    else:
        detail = "At most one new order is placed at the current price."
    notice = (
        f"Grid {pair} ({side_name}) continued after restart. "
        f"{len(loose)} order(s) were missing from the last checkpoint. {detail} "
        "Other levels that would land on that same price are not restored. "
        "Known orders keep working."
    )
    return {
        "touch_budget": budget,
        "notice": notice,
        "ambiguous_ids": [_order_id(order) for order in loose],
        "blocked_prices": blocked_prices,
        "blocked_orders": blocked_orders,
        "touch_hold_ids": [_order_id(order) for order in near if _order_id(order)],
        "touch_zero_unknown_market": unknown_market,
    }


def grid_can_resume(
    checkpoint: Dict[str, Any],
    live_orders: List[Dict[str, Any]],
) -> Optional[str]:
    """A grid with a checkpoint always comes back. The plan caps new orders."""
    return None


def lp_can_resume(checkpoint: Dict[str, Any]) -> Optional[str]:
    """A fresh LP start mints a new position. Refuse if an open was already tried."""
    if checkpoint.get("position_address"):
        return None
    if checkpoint.get("saw_open_attempt"):
        return "LP open was already attempted and the position address was not saved"
    return None


def can_resume(checkpoint: Optional[Dict[str, Any]], live_orders: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """None when this checkpoint may be started again. Otherwise the reason."""
    if not checkpoint:
        return "no checkpoint"
    if checkpoint.get("type") not in SUPPORTED_TYPES:
        return f"unsupported type {checkpoint.get('type')}"
    if checkpoint.get("type") == "grid_executor":
        return grid_can_resume(checkpoint, live_orders or [])
    if checkpoint.get("type") == "lp_executor":
        return lp_can_resume(checkpoint)
    # A checkpoint that has not recorded an order yet must not start beside an
    # open order on the same pair: that order may be the one this crash lost,
    # and a fresh start would place a second.
    if not has_exposure(checkpoint):
        unnamed = [
            order for order in (live_orders or [])
            if order.get("is_open") and order.get("order_id")
        ]
        if unnamed:
            return "exchange has an open order this checkpoint does not name"
    return None


def live_order_view(order: Any) -> Dict[str, Any]:
    return {
        "order_id": getattr(order, "client_order_id", None),
        "price": _text(getattr(order, "price", None)),
        "trading_pair": getattr(order, "trading_pair", None),
        "side": _order_side(order),
        "is_open": bool(getattr(order, "is_open", False)),
        "is_filled": bool(getattr(order, "is_filled", False)),
    }


def _attach(saved: Optional[Dict[str, Any]], live_by_id: Dict[str, Any]) -> Any:
    """A TrackedOrder bound to the live in-flight order, or to the saved fill."""
    from hummingbot.core.data_type.in_flight_order import InFlightOrder
    from hummingbot.strategy_v2.models.executors import TrackedOrder

    if not saved or not saved.get("order_id"):
        return None
    resolved = resolve_saved_order(saved, {
        order_id: {"order_id": order_id} for order_id in live_by_id
    })
    # resolve_saved_order only needs the id to choose live vs snapshot vs drop.
    live = live_by_id.get(saved["order_id"])
    if live is not None:
        tracked = TrackedOrder(order_id=saved["order_id"])
        tracked.order = live
        return tracked
    if resolved and resolved["source"] == "snapshot" and resolved.get("order"):
        tracked = TrackedOrder(order_id=saved["order_id"])
        tracked.order = InFlightOrder.from_json(resolved["order"])
        return tracked
    return None




def seed_ledger(executor: Any, checkpoint: Dict[str, Any]) -> None:
    """The resumed executor keeps the same ids, so the next cancel can drop them."""
    ledger = order_ledger(executor)
    for item in _saved_orders(checkpoint):
        order_id = item.get("order_id")
        if not order_id:
            continue
        ledger[order_id] = {
            "order_id": order_id,
            "filled": bool(item.get("filled")),
            "gone": False,
            "order": item.get("order"),
            "level_id": item.get("level_id"),
            "role": item.get("role"),
        }


def apply_checkpoint(executor: Any, checkpoint: Dict[str, Any], live_orders: List[Any]) -> None:
    """Put the saved ownership back on a freshly built executor. Call before start()."""
    from decimal import Decimal as D
    from hummingbot.core.data_type.common import OrderType, TradeType
    from hummingbot.strategy_v2.executors.grid_executor.data_types import GridLevel
    from hummingbot.strategy_v2.executors.lp_executor.data_types import LPExecutorState

    live_by_id = {
        getattr(order, "client_order_id", None): order
        for order in live_orders
        if getattr(order, "client_order_id", None)
    }
    kind = checkpoint.get("type")

    if kind == "grid_executor":
        seat_known_opens(
            checkpoint.get("levels") or [],
            live_orders,
            _saved_ids(checkpoint),
        )
        restored = []
        for raw in checkpoint.get("levels") or []:
            level = GridLevel(
                id=raw["id"],
                price=D(raw["price"]),
                amount_quote=D(raw["amount_quote"]),
                take_profit=D(raw["take_profit"]),
                side=TradeType[raw["side"]],
                open_order_type=OrderType[raw["open_order_type"]],
                take_profit_order_type=OrderType[raw["take_profit_order_type"]],
            )
            level.active_open_order = _attach(raw.get("open_order"), live_by_id)
            level.active_close_order = _attach(raw.get("close_order"), live_by_id)
            restored.append(level)
        executor.grid_levels = restored
        executor._close_order = _attach(checkpoint.get("close_order"), live_by_id)
        if hasattr(executor, "update_grid_levels"):
            executor.update_grid_levels()
        seed_ledger(executor, checkpoint)
        return

    if kind == "position_executor":
        executor._open_order = _attach(checkpoint.get("open_order"), live_by_id)
        executor._close_order = _attach(checkpoint.get("close_order"), live_by_id)
        executor._take_profit_limit_order = _attach(checkpoint.get("take_profit_order"), live_by_id)
        seed_ledger(executor, checkpoint)
        return

    if kind == "dca_executor":
        executor._open_orders = [
            tracked for tracked in (
                _attach(item, live_by_id) for item in checkpoint.get("open_orders") or []
            ) if tracked is not None
        ]
        executor._close_orders = [
            tracked for tracked in (
                _attach(item, live_by_id) for item in checkpoint.get("close_orders") or []
            ) if tracked is not None
        ]
        seed_ledger(executor, checkpoint)
        return

    if kind == "order_executor":
        executor._order = _attach(checkpoint.get("order"), live_by_id)
        seed_ledger(executor, checkpoint)
        return

    if kind == "twap_executor":
        plan = {}
        for item in checkpoint.get("plan") or []:
            plan[float(item["timestamp"])] = _attach(item.get("order"), live_by_id)
        executor._order_plan = plan
        if checkpoint.get("start_timestamp") is not None:
            executor._start_timestamp = float(checkpoint["start_timestamp"])
        seed_ledger(executor, checkpoint)
        return

    if kind == "arbitrage_executor":
        buy = _attach(checkpoint.get("buy_order"), live_by_id)
        sell = _attach(checkpoint.get("sell_order"), live_by_id)
        if buy is not None:
            executor._buy_order = buy
        if sell is not None:
            executor._sell_order = sell
        seed_ledger(executor, checkpoint)
        return

    if kind == "xemm_executor":
        maker = _attach(checkpoint.get("maker_order"), live_by_id)
        taker = _attach(checkpoint.get("taker_order"), live_by_id)
        if maker is not None:
            executor.maker_order = maker
        if taker is not None:
            executor.taker_order = taker
        seed_ledger(executor, checkpoint)
        return

    if kind == "lp_executor":
        state = LPExecutorState.model_validate(checkpoint.get("state_json") or {})
        state.active_open_order = _attach(checkpoint.get("open_order"), live_by_id)
        state.active_close_order = _attach(checkpoint.get("close_order"), live_by_id)
        executor.lp_position_state = state
        seed_ledger(executor, checkpoint)
        return

    raise ValueError(f"cannot apply checkpoint for {kind}")

def cap_arguments(plan: Any) -> Dict[str, Any]:
    """Copy installer inputs without turning a missing key into an empty list."""
    if not isinstance(plan, dict):
        return {}
    keys = (
        "touch_budget",
        "blocked_orders",
        "blocked_prices",
        "touch_hold_ids",
        "touch_zero_unknown_market",
    )
    return {key: plan[key] for key in keys if key in plan}


def _decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, Decimal):
        return None if value.is_nan() else value
    if isinstance(value, float) and value != value:
        return None
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None
    if parsed.is_nan():
        return None
    return parsed


def _positive_tick(executor: Any) -> Optional[Decimal]:
    rules = getattr(executor, "trading_rules", None)
    tick = _decimal(getattr(rules, "min_price_increment", None))
    if tick is None or tick <= 0:
        return None
    return tick


def _quantize(price: Any, tick: Decimal) -> Optional[Decimal]:
    number = _decimal(price)
    if number is None:
        return None
    return (number // tick) * tick


def _same_tick(left: Any, right: Any, tick: Optional[Decimal]) -> Optional[bool]:
    """True when both prices land on one tick. None means the tick cannot be used."""
    if tick is None:
        return None
    quantized_left = _quantize(left, tick)
    quantized_right = _quantize(right, tick)
    if quantized_left is None or quantized_right is None:
        return None
    return quantized_left == quantized_right


def _side_from_config(executor: Any) -> Optional[str]:
    side = getattr(getattr(executor, "config", None), "side", None)
    name = getattr(side, "name", None)
    if name in ("BUY", "SELL"):
        return name
    return None


def _spread(executor: Any) -> Optional[Decimal]:
    spread = _decimal(getattr(getattr(executor, "config", None), "safe_extra_spread", None))
    if spread is None or spread < 0:
        return None
    return spread


def _quote(executor: Any) -> Optional[Decimal]:
    return _decimal(getattr(executor, "current_open_quote", None))


def _touch_formula(quote: Decimal, side: str, spread: Decimal) -> Decimal:
    if side == "BUY":
        return quote * (1 - spread)
    return quote * (1 + spread)


def _placement(level_price: Any, quote: Decimal, side: str, spread: Decimal) -> Optional[Decimal]:
    price = _decimal(level_price)
    if price is None:
        return None
    if side == "BUY" and price >= quote:
        return _touch_formula(quote, side, spread)
    if side == "SELL" and price <= quote:
        return _touch_formula(quote, side, spread)
    return price


def _pair_name(executor: Any) -> Optional[str]:
    return getattr(getattr(executor, "config", None), "trading_pair", None)


def _on_pair(order: Any, pair: Optional[str]) -> bool:
    if not pair:
        return True
    order_pair = getattr(order, "trading_pair", None)
    if order_pair is None and isinstance(order, dict):
        order_pair = order.get("trading_pair")
    return order_pair in (None, pair)


def _read_order_book(executor: Any) -> Optional[Dict[str, Any]]:
    """Active book plus tracker caches. None means the active book was not read."""
    connectors = getattr(executor, "connectors", None)
    if not connectors:
        return None
    pair = _pair_name(executor)
    active: List[Any] = []
    cached: List[Any] = []
    lost: List[Any] = []
    ready = True
    try:
        values = list(connectors.values())
        if not values:
            return None
        for connector in values:
            if getattr(connector, "ready", None) is not True:
                ready = False
            inflight = getattr(connector, "in_flight_orders", None)
            if not isinstance(inflight, dict):
                return None
            for order in inflight.values():
                if _on_pair(order, pair):
                    active.append(order)
            tracker = getattr(connector, "_order_tracker", None)
            if tracker is None:
                continue
            for order in (getattr(tracker, "cached_orders", None) or {}).values():
                if _on_pair(order, pair):
                    cached.append(order)
            for order in (getattr(tracker, "lost_orders", None) or {}).values():
                if _on_pair(order, pair):
                    lost.append(order)
    except Exception:
        logger.warning("grid resume book unreadable", exc_info=True)
        return None
    return {"ready": ready, "active": active, "cached": cached, "lost": lost}


def _cached_finished(book: Optional[Dict[str, Any]], remembered: set) -> set:
    """Completions already in the tracker cache. An unread active book is not a completion."""
    done = set(remembered)
    if not book:
        return done
    for order in book["cached"]:
        order_id = _order_id(order)
        if order_id and not _is_open(order):
            done.add(order_id)
    return done


def _finished_ids(book: Optional[Dict[str, Any]], remembered: set) -> set:
    done = _cached_finished(book, remembered)
    if not book or not book["ready"]:
        return done
    for order in book["active"]:
        order_id = _order_id(order)
        if order_id and not _is_open(order):
            done.add(order_id)
    return done


def _rows(value: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(value, list):
        return None
    return [row for row in value if isinstance(row, dict)]


def _unfinished_rows(rows: Optional[List[Dict[str, Any]]], finished: set) -> List[Dict[str, Any]]:
    if not rows:
        return []
    kept = []
    for row in rows:
        order_id = row.get("order_id")
        if order_id and order_id in finished:
            continue
        kept.append(row)
    return kept


def _eternal_prices(prices: Any, pairs: Optional[List[Dict[str, Any]]], tick: Optional[Decimal]) -> List[Any]:
    if not isinstance(prices, list):
        return []
    if not isinstance(pairs, list):
        return list(prices)
    eternal = []
    for price in prices:
        paired = False
        for row in pairs:
            same = _same_tick(price, row.get("price"), tick)
            if same is True:
                paired = True
                break
        if not paired:
            eternal.append(price)
    return eternal


def _order_at_price(book: Optional[Dict[str, Any]], price: Any, tick: Optional[Decimal]) -> bool:
    if book is None or tick is None or price is None:
        return True
    for order in list(book["active"]) + list(book["lost"]):
        if order in book["active"] and not _is_open(order):
            continue
        if _same_tick(_order_price(order), price, tick) is True:
            return True
    return False


def _should_install(arguments: Dict[str, Any]) -> bool:
    orders = arguments.get("blocked_orders", None)
    holds = arguments.get("touch_hold_ids", None)
    prices = arguments.get("blocked_prices", None)
    if isinstance(orders, list) and orders:
        return True
    if isinstance(holds, list) and holds:
        return True
    if isinstance(prices, list) and prices and not isinstance(orders, list):
        return True
    if arguments.get("touch_zero_unknown_market"):
        return True
    if "touch_budget" in arguments and arguments["touch_budget"] is not None:
        return True
    return False


def install_grid_resume_cap(executor: Any, plan: Any) -> bool:
    """Wrap order proposals. False when there is nothing to limit."""
    arguments = cap_arguments(plan)
    if not _should_install(arguments):
        return False
    _bind_precise_cap(executor, arguments)
    return True


def blank_grid_cap(executor: Any) -> None:
    """Last resort: do not leave the original proposer in place."""
    executor.get_open_orders_to_create = lambda: []


def conservative_grid_cap(executor: Any, plan: Any) -> None:
    """Block known prices without ever turning a zero into one."""
    arguments = cap_arguments(plan)
    if not _should_install(arguments):
        return
    _bind_conservative_cap(executor, arguments)


def _known_prices(arguments: Dict[str, Any]) -> List[Any]:
    prices = list(arguments.get("blocked_prices") or [])
    orders = arguments.get("blocked_orders")
    if isinstance(orders, list):
        for row in orders:
            if isinstance(row, dict) and row.get("price") is not None:
                prices.append(row.get("price"))
    return prices


def _bind_conservative_cap(executor: Any, arguments: Dict[str, Any]) -> None:
    initial = arguments["touch_budget"] if "touch_budget" in arguments else 0
    executor._touch_initial_budget = initial
    executor._touch_place_budget = 0 if initial == 0 else initial
    executor._touch_zero_released = False
    original = executor.get_open_orders_to_create
    banned = _known_prices(arguments)

    def wrapped():
        try:
            proposed = list(original())
        except Exception:
            logger.warning("grid proposal failed under conservative cap", exc_info=True)
            return []
        allowed = []
        for level in proposed:
            price = getattr(level, "price", None)
            if any(str(price) == str(item) for item in banned):
                continue
            if initial == 0 and _conservative_touch(level, executor):
                continue
            allowed.append(level)
        return allowed

    executor.get_open_orders_to_create = wrapped


def _conservative_touch(level: Any, executor: Any) -> bool:
    side = getattr(getattr(executor, "config", None), "side", None)
    name = getattr(side, "name", None)
    quote = _decimal(getattr(executor, "current_open_quote", None))
    price = _decimal(getattr(level, "price", None))
    if name not in ("BUY", "SELL") or quote is None or price is None:
        return True
    if name == "BUY":
        return price >= quote
    return price <= quote


def _bind_precise_cap(executor: Any, arguments: Dict[str, Any]) -> None:
    initial = arguments["touch_budget"] if "touch_budget" in arguments else None
    executor._touch_initial_budget = initial
    executor._touch_place_budget = initial
    executor._touch_zero_released = initial in (1, None)
    executor._seen_finished = set()
    executor._seen_open = set()
    executor._cap_arguments = arguments
    original = executor.get_open_orders_to_create

    def wrapped():
        try:
            proposed = list(original())
        except Exception:
            logger.warning("grid proposal failed under resume cap", exc_info=True)
            return []
        try:
            return _filter_proposals(executor, proposed)
        except Exception:
            logger.warning("grid resume cap failed closed", exc_info=True)
            return []

    executor.get_open_orders_to_create = wrapped


def _filter_proposals(executor: Any, proposed: List[Any]) -> List[Any]:
    arguments = executor._cap_arguments
    book = _read_order_book(executor)
    if book is None or not book["ready"]:
        executor._seen_finished = _cached_finished(book, executor._seen_finished)
        return []
    finished = _finished_ids(book, executor._seen_finished)
    seen_open = _remember_open_ids(book, executor._seen_open)
    executor._seen_finished = finished
    executor._seen_open = seen_open
    tick = _positive_tick(executor)
    side = _side_from_config(executor)
    quote = _quote(executor)
    spread = _spread(executor)
    pairs = _rows(arguments.get("blocked_orders")) if "blocked_orders" in arguments else None
    prices = arguments.get("blocked_prices")
    if tick is None and (pairs or (isinstance(prices, list) and prices)):
        return []
    if side is None or quote is None or spread is None:
        return []
    touch_entry = _touch_formula(quote, side, spread)
    unfinished = _unfinished_rows(pairs, finished)
    eternal = _eternal_prices(prices, pairs, tick)
    if _uncomparable(unfinished, eternal, book, tick):
        return []
    allowed = []
    touch_returned = False
    for level in proposed:
        level_price = getattr(level, "price", None)
        placement = _placement(level_price, quote, side, spread)
        if placement is None or tick is None:
            continue
        touching = _same_tick(placement, touch_entry, tick) is True
        if _blocked(placement, tick, unfinished, eternal, book):
            continue
        if not touching:
            allowed.append(level)
            continue
        if touch_returned:
            continue
        if not _allow_touch(
            executor, arguments, book, finished, seen_open, unfinished, eternal, touch_entry, tick,
        ):
            continue
        if not _spend_touch(
            executor, arguments, book, finished, seen_open, unfinished, eternal, touch_entry, tick,
        ):
            continue
        touch_returned = True
        allowed.append(level)
    return allowed


def _uncomparable(unfinished, eternal, book, tick) -> bool:
    """A price we cannot compare is not proof that it differs from the level."""
    sources = [row.get("price") for row in unfinished]
    sources.extend(eternal)
    if book:
        for order in book["active"]:
            if _is_open(order):
                sources.append(_order_price(order))
        for order in book["lost"]:
            sources.append(_order_price(order))
    for price in sources:
        if _decimal(price) is None or tick is None or _quantize(price, tick) is None:
            return True
    return False


def _blocked(
    placement: Decimal,
    tick: Decimal,
    unfinished: List[Dict[str, Any]],
    eternal: List[Any],
    book: Dict[str, Any],
) -> bool:
    for row in unfinished:
        if row.get("price") is None:
            continue
        if _same_tick(row.get("price"), placement, tick) is True:
            return True
    for price in eternal:
        if _same_tick(price, placement, tick) is True:
            return True
    return _order_at_price(book, placement, tick)


def _holds_done(arguments: Dict[str, Any], finished: set) -> bool:
    holds = arguments.get("touch_hold_ids") or []
    if not isinstance(holds, list):
        return False
    return all(item in finished for item in holds)


def _remember_open_ids(book: Optional[Dict[str, Any]], remembered: set) -> set:
    seen = set(remembered)
    if not book:
        return seen
    for order in book["active"]:
        order_id = _order_id(order)
        if order_id and _is_open(order):
            seen.add(order_id)
    return seen


def _lost_ids(book: Optional[Dict[str, Any]]) -> set:
    if not book:
        return set()
    return {order_id for order in book["lost"] if (order_id := _order_id(order))}


def _unseen_or_lost(arguments: Dict[str, Any], book, finished: set, seen_open: set) -> bool:
    """An id we have not seen finished still holds the zero, whatever its price."""
    if "blocked_orders" not in arguments or not isinstance(arguments.get("blocked_orders"), list):
        return False
    lost = _lost_ids(book)
    for row in arguments["blocked_orders"]:
        if not isinstance(row, dict):
            return True
        order_id = row.get("order_id")
        if not order_id or order_id in lost:
            return True
        if order_id not in finished and order_id not in seen_open:
            return True
    return False


def _allow_touch(executor, arguments, book, finished, seen_open, unfinished, eternal, touch_entry, tick) -> bool:
    remaining = executor._touch_place_budget
    if remaining is None:
        return True
    if remaining > 0:
        return True
    return _can_release_zero(
        executor, arguments, book, finished, seen_open, unfinished, eternal, touch_entry, tick,
    )


def _can_release_zero(executor, arguments, book, finished, seen_open, unfinished, eternal, touch_entry, tick) -> bool:
    if executor._touch_initial_budget != 0 or executor._touch_zero_released:
        return False
    if book is None or not book["ready"] or tick is None:
        return False
    if not _holds_done(arguments, finished):
        return False
    if _unseen_or_lost(arguments, book, finished, seen_open):
        return False
    if "blocked_orders" not in arguments and isinstance(arguments.get("blocked_prices"), list) and arguments["blocked_prices"]:
        return False
    for row in unfinished:
        if _decimal(row.get("price")) is None:
            return False
        if _same_tick(row.get("price"), touch_entry, tick) is True:
            return False
    for price in eternal:
        same = _same_tick(price, touch_entry, tick)
        if same is None or same:
            return False
    if _order_at_price(book, touch_entry, tick):
        return False
    return True


def _spend_touch(executor, arguments, book, finished, seen_open, unfinished, eternal, touch_entry, tick) -> bool:
    remaining = executor._touch_place_budget
    if remaining is None:
        return True
    if remaining == 0:
        if not _can_release_zero(
            executor, arguments, book, finished, seen_open, unfinished, eternal, touch_entry, tick,
        ):
            return False
        executor._touch_place_budget = 1
        remaining = 1
    if remaining <= 0:
        return False
    executor._touch_place_budget = remaining - 1
    executor._touch_zero_released = True
    return True
