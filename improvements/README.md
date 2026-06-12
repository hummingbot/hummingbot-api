# improvements — backlog atómico de mejoras de código

Backlog accionable de mejoras de código generado por el skill `/improvements` (read-only).
Cada archivo `.md` es **una mejora atómica**: un problema, una solución, un criterio de aceptación.

## Convención

- `todo/` — mejoras pendientes. `done/` — implementadas (con commits anotados).
- Nombre: `{CATEGORÍA}-{NNN}-{slug}.md`.
  - Categorías: `PERF` (performance), `CORR` (correctness), `ARCH` (arquitectura), `SEC` (seguridad), `READ` (legibilidad).
  - `NNN`: contador de 3 dígitos **único dentro del scope** (cuenta `todo/` + `done/`), nunca se reutiliza.
- El frontmatter es la ficha; el cuerpo es la especificación. No edites el `status` a mano: lo mueve `/ship-improvement`.

## Flujo

1. `/improvements <scope>` — audita y deja items en `todo/` (no toca código).
2. `/ship-improvement <id>` — implementa un item, commitea y lo mueve a `done/` anotando los commits.

> Primer scan: `services/` — 2026-06-11.
