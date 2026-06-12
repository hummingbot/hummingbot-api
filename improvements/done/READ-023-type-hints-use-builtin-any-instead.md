---
id: READ-023
title: Type hints use builtin `any` instead of `typing.Any`
category: readability
impact: medium
effort: S
risk: low
files:
  - services/accounts_service.py
  - services/gateway_service.py
  - services/gateway_client.py
  - services/unified_connector_service.py
commits:
  - "e2a7c8e (refactor) READ-023: use typing.Any instead of builtin any in hints"
status: done
created: 2026-06-11
---

## Problema
Several annotations use the builtin function `any` as a type instead of `typing.Any`, e.g. services/accounts_service.py:1170, 1198, 1302, 1530 (`Dict[str, any]`), services/gateway_service.py:98,204,229,274,340 (`Dict[str, any]`), services/gateway_client.py:269 (`value: any`), services/unified_connector_service.py:67-68 (`Dict[str, any]`). `any` is a function, not a type; the hint is semantically wrong, misleads readers, and breaks static type checkers (mypy/pyright flag it). It reads as if a real type were intended.

## Solución propuesta
Replace `any` with `Any` (importing `from typing import Any` where missing) in these annotations. A targeted sed/replace per file plus ensuring the `Any` import exists resolves it.

## Criterio de aceptación
- [x] grep -rn "Dict\[str, any\]\|: any\b\|-> any\b" over services/ returns no matches
- [x] Each touched file imports `Any` from typing
- [x] A type checker no longer reports 'Function ... not valid as a type' for these lines
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. All 12 cited locations match exactly: services/accounts_service.py:1170,1198,1302,1530 use `Dict[str, any]`; services/gateway_service.py:98,204,229,274,340 use `Dict[str, any]`; services/gateway_client.py:269 uses `value: any`; services/unified_connector_service.py:67-68 use `Dict[str, any]`. In all cases `any` is the builtin function, not `typing.Any`, so the annotations are semantically wrong and static type checkers (mypy/pyright) flag them as invalid types. None of the four files import `Any` (accounts_service.py imports `TYPE_CHECKING, Dict, List, Optional,
