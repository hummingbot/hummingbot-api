---
id: SEC-018
title: Credenciales por defecto admin/admin y password de cifrado 'a' embebidos como defaults
category: security
impact: high
effort: S
risk: low
files:
  - config.py
  - main.py
commits: []
status: todo
created: 2026-06-11
---

## Problema
En config.py:64-67 SecuritySettings define defaults username='admin', password='admin' y config_password='a' (password con el que se cifran TODAS las credenciales de conectores via ETHKeyFileSecretManger en main.py:104,123). Si el operador no setea las variables de entorno, la API queda con auth Basic trivialmente adivinable y las credenciales de exchange quedan cifradas con un secreto de un caracter. Como CORS esta abierto (main.py:321) y no hay forzado de cambio de password, un despliegue por defecto es directamente explotable.

## Solución propuesta
No proveer defaults usables para secretos: hacer que password y config_password sean obligatorios (sin default) y fallar el arranque si no estan seteados, o generar/derivar uno aleatorio y loguear una advertencia clara. Como minimo, en el lifespan (main.py) emitir un error/warning prominente y opcionalmente abortar si username/password/config_password siguen siendo los valores por defecto.

## Criterio de aceptación
- [ ] Arrancar la app sin setear las variables de seguridad falla con un mensaje claro, o registra una advertencia de severidad alta
- [ ] config_password ya no tiene 'a' como valor utilizable por defecto
- [ ] Existe documentacion/validacion que impide correr en produccion con admin/admin
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verificado contra el código real y confirmado como hallazgo válido y relevante.

Evidencia exacta:
- /Users/dman/Documents/work/hummingbot-api/config.py:64-67 — SecuritySettings define defaults usables para secretos: username="admin" (l.64), password="admin" (l.65), debug_mode=False (l.66) y config_password="a" (l.67). El env_prefix de SecuritySettings es "" (l.70), por lo que las variables son USERNAME/PASSWORD/CONFIG_PASSWORD.
- /Users/dman/Documents/work/hummingbot-api/main.py:104 y main.py:123 — config_password se usa para construir ETHKeyFileSecretManger, el manager que cifra/descifra TOD
