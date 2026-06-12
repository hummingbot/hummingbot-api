---
id: SEC-020
title: debug_mode deshabilita completamente la autenticacion en toda la API y WebSockets
category: security
impact: medium
effort: S
risk: low
files:
  - main.py:370
  - routers/websocket.py:29
  - config.py:66
commits:
  - "833d888 (fix) SEC-020: gate debug_mode auth bypass to non-production environments"
status: done
created: 2026-06-11
---

## Problema
En main.py:370 auth_user concede acceso si debug_mode es True sin importar credenciales, y en routers/websocket.py:29 _authenticate_websocket retorna True inmediatamente con debug_mode. debug_mode es una env var (config.py:66, env_prefix vacio => variable DEBUG_MODE) que al activarse deja TODA la API (incluyendo trading real, manejo de wallets y borrado de cuentas) sin autenticacion. Es un interruptor peligroso de un solo paso, facil de dejar activado por error en un entorno expuesto.

## Solución propuesta
Acotar debug_mode: ligarlo a entorno no-produccion (p.ej. solo permitido si logfire_environment=='dev' o un flag ALLOW_INSECURE explicito), loguear una advertencia ruidosa y persistente en el arranque cuando esta activo, y considerar que solo afecte a binds en localhost. Documentar claramente que nunca debe usarse en despliegues accesibles por red.

## Criterio de aceptación
- [x] Con debug_mode activo, el arranque registra una advertencia de seguridad clara
- [x] debug_mode no puede activarse silenciosamente en el entorno de produccion configurado
- [x] Los endpoints sensibles siguen exigiendo auth salvo en el modo de desarrollo explicitamente reconocido
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against real code. main.py:370 — auth_user bypasses the 401 via `and not debug_mode`, returning the username regardless of credentials. routers/websocket.py:29 — _authenticate_websocket returns True immediately when settings.security.debug_mode. config.py:66 — debug_mode is in SecuritySettings with env_prefix="" (so env var DEBUG_MODE), default False. All file:line refs are accurate. grep confirms only these references and main.py:89 caches the value. Every router (docker, gateway, accounts, connectors, portfolio, trading, gateway_swap/clmm, bot_orchestration) is wired with Depends(au

La idea opcional de limitar a binds localhost no se implementó (scope mínimo); el gating por entorno + warning CRITICAL cubren los criterios.
