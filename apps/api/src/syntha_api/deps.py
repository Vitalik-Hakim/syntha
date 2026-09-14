from collections.abc import Iterator
from typing import Annotated, Any

import psycopg
from fastapi import Depends

from syntha_api.settings import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_conn(settings: SettingsDep) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(
        settings.plain_database_url, connect_timeout=settings.dependency_timeout_s
    ) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        yield conn


ConnDep = Annotated[psycopg.Connection[Any], Depends(get_conn)]
