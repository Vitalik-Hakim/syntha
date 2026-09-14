from fastapi import FastAPI

from syntha_api import __version__
from syntha_api.routes import backtests, catalog, compare, health, strategies


def create_app() -> FastAPI:
    app = FastAPI(title="Syntha API", version=__version__)
    app.include_router(health.router, prefix="/api")
    app.include_router(catalog.router, prefix="/api")
    # catalog before strategies: /strategies/builtins and /validate must win over /strategies/{id}
    app.include_router(strategies.router, prefix="/api")
    app.include_router(backtests.router, prefix="/api")
    app.include_router(compare.router, prefix="/api")
    return app


app = create_app()
