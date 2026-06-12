---
id: ARCH-010
title: Dead duplicated AccountTradingInterface in accounts_service.py shadows the live one in trading_service.py
category: architecture
impact: high
effort: M
risk: low
files:
  - services/accounts_service.py
  - services/trading_service.py
commits:
  - "ba05ab7 (refactor) ARCH-010: remove dead AccountTradingInterface from accounts_service"
status: done
created: 2026-06-11
---

## Problema
There are two near-identical AccountTradingInterface classes: accounts_service.py:23-432 and trading_service.py:23-392. They duplicate buy/sell/cancel/get_active_orders/get_connector/is_connector_loaded/get_all_trading_pairs/cleanup/_register_trading_pair_with_connector almost verbatim. Only the trading_service version is live: executor_service.py:37 imports it and create_executor (executor_service.py:320,350) builds executors with trading_service.get_trading_interface. The accounts_service version is dead: accounts_service.get_trading_interface (accounts_service.py:497-515) has ZERO callers (confirmed by grep over routers/, services/, main.py). accounts_service still instantiates the dict (accounts_service.py:495), builds interfaces nowhere, and iterates _trading_interfaces only in stop() (accounts_service.py:589-591), which is always empty. The two copies have already diverged (accounts version has a stale _wait_for_order_book_ready helper and a different default order_book_timeout of 10.0 vs 30.0), so any future fix to trading logic must be made twice or silently rots.

## Solución propuesta
Delete the entire AccountTradingInterface class (accounts_service.py:23-432), the get_trading_interface factory (accounts_service.py:497-515), the self._trading_interfaces field (accounts_service.py:495) and its cleanup loop in stop() (accounts_service.py:589-592). Keep trading_service.AccountTradingInterface as the single source of truth. Verify nothing else references accounts_service._trading_interfaces after removal.

## Criterio de aceptación
- [x] accounts_service.py no longer defines AccountTradingInterface or get_trading_interface
- [x] grep -rn 'AccountTradingInterface' services/ shows it only in trading_service.py and its importers
- [x] app starts and executors are still created successfully via trading_service.get_trading_interface
- [x] no reference to accounts_service._trading_interfaces remains
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. There are two AccountTradingInterface classes: accounts_service.py:23-432 and trading_service.py:23-392. Only the trading_service version is live: executor_service.py:37 imports `AccountTradingInterface` from services.trading_service, and executor_service.py:283 builds interfaces via self._trading_service.get_trading_interface (used at line 320). The accounts_service version is dead - grep over routers/, services/, main.py confirms accounts_service.get_trading_interface (line 497) has ZERO callers; the only references to accounts_service._trading_interfaces are 

trading_service.py no requirió cambios (ya era la única fuente viva).
