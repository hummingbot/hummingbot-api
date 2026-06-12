---
id: PERF-001
title: save_account_state commits once per connector, multiplying transaction round-trips in the periodic dump
category: performance
impact: high
effort: M
risk: medium
files:
  - database/repositories/account_repository.py
  - services/accounts_service.py
  - database/connection.py
commits:
  - "2c65cdf (perf) PERF-001: commit account snapshots once per dump"
status: done
created: 2026-06-11
---

## Problema
AccountRepository.save_account_state ends with `await self.session.commit()` (account_repository.py:97). dump_account_state (accounts_service.py:686-693) calls it inside a nested loop over every account x connector under a single session_context. Each connector therefore triggers its own COMMIT (a separate DB round-trip / fsync). With N accounts and M connectors this is N*M commits every update cycle (the loop runs every account_update_interval, default 5 min, plus on every /portfolio/state refresh). The session_context wrapping is wasted because the inner commit closes the transaction each iteration.

## Solución propuesta
Remove the per-call `await self.session.commit()` from save_account_state (keep only the flush to obtain the AccountState id). Let the single outer session_context in dump_account_state own the transaction and commit once after all account/connector rows are added (or commit explicitly once after the loop). This collapses N*M commits into one transaction per snapshot.

## Criterio de aceptación
- [x] save_account_state no longer calls session.commit(); it only flushes to get the id
- [x] dump_account_state performs exactly one commit per snapshot regardless of account/connector count
- [x] A snapshot with multiple accounts/connectors persists all token_states atomically and reads back identically to before
- [x] Existing tests in test/ still pass
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Confirmed against the real code. save_account_state ends with `await self.session.commit()` at account_repository.py:97, and dump_account_state (accounts_service.py:686-693) calls it inside a nested loop over accounts x connectors under one get_session_context. So each connector triggers its own COMMIT/fsync round-trip => N*M commits per periodic snapshot. The fix is valid and low-risk: get_session_context (database/connection.py:134) already commits on successful exit, so simply removing the per-call commit (keeping only `await self.session.flush()` to obtain the AccountState id, which is pre

database/connection.py no requirió cambios: get_session_context ya commitea al salir; el fix consistió en quitar el commit por conector del repositorio.
