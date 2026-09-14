import os

import pytest
from fastapi.testclient import TestClient

from syntha_api.main import create_app
from syntha_api.settings import Settings, get_settings


def _client(settings: Settings) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_liveness_needs_no_dependencies() -> None:
    res = _client(Settings()).get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "syntha-api"
    assert "engine_version" in body


def test_readiness_reports_unreachable_dependencies() -> None:
    unreachable = Settings(
        database_url="postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
        redis_url="redis://127.0.0.1:1/0",
        dependency_timeout_s=1,
    )
    res = _client(unreachable).get("/api/health/ready")
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "degraded"
    assert {d["name"]: d["ok"] for d in body["dependencies"]} == {
        "postgres": False,
        "redis": False,
    }


def test_readiness_against_real_dependencies() -> None:
    db = os.environ.get("SYNTHA_TEST_DATABASE_URL")
    rd = os.environ.get("SYNTHA_TEST_REDIS_URL")
    if not (db and rd):
        pytest.skip("SYNTHA_TEST_DATABASE_URL / SYNTHA_TEST_REDIS_URL not set")
    res = _client(Settings(database_url=db, redis_url=rd)).get("/api/health/ready")
    assert res.status_code == 200, res.json()
    assert res.json()["status"] == "ready"
