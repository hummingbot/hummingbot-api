---
id: PERF-005
title: OrdersRecorder emits unconditional INFO logging and per-listener debug introspection on the order-event hot path
category: performance
impact: low
effort: S
risk: low
files:
  - services/orders_recorder.py:110
  - services/orders_recorder.py:114
  - services/orders_recorder.py:150
  - services/orders_recorder.py:158
  - services/orders_recorder.py:171
  - services/orders_recorder.py:190
  - services/orders_recorder.py:64-69
commits: []
status: todo
created: 2026-06-11
---

## Problema
_did_create_order (orders_recorder.py:110,114) logs at INFO on every BuyOrderCreated/SellOrderCreated event, and _handle_order_created (orders_recorder.py:150,158,171,190) emits several more INFO lines per order. start() (orders_recorder.py:64-84) additionally iterates connector._event_listeners and logs per-listener details. On high-frequency market-making strategies the create-order event fires constantly, so this synchronous INFO logging adds overhead and log volume to the trade recording path.

## Solución propuesta
Demote the per-event create/handle logs (orders_recorder.py:110,114,150,158,171,190) to logger.debug, and remove or guard the per-listener introspection block in start() behind logger.isEnabledFor(logging.DEBUG). Keep error-level logs intact.

## Criterio de aceptación
- [ ] Order create/fill recording no longer emits INFO logs per event
- [ ] Listener-introspection logging in start() runs only when DEBUG is enabled
- [ ] Error and warning logging is unchanged
- [ ] No change to actual order/trade persistence behavior
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. All cited line numbers match exactly. In _did_create_order, line 110 logs INFO on every BuyOrderCreated/SellOrderCreated event ("_did_create_order called for order ...") and line 114 logs INFO again ("Creating task to handle order created"). In _handle_order_created, line 150 logs INFO unconditionally on every create ("_handle_order_created started"), line 190 logs INFO on every successful record ("Successfully recorded order created"), with lines 158 and 171 logging INFO on conditional branches. These are plainly leftover debug-diagnostic messages on the order-
