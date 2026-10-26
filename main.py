from contextlib import asynccontextmanager

from fastapi import FastAPI

from middleware.csrf import require_json
from routes import auth, health
from utils.config import Settings, get_settings
from utils.db import create_client, ensure_indexes


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = create_client(settings)
        app.state.db = client[settings.mongo_db]
        await ensure_indexes(app.state.db)
        yield
        await client.close()

    app = FastAPI(title="Lukis API", lifespan=lifespan)
    app.state.settings = settings
    app.middleware("http")(require_json)
    app.include_router(health.router)
    app.include_router(auth.router)
    return app


app = create_app()
