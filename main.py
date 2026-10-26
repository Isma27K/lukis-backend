from contextlib import asynccontextmanager

from fastapi import FastAPI

from routes import health
from utils.config import Settings, get_settings
from utils.db import create_client


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = create_client(settings)
        app.state.db = client[settings.mongo_db]
        yield
        await client.close()

    app = FastAPI(title="Lukis API", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
