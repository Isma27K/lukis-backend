from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from function.errors import DomainError
from middleware.csrf import require_json
from routes import auth, drawings, health, invites, workspaces
from utils.config import Settings, get_settings
from utils.db import create_client, ensure_indexes


async def handle_domain_error(request: Request, error: DomainError) -> JSONResponse:
    return JSONResponse({"detail": error.detail}, status_code=error.status_code)


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
    # Covers every subclass: NotFoundError, ForbiddenError, ConflictError.
    app.add_exception_handler(DomainError, handle_domain_error)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(drawings.router)
    app.include_router(invites.router)
    return app


app = create_app()
