---
id: CORR-008
title: _last_known_prices is a shared class-level mutable dict on AccountsService
category: correctness
impact: low
effort: S
risk: low
files:
  - services/accounts_service.py:449 (declaration)
  - services/accounts_service.py:904 (mutation)
  - services/accounts_service.py:910-912,923-925 (reads)
commits:
  - "5dc3633 (fix) CORR-008: make _last_known_prices per-instance state"
status: done
created: 2026-06-11
---

## Problema
`_last_known_prices = {}` is declared as a class attribute (services/accounts_service.py:449), not an instance attribute. It is mutated through `self._last_known_prices[pair] = price` in `_safe_get_last_traded_prices` (services/accounts_service.py:904) and read in `_get_fallback_prices` (services/accounts_service.py:923). Because it lives on the class, the cache is shared across every AccountsService instance ever created (tests, multiple wirings, future multi-instance use), so cached last-traded prices from one logical context leak into another. It is also unbounded and never evicted, so it grows for every trading pair seen for the lifetime of the process.

## Solución propuesta
Move the cache to instance state by initializing `self._last_known_prices = {}` in __init__ instead of at class scope, so each AccountsService owns its own cache. If unbounded growth is a concern, back it with a bounded structure (e.g. an LRU/`functools` cache or a capped dict) keyed by trading pair.

## Criterio de aceptación
- [x] _last_known_prices is initialized per-instance in __init__, not as a class attribute
- [x] Two AccountsService instances do not share the same price cache
- [x] Reads/writes at services/accounts_service.py:904 and :923 operate on the instance cache
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. `_last_known_prices = {}` is declared at class scope (services/accounts_service.py:449), not in __init__ (lines 451-469 contain no such init). It is mutated via `self._last_known_prices[pair] = price` (line 904) and read in `_safe_get_last_traded_prices` (lines 910-912) and `_get_fallback_prices` (lines 923-925). All factual claims hold; the only minor inaccuracy is that the finding attributes the read solely to `_get_fallback_prices` while it is read in both methods. This is a genuine mutable-class-attribute anti-pattern: (1) cross-instance sharing is real and 

La sugerencia opcional de cache acotado no se implementó (no era criterio).
