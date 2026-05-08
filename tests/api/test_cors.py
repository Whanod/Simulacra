"""CORS middleware tests (G1 in plan-api.md)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from defi_sim_api.main import CORS_ALLOWED_ORIGINS_ENV, create_app


class TestCorsDefaults:
    def test_module_app_allows_localhost_3000(self, client: TestClient) -> None:
        res = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_preflight_allows_localhost_3000(self, client: TestClient) -> None:
        res = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
        allowed_methods = res.headers.get("access-control-allow-methods", "")
        assert "GET" in allowed_methods or allowed_methods == "*"

    def test_unlisted_origin_is_rejected(self, client: TestClient) -> None:
        res = client.get("/health", headers={"Origin": "http://evil.example.com"})
        # Request still completes (CORS is enforced by browsers), but the
        # allow-origin header must NOT echo the disallowed origin.
        assert res.status_code == 200
        assert res.headers.get("access-control-allow-origin") != "http://evil.example.com"


class TestCorsEnvOverride:
    def test_env_var_extends_allowed_origins(self, monkeypatch) -> None:
        monkeypatch.setenv(
            CORS_ALLOWED_ORIGINS_ENV,
            "http://studio.example.com,http://other.example.com",
        )
        custom_app = create_app()
        with TestClient(custom_app) as c:
            res = c.get("/health", headers={"Origin": "http://studio.example.com"})
            assert res.status_code == 200
            assert (
                res.headers.get("access-control-allow-origin")
                == "http://studio.example.com"
            )

            res2 = c.get("/health", headers={"Origin": "http://other.example.com"})
            assert res2.status_code == 200
            assert (
                res2.headers.get("access-control-allow-origin")
                == "http://other.example.com"
            )

    def test_env_var_override_rejects_default_origin(self, monkeypatch) -> None:
        monkeypatch.setenv(CORS_ALLOWED_ORIGINS_ENV, "http://studio.example.com")
        custom_app = create_app()
        with TestClient(custom_app) as c:
            res = c.get("/health", headers={"Origin": "http://localhost:3000"})
            assert res.status_code == 200
            assert res.headers.get("access-control-allow-origin") != "http://localhost:3000"

    def test_empty_env_var_falls_back_to_no_origins(self, monkeypatch) -> None:
        monkeypatch.setenv(CORS_ALLOWED_ORIGINS_ENV, "")
        custom_app = create_app()
        with TestClient(custom_app) as c:
            res = c.get("/health", headers={"Origin": "http://localhost:3000"})
            assert res.status_code == 200
            assert res.headers.get("access-control-allow-origin") != "http://localhost:3000"
