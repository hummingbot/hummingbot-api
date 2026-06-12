---
id: CORR-009
title: BotsOrchestrator.stop spawns mqtt_manager.stop() as an unretained fire-and-forget task and may run with no event loop
category: correctness
impact: medium
effort: S
risk: low
files:
  - services/bots_orchestrator.py:90
  - services/bots_orchestrator.py:101
  - main.py:299
commits:
  - "62f463f (fix) CORR-009: await MQTT teardown in BotsOrchestrator.stop"
status: done
created: 2026-06-11
---

## Problema
`BotsOrchestrator.stop` is a synchronous method (services/bots_orchestrator.py:90) that cancels the update/performance tasks and then calls `asyncio.create_task(self.mqtt_manager.stop())` (services/bots_orchestrator.py:101). The task is not retained, so it can be garbage-collected before completing (same weak-reference issue as the recorders), meaning the MQTT manager may never actually be shut down and its connection/subscriptions leak. Worse, because stop() is sync and fires-and-forgets, during application shutdown the event loop can stop/close before the task runs, in which case `mqtt_manager.stop()` never executes at all and `asyncio.create_task` may raise if no loop is running.

## Solución propuesta
Make stop() awaitable: convert it to `async def stop(self)` and `await self.mqtt_manager.stop()` after cancelling the loop tasks (also `await` the cancelled tasks to swallow CancelledError), and update the shutdown caller to await it. If stop() must remain sync for compatibility, at minimum retain the task in an attribute and ensure shutdown awaits it before the loop closes.

## Criterio de aceptación
- [x] mqtt_manager.stop() is awaited (or its task is retained and awaited) during orchestrator shutdown
- [x] MQTT connection and subscriptions are reliably torn down on shutdown with no leaked task warnings
- [x] No 'Task was destroyed but it is pending' or 'no running event loop' errors during shutdown
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. services/bots_orchestrator.py:90 defines `def stop(self)` (sync), it cancels `_update_bots_task` and `_performance_dump_task`, then at line 101 calls `asyncio.create_task(self.mqtt_manager.stop())` fire-and-forget without retaining the task. The sole caller is the FastAPI lifespan shutdown handler at main.py:299 (`bots_orchestrator.stop()`), which is NOT awaited; after it, the handler proceeds through several awaited cleanups and returns, after which the event loop is torn down. The scheduled task can therefore be GC'd or simply never run to completion, so `MQTT
