## Testing Guidelines for hummingbot-api

This document captures the test patterns used in the Gateway repository tests (gateway-src/test) and provides a concise, actionable guideline for how Python tests in this repo should be organized and written. Follow these rules to keep tests consistent, fast, and easy to maintain.

### Purpose
- Make route-level, unit, connector, and lifecycle tests predictable and uniform.
- Provide shared mock utilities and fixtures so individual tests stay focused and fast.
- Gate long-running / on-chain tests so they run only when explicitly requested.

### Test directory structure
- `test/routes/` — route-level tests that exercise FastAPI endpoints using `TestClient`.
- `test/services/` — unit tests for service-layer logic.
- `test/connectors/` — connector-specific unit tests and route tests.
- `test/mocks/` — shared mock implementations and fixtures (logger, config, chain configs, file fixtures).
- `test/helpers/` — small factories for mock responses and reusable builders.
- `test/lifecycle/` — manual or integration tests that run against live networks. These are skipped by default and must be explicitly enabled.

### Test types and rules (high level)
- Route registration tests: verify that routes exist. Send minimal payloads and assert status is 400 (schema error) or 500 — not 404. This proves the route was registered.
- Schema validation tests: send malformed or missing fields to confirm the API returns 400 for invalid input.
- Connector acceptance tests: send valid-like payloads and assert `status != 404`. These tests verify the router accepts the connector parameter and performs further validation.
- Unit tests: mock external dependencies and test business logic in isolation.
- Lifecycle / manual integration tests: run real on-chain flows (open → add → remove → close). These must be gated by an env var (see below) and documented clearly at the top of the test file.

### Shared mocks and setup
- Provide a single shared-mocks module (`test/mocks/shared_mocks.py`) that
  - stubs the logger and logger.update routines,
  - provides a ConfigManager mock with `get`/`set` behavior and a shared storage object,
  - stubs chain config getters (e.g., `getSolanaChainConfig`, `getEthereumChainConfig`),
  - stubs token list file reads and other filesystem reads used by connectors.
- For tests that exercise the application, import `test/mocks/app_mocks.py` at module-level so mocks are applied before app modules are imported.

### Fixtures and app builder (Python parallels to JS pattern)
- Provide `test/conftest.py` with these fixtures:
  - `app`: builds a minimal FastAPI app and registers only the router under test (same pattern as `buildApp()` in JS). This avoids starting the whole app lifespan.
  - `client`: a `TestClient(app)` used by individual tests.
  - `shared_mocks`: optional fixture to access mock storage or reset state between tests.
- Use `app.dependency_overrides` to inject test doubles for services like `get_accounts_service` and `get_database_manager`.

### Assertions and model validation
- When asserting successful responses, parse the JSON into the Pydantic response model (e.g. `CLMMOpenAndAddResponse`) and assert typed fields. This enforces contract parity with OpenAPI docs.
- When verifying route registration, assert that an empty or invalid payload returns 400 or 500 but not 404.

### Lifecycle/integration tests
- Place long running or network-affecting tests under `test/lifecycle/`.
- Gate execution using an environment variable (for example `MANUAL_TEST=true`) or a pytest marker `@pytest.mark.manual` so CI won't run them by default.
- Document prerequisites at the top of the test file (wallet, passphrase, balances, env vars, timeouts).

### Naming conventions
- Use `.routes.test.py` for route-level tests and `.test.py` for unit/service tests.
- Keep test file names and directory structure parallel to `gateway-src/test` to make reviews easier for cross-repo maintenance.

### Timeouts and long-running steps
- Use explicit `pytest.mark.timeout` or `timeout` arguments for long-running tests. Default unit tests should be fast (< 1s — 200ms ideally).

### CI and markers
- Mark integration/manual tests with a `manual` or `integration` marker. Exclude these from CI by default.

### How to run tests locally (recommended)
1. Create a virtual environment and install test deps (example):

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt  # ensure pytest, httpx, fastapi, pydantic are present
pytest -q
```

2. To run only route tests:

```bash
pytest test/routes -q
```

3. To run a manual lifecycle test (example):

```bash
MANUAL_TEST=true GATEWAY_TEST_MODE=dev pytest test/lifecycle/pancakeswap-sol-position-lifecycle.test.py -q
```

### Running tests in Docker (recommended CI/dev pattern)

We provide a dedicated `test` build stage in the repository Dockerfile so CI and developers can run tests inside a container without shipping test files in the final runtime image.

1) Build the test image (this builds the conda env and includes test tooling and `test/` files):

```bash
docker build --target test -t hummingbot-api:test .
```

2) Run tests inside the test image:

```bash
docker run --rm hummingbot-api:test /opt/conda/envs/hummingbot-api/bin/pytest -q
```

Alternative (fast local iteration): mount the working tree into a dev container and run pytest without rebuilding the image:

```bash
docker run --rm -v "$(pwd)":/work -w /work continuumio/miniconda3 bash -lc \
  "/opt/conda/bin/pip install -r requirements-dev.txt && /opt/conda/envs/hummingbot-api/bin/pytest -q"
```

Notes:
- The final runtime Docker image is intentionally minimal and does not include the `test/` directory or pytest. Use the `--target test` build above for CI or development test runs.
- If your CI runner cannot access the repo tests due to .dockerignore, ensure the build context sent to docker includes the `test/` directory (default when building from the repo).

-### Checklist for writing a new test

**SOLID Methodology Requirement:**
All new code (including tests and production code) should follow SOLID principles:
- Single Responsibility, Open/Closed, Liskov Substitution, Interface Segregation, and Dependency Inversion.
This ensures the codebase remains clean, stable, and easily extendable. Review all new code for adherence to these principles before merging.

- Decide test type (route/unit/connector/lifecycle).
- If route test: register only the router under test via app fixture.
- Use shared mocks (import `test/mocks/app_mocks.py`) for external services.
- Use dependency overrides to inject test doubles where appropriate.
- Validate responses using Pydantic models where available.
- For long-running or network tests: gate with env var and document preconditions.

### Example minimal route test template (Python)

See `test/conftest.py` for fixtures. Minimal pattern:

1. Import `client` fixture.
2. Use `client.post('/gateway/clmm/open', json={})` with empty payload and assert status in [400, 500] to assert route present.

### Next steps for enforcement and improvements
- Create `test/conftest.py` and the `test/mocks` modules to implement the shared mocks and fixtures described here.
- Add a `pytest.ini` registering `manual` and `integration` markers so they can be filtered in CI.
- Optionally add a small pre-commit or CI check that ensures route tests assert not-404 for empty payloads (lint-like test hygiene check).

---
This document is the canonical summary of the Gateway JS test patterns adapted for the Python API tests. If you want, I can now implement the `conftest.py` and `test/mocks/*` scaffolding and convert one existing test to use the new fixtures.
