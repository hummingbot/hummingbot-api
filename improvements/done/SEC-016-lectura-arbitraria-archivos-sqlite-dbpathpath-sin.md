---
id: SEC-016
title: Lectura arbitraria de archivos SQLite via db_path:path sin validar en archived-bots
category: security
impact: high
effort: M
risk: low
files:
  - routers/archived_bots.py
  - utils/hummingbot_database_reader.py
  - utils/file_system.py
commits:
  - "1b6d76e (fix) SEC-016: validate db_path containment in archived-bots endpoints"
status: done
created: 2026-06-11
---

## Problema
Los endpoints GET /archived-bots/{db_path:path}/status, /summary, /performance, /trades, /orders, /executors, /positions, /controllers reciben db_path directamente de la URL (converter :path, que captura barras y rutas absolutas) y lo pasan sin validacion a HummingbotDatabase(db_path) en routers/archived_bots.py:83, 105, 140, 198, 239, 276, 306, 339. En utils/hummingbot_database_reader.py:18 eso se convierte en create_engine(f'sqlite:///{db_path}'). A diferencia de delete_archived_bot (que via fs_util.delete_archived_bot valida que la ruta este bajo 'archived/'), estos endpoints de lectura NO validan nada. Un usuario autenticado puede apuntar a cualquier archivo SQLite del host (p.ej. /archived-bots//absolute/path/to/any.sqlite/status o usando ../) y leer su contenido (ordenes, trades, etc.), filtrando datos fuera del directorio de bots archivados.

## Solución propuesta
Aplicar la misma validacion de contencion que ya existe para delete: resolver db_path a una ruta absoluta canonica (os.path.realpath) y verificar con os.path.commonpath que cae dentro de fs_util._get_full_path('archived') antes de instanciar HummingbotDatabase. Mejor aun, no aceptar rutas del cliente: que el cliente envie solo el id/nombre del bot archivado y construir la ruta en el servidor desde la lista blanca devuelta por fs_util.list_databases(). Rechazar con 400/404 cualquier ruta que no este en la lista de databases conocidas.

## Criterio de aceptación
- [x] GET /archived-bots/{db_path}/status con una ruta absoluta o con ../ que apunte fuera de bots/archived devuelve 400/404 y no abre el archivo
- [x] Las rutas validas devueltas por GET /archived-bots/ siguen funcionando en todos los sub-endpoints (status, summary, trades, orders, executors, positions, controllers, performance)
- [x] Existe verificacion con os.path.realpath + os.path.commonpath (o lista blanca) compartida por lectura y borrado
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Confirmed by reading the real code. All 8 read endpoints in routers/archived_bots.py (/status L83, /summary L105, /performance L140, /trades L198, /orders L239, /executors L276, /positions L306, /controllers L339) receive db_path via the FastAPI {db_path:path} converter (captures slashes and absolute paths) and pass it unvalidated to HummingbotDatabase(db_path). In utils/hummingbot_database_reader.py L18, that becomes create_engine(f'sqlite:///{os.path.join(db_path)}') with no containment check. The contrast with delete is real: fs_util.delete_archived_bot (utils/file_system.py L418-451) valid

Hardening adicional en el sink: HummingbotDatabase.__init__ rechaza archivos inexistentes.
