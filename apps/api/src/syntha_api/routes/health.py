from typing import Annotated

import redis
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text

from syntha_api import __version__
from syntha_api.settings import Settings, get_settings
from syntha_contracts import DependencyStatus, HealthResponse, ReadinessResponse
from syntha_engine import ENGINE_VERSION
from syntha_storage import create_db_engine

router = APIRouter(tags=["health"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/health")
def health() -> HealthResponse:
    return HealthResponse(service="syntha-api", version=__version__, engine_version=ENGINE_VERSION)


def _check_postgres(settings: Settings) -> DependencyStatus:
    engine = create_db_engine(
        settings.database_url, connect_timeout_s=settings.dependency_timeout_s
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return DependencyStatus(name="postgres", ok=True)
    except Exception as exc:
        return DependencyStatus(name="postgres", ok=False, detail=type(exc).__name__)
    finally:
        engine.dispose()


def _check_redis(settings: Settings) -> DependencyStatus:
    client = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.dependency_timeout_s,
        socket_timeout=settings.dependency_timeout_s,
    )
    try:
        client.ping()
        return DependencyStatus(name="redis", ok=True)
    except Exception as exc:
        return DependencyStatus(name="redis", ok=False, detail=type(exc).__name__)
    finally:
        client.close()


@router.get("/health/ready", responses={503: {"model": ReadinessResponse}})
def ready(settings: SettingsDep, response: Response) -> ReadinessResponse:
    deps = [_check_postgres(settings), _check_redis(settings)]
    all_ok = all(d.ok for d in deps)
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if all_ok else "degraded", dependencies=deps)
