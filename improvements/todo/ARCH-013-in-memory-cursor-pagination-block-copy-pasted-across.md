---
id: ARCH-013
title: In-memory cursor pagination block is copy-pasted across 5 endpoints in routers/trading.py
category: architecture
impact: medium
effort: S
risk: low
files:
  - routers/trading.py
  - models/pagination.py
commits: []
status: todo
created: 2026-06-11
---

## Problema
The same ~25-line block (attach _cursor_id to each item, sort by _cursor_id, walk the list to find the cursor index, slice by limit, compute has_more/next_cursor, pop _cursor_id, build PaginatedResponse) is duplicated in get_positions (routers/trading.py:170-202), get_active_orders (routers/trading.py:268-300), get_orders (around routers/trading.py:375), trades (routers/trading.py:482) and funding payments (routers/trading.py:673). This is business/presentation logic living in the router, repeated verbatim, so a pagination bug must be fixed in five places and each endpoint can subtly drift.

## Solución propuesta
Extract a single helper, e.g. paginate_by_cursor(items, cursor, limit, cursor_id_fn) -> PaginatedResponse, into a shared module (e.g. models/pagination.py which already exists, or utils). Replace the five inline blocks with a call to it. The cursor-id assignment per item stays at the call site; the sort/slice/next-cursor/pop logic moves into the helper.

## Criterio de aceptación
- [ ] A single reusable cursor-pagination helper exists
- [ ] get_positions/get_active_orders/get_orders/trades/funding in routers/trading.py call the helper instead of inlining the block
- [ ] responses (data ordering, has_more, next_cursor) are byte-identical to current behavior for a multi-page dataset
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified all five locations in /Users/dman/Documents/work/hummingbot-api/routers/trading.py. The ~25-line in-memory cursor-pagination block (sort by _cursor_id, walk list to find cursor index, slice by limit, compute has_more/next_cursor, pop _cursor_id, build PaginatedResponse) is duplicated near-verbatim in: get_positions (170-202), get_active_orders (268-300), get_orders (sort 371, cursor block 374-402), trades (sort 478, cursor block 480-509), funding payments (sort 669, cursor block 671-700). The only per-endpoint variation is (a) how _cursor_id is built and (b) the sort key (positions/ac
