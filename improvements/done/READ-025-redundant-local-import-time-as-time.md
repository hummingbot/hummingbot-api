---
id: READ-025
title: Redundant local `import time as _time` shadows module-level time import
category: readability
impact: low
effort: S
risk: low
files:
  - services/market_data_service.py
commits:
  - "d7b5d10 (refactor) READ-025: drop redundant local 'import time as _time'"
status: done
created: 2026-06-11
---

## Problema
services/market_data_service.py:9 already imports `time` at module scope, but the validation method re-imports it locally as `import time as _time` (line 391) and then uses `_time.time()` (line 399). The local alias is unnecessary, inconsistent with the module-level import, and makes the reader wonder why a special alias is needed.

## Solución propuesta
Remove the local `import time as _time` at line 391 and use the already-imported module-level `time` (i.e. `time.time()`).

## Criterio de aceptación
- [x] Line 391 local import is removed
- [x] The method uses the module-level `time`
- [x] grep for `_time` in the file returns no matches
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. Line 9 imports `time` at module scope, and it is used consistently as `time.time()` everywhere in the file (lines 192, 270, 283, 314, 427, 646, 701). The method `validate_trading_pair` at line 391 redundantly does `import time as _time` and uses `_time.time()` at line 399. There is no shadowing or reason for the alias; `time` is never rebound. The finding is accurate, the file:line references match, and the proposed fix (remove line 391, use `time.time()` at line 399) is safe and correct. It is a minor readability/consistency cleanup but legitimately real and ri
