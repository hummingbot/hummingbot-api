"""
Tests for the CORS configuration (SEC-019).

Run with: pytest test/test_cors_settings.py -v
"""
import pytest

from config import CORSSettings


def _build_client(cors: CORSSettings):
    """Build a minimal app with CORSMiddleware wired exactly like main.py does."""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors.allow_origins,
        allow_origin_regex=cors.allow_origin_regex or None,
        allow_credentials=cors.allow_credentials,
        allow_methods=cors.allow_methods,
        allow_headers=cors.allow_headers,
    )

    @app.get("/")
    async def root():
        return {"status": "running"}

    return TestClient(app)


class TestCORSSettings:
    """Tests for CORSSettings defaults and env-driven configuration."""

    def test_default_origins_are_not_wildcard_with_credentials(self):
        cors = CORSSettings()
        assert cors.allow_credentials is True
        assert "*" not in cors.allow_origins
        assert cors.allow_origin_regex != ".*"

    def test_origins_configurable_via_environment(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["https://dashboard.example.com"]')
        monkeypatch.setenv("CORS_ALLOW_ORIGIN_REGEX", "")
        cors = CORSSettings()
        assert cors.allow_origins == ["https://dashboard.example.com"]
        assert cors.allow_origin_regex == ""


class TestCORSMiddlewareBehavior:
    """Tests that the middleware (configured as in main.py) rejects untrusted origins."""

    def test_default_allows_localhost_origins(self):
        client = _build_client(CORSSettings())
        for origin in ("http://localhost:3000", "http://127.0.0.1:8501"):
            response = client.get("/", headers={"Origin": origin})
            assert response.headers.get("access-control-allow-origin") == origin

    def test_default_rejects_untrusted_origin(self):
        client = _build_client(CORSSettings())
        response = client.get("/", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in response.headers

        preflight = client.options(
            "/",
            headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
        )
        assert preflight.status_code == 400
        assert "access-control-allow-origin" not in preflight.headers

    def test_explicit_origin_list_from_env(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", '["https://dashboard.example.com"]')
        monkeypatch.setenv("CORS_ALLOW_ORIGIN_REGEX", "")
        client = _build_client(CORSSettings())

        allowed = client.get("/", headers={"Origin": "https://dashboard.example.com"})
        assert allowed.headers.get("access-control-allow-origin") == "https://dashboard.example.com"
        assert allowed.headers.get("access-control-allow-credentials") == "true"

        rejected = client.get("/", headers={"Origin": "http://localhost:3000"})
        assert "access-control-allow-origin" not in rejected.headers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
