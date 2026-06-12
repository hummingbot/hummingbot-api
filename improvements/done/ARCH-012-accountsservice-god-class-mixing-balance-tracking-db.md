---
id: ARCH-012
title: AccountsService is a god-class mixing balance tracking, DB persistence, gateway wallets, trading, perpetuals and portfolio analytics
category: architecture
impact: high
effort: L
risk: medium
files:
  - services/accounts_service.py
  - services/trading_service.py
  - services/executor_service.py
  - routers/trading.py
  - routers/portfolio.py
  - routers/connectors.py
commits:
  - "f4764bb (refactor) ARCH-012: split AccountsService god-class along its seams"
status: done
created: 2026-06-11
---

## Problema
accounts_service.py is 2279 lines and AccountsService (starting accounts_service.py:434) owns at least six unrelated responsibilities: (1) connector balance polling loops (update_account_state_loop accounts_service.py:614, _get_connector_tokens_info :819); (2) DB persistence and history for accounts/orders/trades/funding (dump_account_state :674, get_orders :1678, get_trades :1745, get_funding_payments :1819); (3) order/leverage/position trading (place_trade :1367, set_leverage :1573, set_position_mode :1605, get_account_positions :1770); (4) Gateway wallet CRUD and pricing (get_gateway_wallets :2013, add_gateway_wallet :2038, get_gateway_balances :2097, _fetch_gateway_prices_immediate :2179); (5) pure portfolio analytics (get_portfolio_distribution :1198, get_account_distribution :1302); (6) an embedded trading-interface class (the dead one above). Routers reach into it for everything (routers/trading.py, routers/portfolio.py, routers/connectors.py), so the class is a high-coupling hub. Business logic (portfolio percentage math) is interleaved with IO (DB sessions, gateway HTTP, connector calls), making any single concern hard to test or change in isolation.

## Solución propuesta
Split AccountsService along its seams into collaborating services that it composes: a GatewayWalletService (wallet CRUD + gateway balance/pricing, ~accounts_service.py:1887-2272), a PortfolioAnalyticsService (pure functions get_portfolio_distribution/get_account_distribution, accounts_service.py:1198-1365, no IO), and a PerpetualTradingService (leverage/position-mode/positions, accounts_service.py:1512-1817). Start with the pure-analytics extraction since it has no IO and zero risk, then move gateway wallet logic. Keep AccountsService as the balance-polling + state coordinator.

## Criterio de aceptación
- [x] Portfolio distribution math lives in a dedicated module with no DB/gateway/connector imports and has unit tests
- [x] Gateway wallet CRUD/pricing lives in its own service consumed by AccountsService
- [x] accounts_service.py is materially smaller and AccountsService no longer imports gateway HTTP clients directly for analytics
- [x] existing /portfolio and /trading endpoints return identical responses
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code in services/accounts_service.py. The file is 2278 lines (finding said 2279, off by one - trivial). AccountsService starts at line 434 and genuinely mixes six unrelated responsibilities, all confirmed at the exact cited line numbers:

1. Balance polling: update_account_state_loop (614), _get_connector_tokens_info (819).
2. DB persistence/history: dump_account_state (674), get_orders (1678), get_trades (1745), get_funding_payments (1819).
3. Trading/leverage/positions: place_trade (1367), set_leverage (1573), set_position_mode (1605), get_account_positions (1770).

Desvíos: _balance_entry pasó a función module-level balance_entry() en gateway_wallet_service.py (evita import circular; compartida por paths CEX y gateway). _get_perpetual_connector, _require_gateway y _fetch_gateway_prices_immediate se movieron sin delegadores en AccountsService (privados, sin callers externos, verificado por grep). Los routers no requirieron cambios gracias a delegadores con firmas idénticas.
