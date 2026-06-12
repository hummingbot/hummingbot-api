---
id: ARCH-014
title: create_executor in executor_service.py is a single ~115-line function doing validation, connector setup, instantiation, persistence and completion handling
category: architecture
impact: medium
effort: M
risk: low
files:
  - services/executor_service.py
commits: []
status: todo
created: 2026-06-11
---

## Problema
create_executor (executor_service.py:286-401) does too much in one body: validates type against EXECUTOR_REGISTRY (:305-317), resolves the trading interface and ensures connector/market (:320-331), defaults the timestamp (:334-335), builds the typed config (:338-345), instantiates the executor (:348-359), mutates two metadata dicts (:364-373), manipulates a ContextVar and starts the task (:376-378), persists to DB (:381), and handles immediate completion (:388-389). The mix of validation, IO (connector init, DB) and orchestration in one function makes it hard to test the validation independently and obscures the happy path.

## Solución propuesta
Decompose into focused private helpers: _validate_executor_config(executor_config) -> (executor_class, config_class, typed_config), _prepare_market(account, connector_name, trading_pair), _instantiate_and_register(typed_config, trading_interface, metadata). create_executor then reads as a short orchestration sequence. No behavior change.

## Criterio de aceptación
- [ ] create_executor body is reduced to a short orchestration calling named helpers
- [ ] config/type validation is isolated in a helper that can be unit-tested without starting an executor or touching the DB
- [ ] creating valid and invalid executors returns the same status codes and payloads as before
- [ ] No se rompe ningún test existente en test/ (se añade test si aplica)

## Notas
Hallazgo confirmado por verificación adversarial. Veredicto: Verified against the actual code at /Users/dman/Documents/work/hummingbot-api/services/executor_service.py:286-401. The finding is accurate. create_executor is a single ~115-line async method that genuinely mixes multiple concerns, and every cited line range checks out: type validation against EXECUTOR_REGISTRY (305-317), trading-interface resolution and connector/market readiness via add_market/ensure_connector (320-331), timestamp defaulting (334-335), typed config construction (338-345), executor instantiation (348-359), mutation of _active_executors and _executor_metadata (364-373), Contex
