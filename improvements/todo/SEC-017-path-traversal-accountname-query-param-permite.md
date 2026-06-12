---
id: SEC-017
title: Path traversal en account_name (query param) permite borrado/creacion de directorios arbitrarios
category: security
impact: high
effort: S
risk: medium
files:
  - routers/accounts.py:50
  - routers/accounts.py:71
  - services/accounts_service.py:978
  - services/accounts_service.py:1038
  - utils/file_system.py:138
commits: []
status: todo
created: 2026-06-11
---

## Problema
En routers/accounts.py:50 (add_account) y :71 (delete_account) el account_name llega como parametro de query/body (la ruta es /add-account y /delete-account, no /{account_name}), por lo que PUEDE contener barras y '..'. delete_account en services/accounts_service.py:1038 hace fs_util.delete_folder('credentials', account_name), y delete_folder (utils/file_system.py:138) construye self._get_full_path(os.path.join(directory, folder_name)) y ejecuta shutil.rmtree SIN validar folder_name contra traversal (a diferencia de create_folder/add_file que si rechazan '/' y '\'). Con account_name='../../algun/dir' un usuario autenticado puede borrar directorios fuera de credentials/. Lo mismo aplica a list_credentials (accounts_service.py:978) que lista credentials/{account_name}/connectors permitiendo enumerar otras rutas.

## Solución propuesta
Validar account_name (y connector_name) en el borde de confianza: aceptar solo un patron seguro (p.ej. regex ^[A-Za-z0-9_-]+$, rechazando '/', '\', '.' inicial y '..') en los routers o en los metodos del servicio antes de cualquier operacion de filesystem. Adicionalmente, endurecer fs_util.delete_folder/list_files para validar folder_name igual que create_folder ya lo hace, de forma defensiva.

## Criterio de aceptación
- [ ] POST /accounts/delete-account?account_name=../foo devuelve 400 sin tocar el filesystem
- [ ] POST /accounts/add-account con account_name conteniendo '/', '\' o '..' es rechazado con 400
- [ ] delete_folder/list_files rechazan nombres con separadores de ruta o componentes '..'
- [ ] Los nombres de cuenta validos (alfanumericos, guion, guion bajo) siguen funcionando
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. The vulnerability is real and exploitable by an authenticated user.

CONFIRMED:
- routers/accounts.py:50 (add_account) and :71 (delete_account) take account_name as a QUERY param (routes are /add-account and /delete-account, NOT /{account_name}), so the raw value can contain '/' and '..'. No validation occurs in the routers (only a 'master_account' literal check).
- delete_account in services/accounts_service.py:1024 does no validation and calls fs_util.delete_folder('credentials', account_name) at line 1038.
- utils/file_system.py:138 delete_folder builds self.
