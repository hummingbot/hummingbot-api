---
id: CORR-007
title: dump_account_state iterates accounts_state while other coroutines mutate it (RuntimeError: dictionary changed size during iteration)
category: correctness
impact: high
effort: M
risk: medium
files:
  - services/accounts_service.py
commits: []
status: todo
created: 2026-06-11
---

## Problema
`dump_account_state` (services/accounts_service.py:690-693) iterates `self.accounts_state.items()` and the inner `connectors.items()` and awaits `repository.save_account_state(...)` inside the loop (services/accounts_service.py:693). The `await` yields control while iterating the live dict. Concurrently, REST-triggered `update_account_state` reassigns `self.accounts_state[account][connector]` and may create new account keys (services/accounts_service.py:789, :815-817), `_update_gateway_balances` deletes stale keys from `self.accounts_state['master_account']` (services/accounts_service.py:2008), and `delete_credentials`/`delete_account` pop keys (services/accounts_service.py:1003, :1042). If any of these run during the dump's await points, Python raises `RuntimeError: dictionary changed size during iteration`, aborting the dump (and the same exposure exists for the read-only aggregators get_portfolio_distribution/get_account_distribution at services/accounts_service.py:1212 and :1310).

## Solución propuesta
Snapshot the structure before iterating so the dump operates on a stable copy: e.g. `snapshot = {acc: dict(conns) for acc, conns in self.accounts_state.items()}` taken synchronously (no awaits) at the top of dump_account_state, then iterate `snapshot`. Alternatively, guard all reads/writes of `accounts_state` with the existing asyncio.Lock pattern used elsewhere. Apply the same defensive copy in the in-memory aggregation paths that iterate accounts_state.

## Criterio de aceptación
- [ ] dump_account_state iterates over a local copy of accounts_state, not the live dict
- [ ] No `RuntimeError: dictionary changed size during iteration` occurs when a balance update, gateway stale-key removal, or credential deletion runs concurrently with a dump
- [ ] get_portfolio_distribution and get_account_distribution also iterate snapshots or are lock-protected
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: REAL y vale la pena. Verifiqué el código real en /Users/dman/Documents/work/hummingbot-api/services/accounts_service.py.

dump_account_state (lineas 674-698) itera self.accounts_state.items() (linea 690) y connectors.items() (linea 691), y dentro del bucle hace `await repository.save_account_state(...)` (linea 693). Ese await es un punto de suspension de I/O real (escritura a DB) que cede el control al event loop MIENTRAS se itera el dict vivo.

Concurrencia confirmada: los endpoints REST en routers/portfolio.py:34 (update_account_state) y routers/accounts.py:87/109/135 (delete_account/delete_
