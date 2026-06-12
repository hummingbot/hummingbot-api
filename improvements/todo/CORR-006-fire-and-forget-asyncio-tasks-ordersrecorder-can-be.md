---
id: CORR-006
title: Fire-and-forget asyncio tasks in OrdersRecorder can be garbage-collected, dropping order/trade DB writes
category: correctness
impact: high
effort: S
risk: low
files:
  - services/orders_recorder.py
  - services/funding_recorder.py
commits: []
status: todo
created: 2026-06-11
---

## Problema
The connector event callbacks `_did_create_order`, `_did_fill_order`, `_did_cancel_order`, `_did_fail_order`, `_did_complete_order` (services/orders_recorder.py:115, :122, :129, :136, :143) each call `asyncio.create_task(self._handle_*(...))` without keeping a reference to the returned Task. The event loop only holds a weak reference to a bare task, so the GC can collect a still-pending task before it finishes, silently aborting the database write for an order creation, fill, cancellation, failure, or completion. These callbacks are the sole persistence path for orders and trades, so a lost task means a lost order/trade record (or a fill recorded against a never-created order). The same defect exists in services/funding_recorder.py:60 (`_did_funding_payment` -> `asyncio.create_task(self._handle_funding_payment(event))`), dropping funding-payment records.

## Solución propuesta
Retain a strong reference to each created task until it completes. Add a `self._pending_tasks: set[asyncio.Task] = set()` to OrdersRecorder (and FundingRecorder), and in every `_did_*` callback do `task = asyncio.create_task(...)`, `self._pending_tasks.add(task)`, `task.add_done_callback(self._pending_tasks.discard)`. This guarantees the loop keeps the task alive for its full lifetime and lets exceptions surface in the done callback. Optionally drain/await `self._pending_tasks` in `stop()` so in-flight writes complete before listeners are removed.

## Criterio de aceptación
- [ ] Every `asyncio.create_task` in orders_recorder.py and funding_recorder.py stores the task in a set and removes it via add_done_callback
- [ ] Order/trade/funding records are persisted reliably under load (no lost writes) when many events fire concurrently
- [ ] stop() does not leave dangling references and in-flight write tasks are awaited or cancelled deterministically
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real source. Confirmed at exact lines: orders_recorder.py:115 (_did_create_order), :122 (_did_fill_order), :129 (_did_cancel_order), :136 (_did_fail_order), :143 (_did_complete_order), and funding_recorder.py:60 (_did_funding_payment) each call asyncio.create_task(...) and discard the returned Task without retaining a reference. This matches the documented CPython behavior where the event loop holds only weak references to tasks (asyncio docs explicitly warn to keep a strong reference), so a still-pending task can be garbage-collected before completing, silently aborting t
