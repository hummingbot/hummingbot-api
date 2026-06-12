---
id: PERF-004
title: Multi-account portfolio history runs N sequential DB queries then re-sorts and mis-paginates with a single cursor
category: performance
impact: medium
effort: M
risk: medium
files:
  - routers/portfolio.py
  - database/repositories/account_repository.py
commits:
  - "5c6f278 (perf) PERF-004: single-query multi-account portfolio history"
status: done
created: 2026-06-11
---

## Problema
In get_portfolio_history (portfolio.py:106-124), when account_names are provided it loops and awaits get_account_state_history once per account in series (serial awaits that could run concurrently), each fetching up to `limit` rows, then concatenates, re-sorts in Python and slices to `limit`. It also passes the same `cursor` to every account query, so pagination is incorrect across accounts and over-fetches (N*limit rows materialized to return `limit`). get_account_state_history already supports filtering but is invoked per-account.

## Solución propuesta
Fetch the per-account histories concurrently with asyncio.gather instead of a serial loop, OR (preferred) extend the repository query to accept a list of account_names with an IN filter so a single query returns the merged, correctly ordered, limited result. At minimum, run the existing per-account calls under asyncio.gather to remove the serial latency.

## Criterio de aceptación
- [x] Multi-account history no longer awaits each account query strictly in series
- [x] Returned data is ordered by timestamp desc and limited correctly across all requested accounts
- [x] Pagination cursor produces non-overlapping pages across accounts
- [x] Endpoint response shape is unchanged for existing single/all-account callers
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Confirmed against the real code. In routers/portfolio.py the multi-account branch (the `else` at lines 104-124, matching the cited 106-124) loops over `filter_request.account_names` and `await`s `accounts_service.get_account_state_history(...)` once per account in series (lines 107-116), each opening its own DB session and fetching up to `fetch_limit` rows, then concatenates, re-sorts in Python by timestamp string (line 119) and slices to `limit` (line 122). All three sub-claims hold:

1) Serial latency: the awaits are sequential and independent; they could run via asyncio.gather. Verified eac

Desvío: se editó también services/accounts_service.py (param pass-through account_names en load_account_state_history), necesario para cablear router→repo.
