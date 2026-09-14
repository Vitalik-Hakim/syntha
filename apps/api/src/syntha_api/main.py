from fastapi import FastAPI

from syntha_api import __version__
from syntha_api.routes import health


def create_app() -> FastAPI:
    app = FastAPI(title="Syntha API", version=__version__)
    app.include_router(health.router, prefix="/api")
    return app


app = create_app()
