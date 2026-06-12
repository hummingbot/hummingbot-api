---
id: SEC-019
title: CORS con allow_origins='*' junto a allow_credentials=True
category: security
impact: medium
effort: S
risk: low
files:
  - main.py:319-325
commits:
  - "c0080a0 (fix) SEC-019: configurable CORS origins instead of '*' with credentials"
status: done
created: 2026-06-11
---

## Problema
En main.py:319-325 el CORSMiddleware se configura con allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*']. La combinacion comodin+credenciales es invalida por spec y, mas alla de eso, refleja cualquier Origin habilitando que paginas web de terceros invoquen la API desde el navegador de un operador autenticado. El comentario 'Modify in production' indica que quedo como placeholder.

## Solución propuesta
Configurar allow_origins desde settings con una lista explicita de origenes confiables (env-driven), y no usar '*' cuando allow_credentials=True. Para una API administrativa que usa Basic Auth, restringir origenes/metodos/headers a lo realmente necesario.

## Criterio de aceptación
- [x] allow_origins se lee de configuracion y por defecto no es '*' cuando se permiten credenciales
- [x] Peticiones cross-origin desde un Origin no listado son rechazadas por el navegador
- [x] La lista de origenes permitidos es configurable por variable de entorno
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Confirmed against the real code. main.py:319-325 configures CORSMiddleware with allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"], and the literal comment "Modify in production to specific origins" confirms it is an unfinished placeholder. The file:line is accurate. The wildcard-origin + allow_credentials=True combination is a genuine misconfiguration: it is invalid per the Fetch/CORS spec (a literal `*` cannot be used with credentials), and Starlette's CORSMiddleware works around this by reflecting the request's Origin back, so any third-party page can make

Métodos/headers quedan en '*' por defecto pero configurables; aceptable con orígenes restringidos. Default: lista vacía + regex localhost-only.
