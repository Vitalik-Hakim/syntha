from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness: the process is up. Never touches dependencies."""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    engine_version: str


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Readiness: the process can reach Postgres and Redis."""

    status: Literal["ready", "degraded"]
    dependencies: list[DependencyStatus]
