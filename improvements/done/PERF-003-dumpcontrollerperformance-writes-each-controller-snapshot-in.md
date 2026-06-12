---
id: PERF-003
title: dump_controller_performance writes each controller snapshot individually instead of batching
category: performance
impact: medium
effort: S
risk: low
files:
  - services/bots_orchestrator.py
  - database/repositories/controller_performance_repository.py
commits:
  - "0d459f4 (perf) PERF-003: batch controller performance snapshots"
status: done
created: 2026-06-11
---

## Problema
dump_controller_performance (bots_orchestrator.py:405-414) calls repo.save_controller_performance once per controller inside nested loops over bots and controllers. save_controller_performance (controller_performance_repository.py:64-66) does session.add + await session.flush() per call, so each controller triggers its own flush round-trip. This periodic dump (every performance_dump_interval, default 5 min) scales as bots*controllers individual flushes within one session.

## Solución propuesta
Add a bulk path: build all ControllerPerformanceSnapshot objects first and use session.add_all(...) with a single flush/commit, or accumulate them and flush once after the loops. Avoid the per-row flush; the snapshot rows do not need their generated ids during the loop.

## Criterio de aceptación
- [x] All controller snapshots for one dump are persisted with a single add_all/flush rather than one flush per controller
- [x] Saved row count and content are unchanged vs the per-row implementation
- [x] saved_count logging still reflects the number of rows written
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. The finding is accurate and file:line references are correct.

bots_orchestrator.py:405-414: dump_controller_performance loops over active bots and, for each, over performance_data.items(), calling repo.save_controller_performance once per controller inside a single shared session.

controller_performance_repository.py:64-66: save_controller_performance does session.add(snapshot) followed by `await self.session.flush()` on every single call. So each controller triggers its own flush round-trip to the DB. With N bots and M controllers each, that is N*M individual
