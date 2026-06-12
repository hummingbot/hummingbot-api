---
id: READ-022
title: Duplicated cached-price fallback logic in accounts_service
category: readability
impact: medium
effort: S
risk: low
files:
  - services/accounts_service.py:908-915
  - services/accounts_service.py:919-929
commits:
  - "0421b9d (refactor) READ-022: dedupe cached-price fallback in AccountsService"
status: done
created: 2026-06-11
---

## Problema
The cached-price fallback loop is implemented twice with near-identical code: inside `_safe_get_last_traded_prices` at services/accounts_service.py:908-915 and in the standalone `_get_fallback_prices` at services/accounts_service.py:919-929. Both iterate trading pairs, use `self._last_known_prices[pair]` when present (logging 'Using cached price ...') and otherwise set Decimal('0') (logging 'No cached price available ...'). The duplication means any change to fallback behavior must be made in two places and risks divergence.

## Solución propuesta
Replace the inline loop at lines 908-915 with a call to `self._get_fallback_prices(missing_pairs)` (filtering to only the pairs not already resolved), so the fallback logic lives in one place.

## Criterio de aceptación
- [x] The inline fallback loop (lines 908-915) is replaced by a call to `_get_fallback_prices`
- [x] Behavior for cached-present and cached-absent pairs is unchanged
- [x] Only one implementation of the cached-price fallback remains
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. The duplication is genuine: the inline fallback loop at services/accounts_service.py:908-915 (inside _safe_get_last_traded_prices) and the standalone _get_fallback_prices at lines 919-929 contain near-identical logic — both iterate trading pairs, use self._last_known_prices[pair] with log 'Using cached price ...', and otherwise set Decimal('0') with log 'No cached price available ...'. The line numbers cited are exact. The proposed fix is behavior-preserving: the inline loop only processes pairs `not in last_traded` (the missing ones), and _get_fallback_prices b
