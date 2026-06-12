---
id: READ-021
title: Dead method _wait_for_order_book_ready never called
category: readability
impact: medium
effort: S
risk: low
files:
  - services/accounts_service.py:180-213
commits: []
status: todo
created: 2026-06-11
---

## Problema
services/accounts_service.py:180 defines async method `_wait_for_order_book_ready` (33 lines, lines 180-213) with full docstring and polling logic. A grep across services/, routers/ and utils/ finds zero call sites. It is duplicated functionality of what `market_data_service.initialize_order_book(...)` already does (called from `add_market`). It is pure dead code that readers must still parse and that suggests a code path that no longer exists.

## Solución propuesta
Delete the entire `_wait_for_order_book_ready` method (services/accounts_service.py:180-213). If a future caller needs order-book readiness it should use the market_data_service path already used in `add_market`.

## Criterio de aceptación
- [ ] Method `_wait_for_order_book_ready` is removed
- [ ] grep -rn "_wait_for_order_book_ready" returns no matches
- [ ] Test suite / app startup unaffected
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. The async method `_wait_for_order_book_ready` is defined at services/accounts_service.py lines 180-213 with a full docstring and polling logic, exactly as described. A grep for `_wait_for_order_book_ready` across all .py files returns only the definition line — zero call sites. It is genuinely dead code. Its functionality (waiting for an order book to become ready) is already covered in `add_market` (lines 159-175), which calls `market_data_service.initialize_order_book(...)` with a timeout. The method is a private helper that overrides nothing in any base class
