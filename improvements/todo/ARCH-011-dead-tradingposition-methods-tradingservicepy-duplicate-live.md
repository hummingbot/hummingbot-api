---
id: ARCH-011
title: Dead trading/position methods in trading_service.py duplicate the live ones in accounts_service.py
category: architecture
impact: medium
effort: S
risk: low
files:
  - services/trading_service.py:453
  - services/trading_service.py:502
  - services/trading_service.py:524
  - services/trading_service.py:544
  - services/trading_service.py:577
  - services/accounts_service.py:1367
  - services/accounts_service.py:1544
  - services/accounts_service.py:1573
  - services/accounts_service.py:1770
  - routers/trading.py:56
  - routers/trading.py:108
  - routers/trading.py:159
  - routers/trading.py:599
commits: []
status: todo
created: 2026-06-11
---

## Problema
TradingService exposes place_order (trading_service.py:453), cancel_order (trading_service.py:502), get_active_orders (trading_service.py:524), get_positions (trading_service.py:544) and set_leverage (trading_service.py:577), but grep over routers/, services/, main.py shows ZERO callers for any of them. Meanwhile the equivalent live operations are implemented separately in AccountsService: place_trade (accounts_service.py:1367), cancel_order (accounts_service.py:1544), get_account_positions (accounts_service.py:1770) and set_leverage (accounts_service.py:1573), which ARE the ones wired to the API (routers/trading.py:56,159,538,599). The result is two parallel, partially-overlapping trading APIs where the validation-rich one lives in AccountsService and the thin dead one in TradingService, creating confusion about which is canonical.

## Solución propuesta
Remove the unused place_order/cancel_order/get_active_orders/get_positions/set_leverage methods from TradingService (they have no callers), keeping TradingService focused on its real responsibility: owning trading interfaces for executors. If a service-layer trading API is desired long-term, consolidate the AccountsService.place_trade validation logic there instead of leaving two copies.

## Criterio de aceptación
- [ ] TradingService no longer defines the 5 unused trading/position methods
- [ ] grep confirms no caller breaks
- [ ] routers/trading.py still places/cancels orders and reads positions via accounts_service unchanged
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against real code. All cited line numbers in trading_service.py are exact: place_order (453), cancel_order (502), get_active_orders (524), get_positions (544), set_leverage (577). Grep across routers/, services/, main.py confirms ZERO callers for these five TradingService methods: no router uses deps.get_trading_service at all, and the only consumers of TradingService (executor_service.py, internal update loops) call only get_trading_interface/get_all_trading_interfaces/update_all_timestamps. Meanwhile routers/trading.py wires the live operations to AccountsService: place_trade (accou
