---
id: READ-024
title: Debug output via print() instead of logging in BotArchiver
category: readability
impact: low
effort: S
risk: low
files:
  - utils/bot_archiver.py
commits:
  - "f19451c (refactor) READ-024: use logging instead of print in BotArchiver"
status: done
created: 2026-06-11
---

## Problema
utils/bot_archiver.py uses bare `print(...)` for operational output at lines 31, 35, 40 ('Archive ... uploaded', 'Credentials not available for AWS S3.', 'Compressed ...'). The rest of the codebase uses module-level `logging`. These prints bypass log levels/handlers, are invisible in structured logs, and the NoCredentialsError branch (line 34-35) silently swallows a real failure with only a print, giving no error-level signal.

## Solución propuesta
Add a module logger (`logger = logging.getLogger(__name__)`) and replace the three `print` calls with `logger.info` (success/compress) and `logger.error` (credentials-not-available) so failures surface at error level.

## Criterio de aceptación
- [x] utils/bot_archiver.py has no `print(` calls
- [x] Success messages use logger.info and the credentials failure uses logger.error
- [x] A module-level logger is defined
- [x] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the real code. utils/bot_archiver.py uses bare print() at the exact lines cited: line 31 ("Archive {archive_name} uploaded successfully to S3."), line 35 ("Credentials not available for AWS S3."), and line 40 ("Compressed {source_dir} into {output_path}"). The line numbers and quoted strings match precisely. A grep across utils/, services/, and routers/ confirms this is the ONLY file using print() — every other module uses logging.getLogger(__name__), so the finding accurately reflects a real inconsistency, not a false convention claim. The silent-swallow concern is also valid
