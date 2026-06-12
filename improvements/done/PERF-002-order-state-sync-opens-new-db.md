---
id: PERF-002
title: Order state sync opens a new DB session and runs a redundant SELECT per in-flight order every minute
category: performance
impact: high
effort: M
risk: medium
files:
  - services/unified_connector_service.py
  - database/repositories/order_repository.py
commits:
  - "ead0511 (perf) PERF-002: one DB session per connector in order state sync"
status: done
created: 2026-06-11
---

## Problema
_sync_orders_to_database (unified_connector_service.py:895-912) loops over every in_flight_order and, inside the loop, opens a fresh `async with self.db_manager.get_session_context()` per order (line 899), then calls get_order_by_client_id followed by update_order_status. update_order_status (order_repository.py:32-35) issues a second SELECT for the same row that was just fetched. This is 2 SELECTs + 1 new session/transaction per order, for every connector, every 60s (order_status_polling_loop). With many open orders across connectors this is a large amount of redundant IO.

## Solución propuesta
Open one session per connector outside the per-order loop (move the session_context up into _sync_orders_to_database, reusing it for all orders of that connector). Mutate the already-fetched ORM object's status directly (set db_order.status = new_status and flush) instead of calling update_order_status, eliminating the second SELECT. Commit once per connector.

## Criterio de aceptación
- [x] _sync_orders_to_database creates at most one DB session per connector call rather than one per order
- [x] No second SELECT is issued for an order already fetched via get_order_by_client_id
- [x] Order status changes are still persisted and terminal orders still removed from in_flight_orders
- [x] Behavior verified with a connector holding multiple in-flight orders
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Confirmed against the real code. In services/unified_connector_service.py:_sync_orders_to_database (lines 879-915), the `async with self.db_manager.get_session_context()` (line 899) sits INSIDE the per-order `for client_order_id, order in list(connector.in_flight_orders.items())` loop (line 895), so a fresh session/transaction is opened per in-flight order. Within each iteration it calls order_repo.get_order_by_client_id (line 901) which runs one SELECT, and then order_repo.update_order_status (line 906) which in order_repository.py:29-41 issues a SECOND, redundant SELECT for the same row befo

Incluye refactor de OrderRepository.update_order_status para reutilizar get_order_by_client_id (archivo listado en el spec).
