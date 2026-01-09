import sys
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure shared app mocks are applied before importing application modules
import test.mocks.app_mocks  # noqa: F401


def build_app_with_router(router, prefix: str | None = None) -> FastAPI:
    app = FastAPI()
    # register sensible-like error helpers if present in project
    try:
        import fastapi_sensible  # pragma: no cover
        # placeholder: if project uses fastify sensible equivalent, adapt here
    except Exception:
        pass

    if router is not None:
        if prefix:
            app.include_router(router, prefix=prefix)
        else:
            app.include_router(router)

    return app


@pytest.fixture
def make_test_client() -> Callable:
    """Return a small helper to build a TestClient for a router.

    Usage in tests:
      client = make_test_client(trading_clmm_routes, prefix='/trading/clmm')
    """

    def _make(router, prefix: str | None = None):
        app = build_app_with_router(router, prefix)
        return TestClient(app)

    return _make


def override_dependencies(app, overrides: dict):
    for dep, value in overrides.items():
        app.dependency_overrides[dep] = value
