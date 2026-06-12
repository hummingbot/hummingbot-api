---
id: ARCH-015
title: Token-balance dict shape and gateway-availability guard are duplicated inline instead of shared
category: architecture
impact: low
effort: S
risk: low
files:
  - services/accounts_service.py
commits: []
status: todo
created: 2026-06-11
---

## Problema
Two small leaked patterns repeat in accounts_service.py. (1) The balance dict literal {token, units, price, value, available_units} is hand-built in both _get_connector_tokens_info (accounts_service.py:862-868) and get_gateway_balances (accounts_service.py:2163-2169) with the same float()/value=price*units convention, so the wire shape of a balance entry is defined in two places. (2) The guard `if not await self.gateway_client.ping(): raise HTTPException(503, 'Gateway service is not available')` is copy-pasted at accounts_service.py:2020, 2050, 2079, 2110 (and a logging variant at :1900), leaking the gateway-availability concern into every public method.

## Solución propuesta
Introduce a small helper to build a balance entry (e.g. _balance_entry(token, units, price)) and call it from both sites, and a _require_gateway() helper (or a decorator) that performs the ping-and-raise once. Replace the four duplicated guards and the two inline dict literals with the helpers.

## Criterio de aceptación
- [ ] A single helper produces the balance entry dict, used by both _get_connector_tokens_info and get_gateway_balances
- [ ] the four 503 gateway guards call one shared helper/decorator
- [ ] balance JSON responses and the 503 behavior are unchanged
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code in services/accounts_service.py; all cited line numbers match exactly. (1) The balance dict {token, units, price, value, available_units} with float()/value=price*units is genuinely hand-built at lines 862-868 and 2163-2169. (2) The guard `if not await self.gateway_client.ping(): raise HTTPException(503, "Gateway service is not available")` is verbatim-duplicated at lines 2020-2021, 2050-2051, 2079-2080, 2110-2111, with the logging-return variant at 1900-1901 (grep confirms exactly these 5 occurrences). Both are real leaked patterns, not by-design, and the line r
